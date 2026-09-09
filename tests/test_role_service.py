import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.core.exceptions import RoleNaoEncontradoError
from app.db.models import ConversationState, ConversationStep, Participant, ParticipantStatus, PixKeyType, Role
from app.services.interfaces import PixPayload
from app.services.role_service import PIX_MERCHANT_CITY, PIX_MERCHANT_NAME, RoleCriado, RoleService


class FakeRoleRepository:
    def __init__(self):
        self.roles: dict[uuid.UUID, Role] = {}
        self.committed = False

    def existe_codigo(self, role_code):
        return any(r.role_code == role_code for r in self.roles.values())

    def obter_por_id(self, role_id):
        return self.roles.get(role_id)

    def obter_por_codigo(self, role_code):
        return next((r for r in self.roles.values() if r.role_code == role_code), None)

    def obter_mais_recente_por_organizador(self, organizer_phone):
        candidatos = [r for r in self.roles.values() if r.organizer_phone == organizer_phone]
        return max(candidatos, key=lambda r: r.created_at) if candidatos else None

    def criar(self, role):
        if role.id is None:
            role.id = uuid.uuid4()
        if role.created_at is None:
            role.created_at = datetime.now(timezone.utc)
        self.roles[role.id] = role
        return role

    def apagar(self, role):
        self.roles.pop(role.id, None)

    def commit(self):
        self.committed = True


class FakeParticipantRepository:
    def __init__(self):
        self.participants: dict[tuple[uuid.UUID, str], Participant] = {}
        self.committed = False

    def obter_por_role_e_telefone(self, role_id, phone):
        return self.participants.get((role_id, phone))

    def salvar(self, participant):
        if participant.id is None:
            participant.id = uuid.uuid4()
        self.participants[(participant.role_id, participant.phone)] = participant
        return participant

    def listar_por_role(self, role_id):
        return [p for p in self.participants.values() if p.role_id == role_id]

    def commit(self):
        self.committed = True


class FakeConversationRepository:
    def __init__(self):
        self.states: dict[str, ConversationState] = {}

    def obter_por_telefone(self, phone):
        return self.states.get(phone)

    def salvar(self, state):
        self.states[state.phone] = state
        return state

    def apagar(self, state):
        self.states.pop(state.phone, None)

    def commit(self):
        pass


class FakePixGenerator:
    def __init__(self):
        self.chamadas = []

    def gerar_pix(self, pix_key, pix_key_type, amount, merchant_name, merchant_city, txid):
        self.chamadas.append(
            dict(
                pix_key=pix_key,
                pix_key_type=pix_key_type,
                amount=amount,
                merchant_name=merchant_name,
                merchant_city=merchant_city,
                txid=txid,
            )
        )
        return PixPayload(copia_e_cola=f"FAKE|{pix_key}|{amount}|{txid}", qr_code_image=b"fake-png")


@pytest.fixture()
def repos():
    return FakeRoleRepository(), FakeParticipantRepository(), FakeConversationRepository()


@pytest.fixture()
def pix_generator():
    return FakePixGenerator()


@pytest.fixture()
def service(repos, pix_generator):
    role_repo, participant_repo, conversation_repo = repos
    return RoleService(role_repo, participant_repo, conversation_repo, pix_generator)


def test_criar_role_calcula_valor_por_pessoa_com_round_half_up(service):
    resultado = service.criar_role(
        total_value=Decimal("100.00"),
        num_people=3,
        organizer_phone="5511999999999",
        pix_key="a@b.com",
        pix_key_type=PixKeyType.email,
    )
    assert resultado.role.value_per_person == Decimal("33.33")


def test_criar_role_usa_role_code_como_txid_do_pix(service, pix_generator):
    resultado = service.criar_role(
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


def test_criar_role_gera_outro_codigo_em_colisao(service, repos):
    role_repo, _, _ = repos
    with patch("app.services.role_service.random.choices", side_effect=[list("AAAA"), list("BBBB")]):
        role_repo.roles[uuid.uuid4()] = Role(role_code="AAAA", organizer_phone="x")
        resultado = service.criar_role(
            total_value=Decimal("10.00"),
            num_people=2,
            organizer_phone="5511999999999",
            pix_key="a@b.com",
            pix_key_type=PixKeyType.email,
        )
    assert resultado.role.role_code == "BBBB"


def test_criar_role_apaga_conversation_state_do_organizador(service, repos):
    _, _, conversation_repo = repos
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999", step=ConversationStep.aguardando_chave_pix
    )
    service.criar_role(
        total_value=Decimal("10.00"),
        num_people=2,
        organizer_phone="5511999999999",
        pix_key="a@b.com",
        pix_key_type=PixKeyType.email,
    )
    assert conversation_repo.obter_por_telefone("5511999999999") is None


def test_registrar_pagamento_valor_bate_confirma(service, repos):
    role_repo, _, _ = repos
    role = Role(id=uuid.uuid4(), organizer_phone="x", value_per_person=Decimal("50.00"), created_at=datetime.now(timezone.utc))
    role_repo.roles[role.id] = role

    participant = service.registrar_pagamento(role.id, "5511888888888", "http://img", Decimal("50.00"))

    assert participant.status == ParticipantStatus.confirmado
    assert participant.confirmed_at is not None


def test_registrar_pagamento_dentro_da_tolerancia_confirma(service, repos):
    role_repo, _, _ = repos
    role = Role(id=uuid.uuid4(), organizer_phone="x", value_per_person=Decimal("50.00"), created_at=datetime.now(timezone.utc))
    role_repo.roles[role.id] = role

    participant = service.registrar_pagamento(role.id, "5511888888888", "http://img", Decimal("50.01"))

    assert participant.status == ParticipantStatus.confirmado


def test_registrar_pagamento_valor_diferente_fica_pendente(service, repos):
    role_repo, _, _ = repos
    role = Role(id=uuid.uuid4(), organizer_phone="x", value_per_person=Decimal("50.00"), created_at=datetime.now(timezone.utc))
    role_repo.roles[role.id] = role

    participant = service.registrar_pagamento(role.id, "5511888888888", "http://img", Decimal("30.00"))

    assert participant.status == ParticipantStatus.comprovante_enviado
    assert participant.confirmed_at is None


def test_registrar_pagamento_sem_valor_extraido_fica_pendente(service, repos):
    role_repo, _, _ = repos
    role = Role(id=uuid.uuid4(), organizer_phone="x", value_per_person=Decimal("50.00"), created_at=datetime.now(timezone.utc))
    role_repo.roles[role.id] = role

    participant = service.registrar_pagamento(role.id, "5511888888888", "http://img", None)

    assert participant.status == ParticipantStatus.comprovante_enviado


def test_registrar_pagamento_reenvio_atualiza_participant_existente(service, repos):
    role_repo, participant_repo, _ = repos
    role = Role(id=uuid.uuid4(), organizer_phone="x", value_per_person=Decimal("50.00"), created_at=datetime.now(timezone.utc))
    role_repo.roles[role.id] = role

    primeiro = service.registrar_pagamento(role.id, "5511888888888", "http://img1", Decimal("50.00"))
    assert primeiro.status == ParticipantStatus.confirmado

    segundo = service.registrar_pagamento(role.id, "5511888888888", "http://img2", Decimal("10.00"))

    assert segundo.id == primeiro.id  # mesma linha, não duplicou
    assert segundo.status == ParticipantStatus.comprovante_enviado
    assert segundo.confirmed_at is None  # reenvio com valor errado reverte a confirmação
    assert len(participant_repo.participants) == 1


def test_registrar_pagamento_role_nao_encontrado_levanta_erro(service):
    with pytest.raises(RoleNaoEncontradoError):
        service.registrar_pagamento(uuid.uuid4(), "5511888888888", "http://img", Decimal("50.00"))


def test_confirmar_encerramento_apaga_role_e_conversation_state(service, repos):
    role_repo, _, conversation_repo = repos
    role = Role(id=uuid.uuid4(), organizer_phone="5511999999999", value_per_person=Decimal("50.00"), created_at=datetime.now(timezone.utc))
    role_repo.roles[role.id] = role
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999",
        step=ConversationStep.aguardando_confirmacao_encerramento,
        pending_role_id=role.id,
    )

    service.confirmar_encerramento(role.id)

    assert role_repo.obter_por_id(role.id) is None
    assert conversation_repo.obter_por_telefone("5511999999999") is None
    assert role_repo.committed


def test_confirmar_encerramento_role_nao_encontrado_levanta_erro(service):
    with pytest.raises(RoleNaoEncontradoError):
        service.confirmar_encerramento(uuid.uuid4())
