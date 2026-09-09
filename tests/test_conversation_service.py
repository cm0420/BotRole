import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.db.models import ConversationState, ConversationStep, Participant, ParticipantStatus, PixKeyType, Role
from app.services.conversation_service import ConversationService


@pytest.fixture()
def conversation_service(repos, role_service):
    role_repo, participant_repo, conversation_repo = repos
    return ConversationService(conversation_repo, role_repo, participant_repo, role_service)


def test_primeira_mensagem_inicia_estado_e_pergunta_valor(conversation_service, repos):
    _, _, conversation_repo = repos
    resposta = conversation_service.processar_mensagem("5511999999999", "oi")
    assert "valor" in resposta.texto.lower()
    estado = conversation_repo.obter_por_telefone("5511999999999")
    assert estado.step == ConversationStep.aguardando_valor


def test_valor_invalido_repergunta_mantendo_estado(conversation_service, repos):
    _, _, conversation_repo = repos
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999", step=ConversationStep.aguardando_valor
    )
    resposta = conversation_service.processar_mensagem("5511999999999", "não sei quanto")
    estado = conversation_repo.obter_por_telefone("5511999999999")
    assert estado.step == ConversationStep.aguardando_valor  # não avançou
    assert estado.pending_total_value is None
    assert "não entendi" in resposta.texto.lower()


def test_valor_valido_avanca_para_num_pessoas(conversation_service, repos):
    _, _, conversation_repo = repos
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999", step=ConversationStep.aguardando_valor
    )
    resposta = conversation_service.processar_mensagem("5511999999999", "150,50")
    estado = conversation_repo.obter_por_telefone("5511999999999")
    assert estado.step == ConversationStep.aguardando_num_pessoas
    assert estado.pending_total_value == Decimal("150.50")
    assert "pessoas" in resposta.texto.lower()


def test_num_pessoas_menor_que_2_repergunta(conversation_service, repos):
    _, _, conversation_repo = repos
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999", step=ConversationStep.aguardando_num_pessoas, pending_total_value=Decimal("100")
    )
    resposta = conversation_service.processar_mensagem("5511999999999", "1")
    estado = conversation_repo.obter_por_telefone("5511999999999")
    assert estado.step == ConversationStep.aguardando_num_pessoas
    assert "mínimo 2" in resposta.texto.lower() or "minimo 2" in resposta.texto.lower()


def test_num_pessoas_valido_avanca_para_tipo_chave_pix(conversation_service, repos):
    _, _, conversation_repo = repos
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999", step=ConversationStep.aguardando_num_pessoas, pending_total_value=Decimal("100")
    )
    resposta = conversation_service.processar_mensagem("5511999999999", "4")
    estado = conversation_repo.obter_por_telefone("5511999999999")
    assert estado.step == ConversationStep.aguardando_tipo_chave_pix
    assert estado.pending_num_people == 4
    assert "cpf" in resposta.texto.lower()


def test_tipo_chave_pix_por_numero_ou_palavra(conversation_service, repos):
    _, _, conversation_repo = repos
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999",
        step=ConversationStep.aguardando_tipo_chave_pix,
        pending_total_value=Decimal("100"),
        pending_num_people=4,
    )
    resposta = conversation_service.processar_mensagem("5511999999999", "3")
    estado = conversation_repo.obter_por_telefone("5511999999999")
    assert estado.step == ConversationStep.aguardando_chave_pix
    assert estado.pending_pix_key_type == PixKeyType.email
    assert "e-mail" in resposta.texto.lower()


def test_tipo_chave_pix_invalido_repergunta(conversation_service, repos):
    _, _, conversation_repo = repos
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999",
        step=ConversationStep.aguardando_tipo_chave_pix,
        pending_total_value=Decimal("100"),
        pending_num_people=4,
    )
    conversation_service.processar_mensagem("5511999999999", "banana")
    estado = conversation_repo.obter_por_telefone("5511999999999")
    assert estado.step == ConversationStep.aguardando_tipo_chave_pix


def test_chave_pix_valida_cria_role_e_apaga_estado(conversation_service, repos):
    _, _, conversation_repo = repos
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999",
        step=ConversationStep.aguardando_chave_pix,
        pending_total_value=Decimal("100"),
        pending_num_people=4,
        pending_pix_key_type=PixKeyType.email,
    )
    resposta = conversation_service.processar_mensagem("5511999999999", "organizador@example.com")

    assert conversation_repo.obter_por_telefone("5511999999999") is None
    assert "rolê criado" in resposta.texto.lower()
    assert resposta.imagem == b"fake-png"


def test_chave_pix_invalida_repergunta_sem_criar_role(conversation_service, repos):
    role_repo, _, conversation_repo = repos
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999",
        step=ConversationStep.aguardando_chave_pix,
        pending_total_value=Decimal("100"),
        pending_num_people=4,
        pending_pix_key_type=PixKeyType.cpf,
    )
    resposta = conversation_service.processar_mensagem("5511999999999", "123")

    assert conversation_repo.obter_por_telefone("5511999999999") is not None
    assert len(role_repo.roles) == 0
    assert "não parece válida" in resposta.texto.lower()


def test_consultar_status_sem_role_ativo(conversation_service):
    resposta = conversation_service.consultar_status("5511999999999")
    assert "não tem nenhum rolê" in resposta.texto.lower()


def test_consultar_status_com_role_mostra_placar(conversation_service, repos):
    role_repo, participant_repo, _ = repos
    role = Role(
        id=uuid.uuid4(),
        organizer_phone="5511999999999",
        role_code="AB23",
        value_per_person=Decimal("50.00"),
        created_at=datetime.now(timezone.utc),
    )
    role_repo.roles[role.id] = role
    participant_repo.participants[(role.id, "111")] = Participant(
        id=uuid.uuid4(), role_id=role.id, phone="111", status=ParticipantStatus.confirmado
    )
    participant_repo.participants[(role.id, "222")] = Participant(
        id=uuid.uuid4(), role_id=role.id, phone="222", status=ParticipantStatus.pendente
    )

    resposta = conversation_service.consultar_status("5511999999999")

    assert "1/2" in resposta.texto
    assert "222" in resposta.texto


def test_iniciar_encerramento_sem_role_ativo(conversation_service):
    resposta = conversation_service.iniciar_encerramento("5511999999999")
    assert "não tem nenhum rolê ativo pra encerrar" in resposta.texto.lower()


def test_iniciar_encerramento_com_role_pede_confirmacao(conversation_service, repos):
    role_repo, _, conversation_repo = repos
    role = Role(
        id=uuid.uuid4(),
        organizer_phone="5511999999999",
        role_code="AB23",
        value_per_person=Decimal("50.00"),
        created_at=datetime.now(timezone.utc),
    )
    role_repo.roles[role.id] = role

    resposta = conversation_service.iniciar_encerramento("5511999999999")

    estado = conversation_repo.obter_por_telefone("5511999999999")
    assert estado.step == ConversationStep.aguardando_confirmacao_encerramento
    assert estado.pending_role_id == role.id
    assert "encerrar" in resposta.texto.lower()


def test_confirmacao_encerramento_com_encerrar_apaga_role(conversation_service, repos):
    role_repo, _, conversation_repo = repos
    role = Role(
        id=uuid.uuid4(),
        organizer_phone="5511999999999",
        role_code="AB23",
        value_per_person=Decimal("50.00"),
        created_at=datetime.now(timezone.utc),
    )
    role_repo.roles[role.id] = role
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999",
        step=ConversationStep.aguardando_confirmacao_encerramento,
        pending_role_id=role.id,
    )

    resposta = conversation_service.processar_mensagem("5511999999999", "encerrar")

    assert role_repo.obter_por_id(role.id) is None
    assert conversation_repo.obter_por_telefone("5511999999999") is None
    assert "encerrado" in resposta.texto.lower()


def test_confirmacao_encerramento_com_outra_mensagem_cancela_sem_apagar(conversation_service, repos):
    role_repo, _, conversation_repo = repos
    role = Role(
        id=uuid.uuid4(),
        organizer_phone="5511999999999",
        role_code="AB23",
        value_per_person=Decimal("50.00"),
        created_at=datetime.now(timezone.utc),
    )
    role_repo.roles[role.id] = role
    conversation_repo.states["5511999999999"] = ConversationState(
        phone="5511999999999",
        step=ConversationStep.aguardando_confirmacao_encerramento,
        pending_role_id=role.id,
    )

    resposta = conversation_service.processar_mensagem("5511999999999", "não, peraí")

    assert role_repo.obter_por_id(role.id) is not None  # rolê continua existindo
    assert conversation_repo.obter_por_telefone("5511999999999") is None  # só o estado some
    assert "cancelado" in resposta.texto.lower()
