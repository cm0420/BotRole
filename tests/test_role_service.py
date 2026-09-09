import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.core.exceptions import RoleNaoEncontradoError
from app.db.models import ConversationState, ConversationStep, ParticipantStatus, PixKeyType, Role
from app.services.role_service import PIX_MERCHANT_CITY, PIX_MERCHANT_NAME, RoleCriado


def test_criar_role_calcula_valor_por_pessoa_com_round_half_up(role_service):
    resultado = role_service.criar_role(
        total_value=Decimal("100.00"),
        num_people=3,
        organizer_phone="5511999999999",
        pix_key="a@b.com",
        pix_key_type=PixKeyType.email,
    )
    assert resultado.role.value_per_person == Decimal("33.33")


def test_criar_role_usa_role_code_como_txid_do_pix(role_service, pix_generator):
    resultado = role_service.criar_role(
        total_value=Decimal("100.00"),
        num_people=2,
        organizer_phone="5511999999999",
        pix_key="a@b.com",
        pix_key_type=PixKeyType.email,
    )
    assert pix_generator.chamadas[0]["txid"] == resultado.role.role_code
    assert pix_generator.chamadas[0]["merchant_name"] == PIX_MERCHANT_NAME
    assert pix_generator.chamadas[0]["merchant_city"] == PIX_MERCHANT_CITY
    assert isinstance(resultado, RoleCriado)
    assert resultado.pix_payload.qr_code_image == b"fake-png"


def test_criar_role_gera_outro_codigo_em_colisao(role_service, repos):
    role_repo, _, _ = repos
    with patch("app.services.role_service.random.choices", side_effect=[list("AAAA"), list("BBBB")]):
        role_repo.roles[uuid.uuid4()] = Role(role_code="AAAA", organizer_phone="x")
        resultado = role_service.criar_role(
            total_value=Decimal("10.00"),
            num_people=2,
            organizer_phone="5511999999999",
            pix_key="a@b.com",
            pix_key_type=PixKeyType.email,
        )
    assert resultado.role.role_code == "BBBB"


def test_criar_role_apaga_conversation_state_do_organizador(role_service, repos):
    _, _, conversation_repo = repos
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999", step=ConversationStep.aguardando_chave_pix
    )
    role_service.criar_role(
        total_value=Decimal("10.00"),
        num_people=2,
        organizer_phone="5511999999999",
        pix_key="a@b.com",
        pix_key_type=PixKeyType.email,
    )
    assert conversation_repo.obter_por_telefone("5511999999999") is None


def test_registrar_pagamento_valor_bate_confirma(role_service, repos):
    role_repo, _, _ = repos
    role = Role(
        id=uuid.uuid4(), organizer_phone="x", value_per_person=Decimal("50.00"), created_at=datetime.now(timezone.utc)
    )
    role_repo.roles[role.id] = role

    participant = role_service.registrar_pagamento(role.id, "5511888888888", "http://img", Decimal("50.00"))

    assert participant.status == ParticipantStatus.confirmado
    assert participant.confirmed_at is not None


def test_registrar_pagamento_dentro_da_tolerancia_confirma(role_service, repos):
    role_repo, _, _ = repos
    role = Role(
        id=uuid.uuid4(), organizer_phone="x", value_per_person=Decimal("50.00"), created_at=datetime.now(timezone.utc)
    )
    role_repo.roles[role.id] = role

    participant = role_service.registrar_pagamento(role.id, "5511888888888", "http://img", Decimal("50.01"))

    assert participant.status == ParticipantStatus.confirmado


def test_registrar_pagamento_valor_diferente_fica_pendente(role_service, repos):
    role_repo, _, _ = repos
    role = Role(
        id=uuid.uuid4(), organizer_phone="x", value_per_person=Decimal("50.00"), created_at=datetime.now(timezone.utc)
    )
    role_repo.roles[role.id] = role

    participant = role_service.registrar_pagamento(role.id, "5511888888888", "http://img", Decimal("30.00"))

    assert participant.status == ParticipantStatus.comprovante_enviado
    assert participant.confirmed_at is None


def test_registrar_pagamento_sem_valor_extraido_fica_pendente(role_service, repos):
    role_repo, _, _ = repos
    role = Role(
        id=uuid.uuid4(), organizer_phone="x", value_per_person=Decimal("50.00"), created_at=datetime.now(timezone.utc)
    )
    role_repo.roles[role.id] = role

    participant = role_service.registrar_pagamento(role.id, "5511888888888", "http://img", None)

    assert participant.status == ParticipantStatus.comprovante_enviado


def test_registrar_pagamento_reenvio_atualiza_participant_existente(role_service, repos):
    role_repo, participant_repo, _ = repos
    role = Role(
        id=uuid.uuid4(), organizer_phone="x", value_per_person=Decimal("50.00"), created_at=datetime.now(timezone.utc)
    )
    role_repo.roles[role.id] = role

    primeiro = role_service.registrar_pagamento(role.id, "5511888888888", "http://img1", Decimal("50.00"))
    assert primeiro.status == ParticipantStatus.confirmado

    segundo = role_service.registrar_pagamento(role.id, "5511888888888", "http://img2", Decimal("10.00"))

    assert segundo.id == primeiro.id  # mesma linha, não duplicou
    assert segundo.status == ParticipantStatus.comprovante_enviado
    assert segundo.confirmed_at is None  # reenvio com valor errado reverte a confirmação
    assert len(participant_repo.participants) == 1


def test_registrar_pagamento_role_nao_encontrado_levanta_erro(role_service):
    with pytest.raises(RoleNaoEncontradoError):
        role_service.registrar_pagamento(uuid.uuid4(), "5511888888888", "http://img", Decimal("50.00"))


def test_confirmar_encerramento_apaga_role_e_conversation_state(role_service, repos):
    role_repo, _, conversation_repo = repos
    role = Role(
        id=uuid.uuid4(),
        organizer_phone="5511999999999",
        value_per_person=Decimal("50.00"),
        created_at=datetime.now(timezone.utc),
    )
    role_repo.roles[role.id] = role
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999",
        step=ConversationStep.aguardando_confirmacao_encerramento,
        pending_role_id=role.id,
    )

    role_service.confirmar_encerramento(role.id)

    assert role_repo.obter_por_id(role.id) is None
    assert conversation_repo.obter_por_telefone("5511999999999") is None
    assert role_repo.committed


def test_confirmar_encerramento_role_nao_encontrado_levanta_erro(role_service):
    with pytest.raises(RoleNaoEncontradoError):
        role_service.confirmar_encerramento(uuid.uuid4())
