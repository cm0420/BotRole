from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api import webhook as webhook_module
from app.db.models import ConversationState, ConversationStep, ParticipantStatus, Role
from app.services.conversation_service import ConversationService
from app.services.interfaces import OcrResult
from app.services.role_service import RoleService


class FakeWhatsAppClient:
    def __init__(self):
        self.textos_enviados = []
        self.imagens_enviadas = []
        self.midias = {}

    async def enviar_texto(self, phone, texto):
        self.textos_enviados.append((phone, texto))

    async def enviar_imagem(self, phone, imagem, legenda=None):
        self.imagens_enviadas.append((phone, imagem, legenda))

    async def baixar_midia(self, media_id):
        return self.midias.get(media_id, b"fake-image-bytes")


class FakeOcrExtractor:
    def __init__(self):
        self.resultado = OcrResult(extracted_value=None, extracted_date=None)

    def extrair(self, image_bytes):
        return self.resultado


def _payload_texto(phone: str, texto: str) -> dict:
    return {
        "entry": [{"changes": [{"value": {"messages": [{"from": phone, "type": "text", "text": {"body": texto}}]}}]}]
    }


def _payload_imagem(phone: str, media_id: str, caption: str | None) -> dict:
    image = {"id": media_id}
    if caption is not None:
        image["caption"] = caption
    return {"entry": [{"changes": [{"value": {"messages": [{"from": phone, "type": "image", "image": image}]}}]}]}


def _payload_status_de_entrega() -> dict:
    return {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.1", "status": "delivered"}]}}]}]}


@pytest.fixture()
def ambiente(repos, pix_generator):
    role_repo, participant_repo, conversation_repo = repos
    role_service = RoleService(role_repo, participant_repo, conversation_repo, pix_generator)
    conversation_service = ConversationService(conversation_repo, role_repo, participant_repo, role_service)
    whatsapp_client = FakeWhatsAppClient()
    ocr_extractor = FakeOcrExtractor()

    overrides = {
        webhook_module.get_role_repository: lambda: role_repo,
        webhook_module.get_participant_repository: lambda: participant_repo,
        webhook_module.get_conversation_repository: lambda: conversation_repo,
        webhook_module.get_role_service: lambda: role_service,
        webhook_module.get_conversation_service: lambda: conversation_service,
        webhook_module.get_ocr_extractor: lambda: ocr_extractor,
        webhook_module.get_whatsapp_client: lambda: whatsapp_client,
    }
    webhook_module.app.dependency_overrides.update(overrides)

    yield {
        "role_repo": role_repo,
        "participant_repo": participant_repo,
        "conversation_repo": conversation_repo,
        "whatsapp_client": whatsapp_client,
        "ocr_extractor": ocr_extractor,
    }

    webhook_module.app.dependency_overrides.clear()


@pytest.fixture()
def client(ambiente):
    return TestClient(webhook_module.app)


def test_get_webhook_verifica_token_correto(client):
    response = client.get(
        "/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "12345"}
    )
    assert response.status_code == 200
    assert response.text == "12345"


def test_get_webhook_rejeita_token_errado(client):
    response = client.get(
        "/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "12345"}
    )
    assert response.status_code == 403


def test_post_status_de_entrega_e_ignorado(client, ambiente):
    response = client.post("/webhook", json=_payload_status_de_entrega())
    assert response.status_code == 200
    assert ambiente["whatsapp_client"].textos_enviados == []


def test_post_primeira_mensagem_inicia_criacao(client, ambiente):
    response = client.post("/webhook", json=_payload_texto("5511999999999", "oi"))
    assert response.status_code == 200
    estado = ambiente["conversation_repo"].obter_por_telefone("5511999999999")
    assert estado.step == ConversationStep.aguardando_valor
    assert "valor" in ambiente["whatsapp_client"].textos_enviados[0][1].lower()


def test_post_mensagem_com_estado_pendente_avanca_passo(client, ambiente):
    ambiente["conversation_repo"].states["5511999999999"] = ConversationState(
        phone="5511999999999", step=ConversationStep.aguardando_valor
    )
    client.post("/webhook", json=_payload_texto("5511999999999", "150,00"))
    estado = ambiente["conversation_repo"].obter_por_telefone("5511999999999")
    assert estado.step == ConversationStep.aguardando_num_pessoas
    assert estado.pending_total_value == Decimal("150.00")


def test_post_texto_status_consulta_placar(client, ambiente):
    response = client.post("/webhook", json=_payload_texto("5511999999999", "quem pagou"))
    assert response.status_code == 200
    assert "não tem nenhum rolê" in ambiente["whatsapp_client"].textos_enviados[0][1].lower()


def test_post_texto_encerrar_sem_role_ativo(client, ambiente):
    client.post("/webhook", json=_payload_texto("5511999999999", "encerrar"))
    assert "não tem nenhum rolê ativo" in ambiente["whatsapp_client"].textos_enviados[0][1].lower()
    assert ambiente["conversation_repo"].obter_por_telefone("5511999999999") is None


def test_post_texto_encerrar_com_role_ativo_pede_confirmacao(client, ambiente):
    role_repo = ambiente["role_repo"]
    role = role_repo.criar(
        Role(organizer_phone="5511999999999", role_code="AB23", value_per_person=Decimal("50.00"))
    )

    client.post("/webhook", json=_payload_texto("5511999999999", "encerrar"))

    estado = ambiente["conversation_repo"].obter_por_telefone("5511999999999")
    assert estado.step == ConversationStep.aguardando_confirmacao_encerramento
    assert estado.pending_role_id == role.id


def test_post_imagem_com_codigo_valido_registra_pagamento(client, ambiente):
    role_repo = ambiente["role_repo"]
    role = role_repo.criar(Role(role_code="AB23", value_per_person=Decimal("50.00"), organizer_phone="x"))
    ambiente["ocr_extractor"].resultado = OcrResult(extracted_value=Decimal("50.00"), extracted_date=None)

    client.post("/webhook", json=_payload_imagem("5511888888888", "media-1", "aqui AB23 o comprovante"))

    participante = ambiente["participant_repo"].obter_por_role_e_telefone(role.id, "5511888888888")
    assert participante is not None
    assert participante.status == ParticipantStatus.confirmado
    assert "conferido por ocr" in ambiente["whatsapp_client"].textos_enviados[0][1].lower()


def test_post_imagem_sem_codigo_pede_reenvio(client, ambiente):
    client.post("/webhook", json=_payload_imagem("5511888888888", "media-1", "sem codigo nenhum aqui"))
    assert "código do rolê na legenda" in ambiente["whatsapp_client"].textos_enviados[0][1].lower()
    assert len(ambiente["participant_repo"].participants) == 0
