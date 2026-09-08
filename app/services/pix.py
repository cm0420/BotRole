import io
import re
from decimal import Decimal

import qrcode

from app.db.models import PixKeyType
from app.services.interfaces import PixPayload

_GUI_PIX = "br.gov.bcb.pix"
_TXID_SEM_REFERENCIA = "***"


def _tlv(field_id: str, value: str) -> str:
    # Tamanho em bytes UTF-8, não em caracteres — um valor com acento/caractere
    # não-ASCII (ex: chave de e-mail) desalinharia a leitura por offset dos
    # campos seguintes se contássemos caracteres.
    length = len(value.encode("utf-8"))
    return f"{field_id}{length:02d}{value}"


def _crc16_ccitt(payload: str) -> str:
    polynomial = 0x1021
    result = 0xFFFF
    for byte in payload.encode("utf-8"):
        result ^= byte << 8
        for _ in range(8):
            if result & 0x8000:
                result = ((result << 1) ^ polynomial) & 0xFFFF
            else:
                result = (result << 1) & 0xFFFF
    return f"{result:04X}"


def _sanitizar_texto(value: str, max_length: int, upper: bool = False) -> str:
    # Campos do BR Code aceitam só ASCII — remove acento e trunca no limite do padrão.
    ascii_value = value.strip().encode("ascii", "ignore").decode("ascii")
    if upper:
        ascii_value = ascii_value.upper()
    if not ascii_value:
        raise ValueError("valor não pode ficar vazio após normalização para ASCII")
    return ascii_value[:max_length]


def _sanitizar_txid(txid: str) -> str:
    alnum = "".join(ch for ch in txid if ch.isalnum())
    return alnum[:25] if alnum else _TXID_SEM_REFERENCIA


def _normalizar_chave(pix_key: str, pix_key_type: PixKeyType) -> str:
    if pix_key_type == PixKeyType.cpf:
        digits = re.sub(r"\D", "", pix_key)
        if len(digits) != 11:
            raise ValueError("chave do tipo cpf precisa ter 11 dígitos")
        return digits
    if pix_key_type == PixKeyType.cnpj:
        digits = re.sub(r"\D", "", pix_key)
        if len(digits) != 14:
            raise ValueError("chave do tipo cnpj precisa ter 14 dígitos")
        return digits
    if pix_key_type == PixKeyType.telefone:
        digits = re.sub(r"\D", "", pix_key)
        if digits.startswith("55") and len(digits) in (12, 13):
            return f"+{digits}"
        if len(digits) in (10, 11):
            return f"+55{digits}"
        raise ValueError("chave do tipo telefone em formato inválido")
    # email e aleatoria: usadas como vêm, só sem espaços nas pontas.
    return pix_key.strip()


def gerar_payload_copia_e_cola(
    pix_key: str,
    pix_key_type: PixKeyType,
    amount: Decimal,
    merchant_name: str,
    merchant_city: str,
    txid: str,
) -> str:
    """Monta o payload BR Code (EMV/Pix) estático, com valor fixo em reais."""
    chave = _normalizar_chave(pix_key, pix_key_type)
    merchant_account_info = _tlv("00", _GUI_PIX) + _tlv("01", chave)
    additional_data = _tlv("05", _sanitizar_txid(txid))

    # Sem o campo "01" (Point of Initiation Method): não faz parte do conjunto
    # de campos do QR Code Pix por este manual (seção 2.6, tabela "QR Code Estático").
    payload_sem_crc = (
        _tlv("00", "01")
        + _tlv("26", merchant_account_info)
        + _tlv("52", "0000")
        + _tlv("53", "986")
        + _tlv("54", f"{amount:.2f}")
        + _tlv("58", "BR")
        + _tlv("59", _sanitizar_texto(merchant_name, 25))
        + _tlv("60", _sanitizar_texto(merchant_city, 15, upper=True))
        + _tlv("62", additional_data)
        + "6304"
    )
    return payload_sem_crc + _crc16_ccitt(payload_sem_crc)


def gerar_qr_code_image(payload: str) -> bytes:
    """Gera a imagem PNG do QR Code a partir do payload BR Code."""
    img = qrcode.make(payload)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


class BrCodePixGenerator:
    """Implementação concreta de PixGenerator via payload BR Code (Pix estático)."""

    def gerar_pix(
        self,
        pix_key: str,
        pix_key_type: PixKeyType,
        amount: Decimal,
        merchant_name: str,
        merchant_city: str,
        txid: str,
    ) -> PixPayload:
        """Gera o payload BR Code e a imagem do QR Code pro Pix de um rolê."""
        payload = gerar_payload_copia_e_cola(
            pix_key, pix_key_type, amount, merchant_name, merchant_city, txid
        )
        return PixPayload(copia_e_cola=payload, qr_code_image=gerar_qr_code_image(payload))
