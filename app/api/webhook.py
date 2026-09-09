import logging
import re

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import DominioError
from app.core.logging import configurar_logging
from app.db.base import SessionLocal
from app.db.models import ParticipantStatus
from app.repositories.conversation_repository import ConversationRepository, SqlAlchemyConversationRepository
from app.repositories.participant_repository import ParticipantRepository, SqlAlchemyParticipantRepository
from app.repositories.role_repository import RoleRepository, SqlAlchemyRoleRepository
from app.services.conversation_service import ConversationService, RespostaBot
from app.services.interfaces import OcrExtractor, PixGenerator, WhatsAppClient
from app.services.ocr import TesseractOcrExtractor
from app.services.pix import BrCodePixGenerator
from app.services.role_service import RoleService
from app.services.whatsapp_client import WhatsAppCloudApiClient

configurar_logging()
logger = logging.getLogger(__name__)

app = FastAPI()

# Formato do código do rolê: seção 6.2 do CLAUDE.md (alfabeto já exclui I/O/0/1).
_ROLE_CODE_RE = re.compile(r"\b([23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4})\b")
_PALAVRAS_STATUS = ("pago", "status", "quem pagou")

_MENSAGEM_CODIGO_INVALIDO = "Manda a foto de novo com o código do rolê na legenda, tipo: AB23"


# --- Dependências (injeção via FastAPI Depends, seção 9) ---------------------


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_role_repository(db: Session = Depends(get_db)) -> RoleRepository:
    return SqlAlchemyRoleRepository(db)


def get_participant_repository(db: Session = Depends(get_db)) -> ParticipantRepository:
    return SqlAlchemyParticipantRepository(db)


def get_conversation_repository(db: Session = Depends(get_db)) -> ConversationRepository:
    return SqlAlchemyConversationRepository(db)


def get_pix_generator() -> PixGenerator:
    return BrCodePixGenerator()


def get_ocr_extractor() -> OcrExtractor:
    return TesseractOcrExtractor()


def get_whatsapp_client() -> WhatsAppClient:
    return WhatsAppCloudApiClient()


def get_role_service(
    role_repository: RoleRepository = Depends(get_role_repository),
    participant_repository: ParticipantRepository = Depends(get_participant_repository),
    conversation_repository: ConversationRepository = Depends(get_conversation_repository),
    pix_generator: PixGenerator = Depends(get_pix_generator),
) -> RoleService:
    return RoleService(role_repository, participant_repository, conversation_repository, pix_generator)


def get_conversation_service(
    conversation_repository: ConversationRepository = Depends(get_conversation_repository),
    role_repository: RoleRepository = Depends(get_role_repository),
    participant_repository: ParticipantRepository = Depends(get_participant_repository),
    role_service: RoleService = Depends(get_role_service),
) -> ConversationService:
    return ConversationService(conversation_repository, role_repository, participant_repository, role_service)


# --- Exception handler central (seção 8) --------------------------------------


@app.exception_handler(Exception)
async def tratar_erro_inesperado(request: Request, exc: Exception) -> JSONResponse:
    # A Cloud API só precisa de um 200 pra não reenviar o webhook; o usuário
    # já foi (ou não) avisado dentro do próprio handler antes do erro subir
    # até aqui. Isso é só a rede de segurança final.
    logger.exception("erro não tratado processando webhook")
    return JSONResponse(status_code=200, content={"status": "error"})


# --- Verificação do webhook (handshake exigido pela WhatsApp Cloud API) ------


@app.get("/webhook")
def verificar_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
) -> Response:
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(status_code=403)


# --- Parsing do payload da Cloud API ------------------------------------------


def _extrair_mensagem(payload: dict) -> dict | None:
    """Extrai a primeira mensagem de usuário do payload, ou None (ex: recibo de entrega, não é mensagem)."""
    try:
        return payload["entry"][0]["changes"][0]["value"]["messages"][0]
    except (KeyError, IndexError, TypeError):
        return None


def _extrair_codigo_role(legenda: str | None) -> str | None:
    if not legenda:
        return None
    match = _ROLE_CODE_RE.search(legenda.upper())
    return match.group(1) if match else None


# --- Roteamento (seção 6.1) ---------------------------------------------------


@app.post("/webhook")
async def receber_webhook(
    request: Request,
    conversation_repository: ConversationRepository = Depends(get_conversation_repository),
    role_repository: RoleRepository = Depends(get_role_repository),
    role_service: RoleService = Depends(get_role_service),
    conversation_service: ConversationService = Depends(get_conversation_service),
    ocr_extractor: OcrExtractor = Depends(get_ocr_extractor),
    whatsapp_client: WhatsAppClient = Depends(get_whatsapp_client),
) -> dict:
    payload = await request.json()
    mensagem = _extrair_mensagem(payload)
    if mensagem is None:
        return {"status": "ignored"}

    phone = mensagem["from"]

    try:
        if mensagem.get("type") == "image":
            await _tratar_imagem(mensagem, phone, role_repository, role_service, ocr_extractor, whatsapp_client)
        else:
            texto = mensagem.get("text", {}).get("body", "")
            resposta = _rotear_texto(texto, phone, conversation_repository, conversation_service)
            await _enviar_resposta(whatsapp_client, phone, resposta)
    except DominioError:
        # ex: role_id apontava pra um rolê já encerrado por outra mensagem
        # concorrente — não fingir que confirmou nada (seção 2).
        await whatsapp_client.enviar_texto(phone, "Ops, não encontrei esse rolê. Ele pode já ter sido encerrado.")

    return {"status": "ok"}


async def _tratar_imagem(
    mensagem: dict,
    phone: str,
    role_repository: RoleRepository,
    role_service: RoleService,
    ocr_extractor: OcrExtractor,
    whatsapp_client: WhatsAppClient,
) -> None:
    """Casos 1 e 2 da seção 6.1: comprovante com/sem código de rolê válido na legenda."""
    legenda = mensagem.get("image", {}).get("caption")
    role_code = _extrair_codigo_role(legenda)
    role = role_repository.obter_por_codigo(role_code) if role_code else None
    if role is None:
        await whatsapp_client.enviar_texto(phone, _MENSAGEM_CODIGO_INVALIDO)
        return

    media_id = mensagem["image"]["id"]
    imagem_bytes = await whatsapp_client.baixar_midia(media_id)
    resultado_ocr = ocr_extractor.extrair(imagem_bytes)

    participant = role_service.registrar_pagamento(
        role_id=role.id,
        phone=phone,
        receipt_image_url=media_id,
        extracted_value=resultado_ocr.extracted_value,
    )

    if participant.status == ParticipantStatus.confirmado:
        texto_resposta = (
            "Recebi seu comprovante e o valor bateu — conferido por OCR "
            "(não é uma confirmação bancária)."
        )
    else:
        texto_resposta = (
            "Recebi seu comprovante, mas não consegui confirmar o valor automaticamente — "
            "o organizador vai conferir manualmente."
        )
    await whatsapp_client.enviar_texto(phone, texto_resposta)


def _rotear_texto(
    texto: str,
    phone: str,
    conversation_repository: ConversationRepository,
    conversation_service: ConversationService,
) -> RespostaBot:
    """Casos 3, 4, 5, 6 e 7 da seção 6.1, nessa ordem de prioridade."""
    estado_pendente = conversation_repository.obter_por_telefone(phone)
    if estado_pendente is not None:
        # processar_mensagem já trata tanto a confirmação de encerramento
        # (caso 3) quanto os outros passos pendentes (caso 4).
        return conversation_service.processar_mensagem(phone, texto)

    texto_lower = texto.strip().lower()
    if any(palavra in texto_lower for palavra in _PALAVRAS_STATUS):
        return conversation_service.consultar_status(phone)
    if "encerrar" in texto_lower:
        return conversation_service.iniciar_encerramento(phone)
    return conversation_service.processar_mensagem(phone, texto)  # caso 7: começa um rolê novo


async def _enviar_resposta(whatsapp_client: WhatsAppClient, phone: str, resposta: RespostaBot) -> None:
    if resposta.imagem is not None:
        await whatsapp_client.enviar_imagem(phone, resposta.imagem)
    await whatsapp_client.enviar_texto(phone, resposta.texto)
