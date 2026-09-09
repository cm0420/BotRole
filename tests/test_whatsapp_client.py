import json

import httpx
import pytest

from app.services.whatsapp_client import WhatsAppCloudApiClient


def _client_com_transporte(handler):
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://graph.facebook.com/v21.0", transport=transport, headers={"Authorization": "Bearer x"}
    )
    return WhatsAppCloudApiClient(http_client=http_client)


@pytest.mark.asyncio
async def test_enviar_texto_faz_post_correto():
    requisicoes = []

    def handler(request: httpx.Request) -> httpx.Response:
        requisicoes.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    client = _client_com_transporte(handler)
    await client.enviar_texto("5511999999999", "oi")

    assert len(requisicoes) == 1
    req = requisicoes[0]
    assert req.url.path.endswith("/messages")
    corpo = json.loads(req.content)
    assert corpo["to"] == "5511999999999"
    assert corpo["type"] == "text"
    assert corpo["text"]["body"] == "oi"


@pytest.mark.asyncio
async def test_enviar_imagem_faz_upload_e_depois_envia_com_media_id():
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "media-123"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    client = _client_com_transporte(handler)
    await client.enviar_imagem("5511999999999", b"fake-png-bytes", legenda="olha o QR")

    assert len(chamadas) == 2
    assert chamadas[0].url.path.endswith("/media")
    corpo_envio = json.loads(chamadas[1].content)
    assert corpo_envio["type"] == "image"
    assert corpo_envio["image"]["id"] == "media-123"
    assert corpo_envio["image"]["caption"] == "olha o QR"


@pytest.mark.asyncio
async def test_baixar_midia_resolve_url_e_baixa_bytes():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/media-abc"):
            return httpx.Response(200, json={"url": "https://cdn.example.com/arquivo.jpg"})
        if str(request.url) == "https://cdn.example.com/arquivo.jpg":
            return httpx.Response(200, content=b"bytes-da-imagem")
        return httpx.Response(404)

    client = _client_com_transporte(handler)
    conteudo = await client.baixar_midia("media-abc")

    assert conteudo == b"bytes-da-imagem"
