import httpx

from app.core.config import settings

_GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


class WhatsAppCloudApiClient:
    """Implementação concreta de WhatsAppClient via WhatsApp Cloud API (Meta)."""

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._http = http_client or httpx.AsyncClient(
            base_url=_GRAPH_API_BASE,
            headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
        )

    async def enviar_texto(self, phone: str, texto: str) -> None:
        """Envia uma mensagem de texto simples pro telefone (1:1, dentro da janela de 24h)."""
        response = await self._http.post(
            f"/{settings.whatsapp_phone_number_id}/messages",
            json={"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": texto}},
        )
        response.raise_for_status()

    async def enviar_imagem(self, phone: str, imagem: bytes, legenda: str | None = None) -> None:
        """Sobe a imagem pra Cloud API e manda como mensagem de imagem, com legenda opcional."""
        media_id = await self._upload_midia(imagem)
        payload = {"messaging_product": "whatsapp", "to": phone, "type": "image", "image": {"id": media_id}}
        if legenda:
            payload["image"]["caption"] = legenda
        response = await self._http.post(f"/{settings.whatsapp_phone_number_id}/messages", json=payload)
        response.raise_for_status()

    async def _upload_midia(self, imagem: bytes) -> str:
        response = await self._http.post(
            f"/{settings.whatsapp_phone_number_id}/media",
            data={"messaging_product": "whatsapp", "type": "image/png"},
            files={"file": ("qrcode.png", imagem, "image/png")},
        )
        response.raise_for_status()
        return response.json()["id"]

    async def baixar_midia(self, media_id: str) -> bytes:
        """Resolve a URL temporária da mídia recebida e baixa os bytes (comprovante)."""
        info = await self._http.get(f"/{media_id}")
        info.raise_for_status()
        media_url = info.json()["url"]
        conteudo = await self._http.get(media_url)
        conteudo.raise_for_status()
        return conteudo.content
