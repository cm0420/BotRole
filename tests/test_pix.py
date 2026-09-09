import io
from decimal import Decimal

import pytest
from PIL import Image

from app.db.models import PixKeyType
from app.services.interfaces import PixPayload
from app.services.pix import (
    BrCodePixGenerator,
    _crc16_ccitt,
    normalizar_chave,
    _sanitizar_txid,
    _tlv,
    gerar_payload_copia_e_cola,
    gerar_qr_code_image,
)


def _parse_tlv(payload: str) -> dict[str, str]:
    # Opera em bytes UTF-8, não em caracteres: o tamanho de cada campo no
    # BR Code é em bytes (ver _tlv em app/services/pix.py).
    data = payload.encode("utf-8")
    fields: dict[str, str] = {}
    i = 0
    while i < len(data):
        field_id = data[i : i + 2].decode("ascii")
        length = int(data[i + 2 : i + 4])
        value = data[i + 4 : i + 4 + length].decode("utf-8")
        fields[field_id] = value
        i += 4 + length
    return fields


def test_payload_estrutura_e_crc_validos():
    payload = gerar_payload_copia_e_cola(
        pix_key="organizador@example.com",
        pix_key_type=PixKeyType.email,
        amount=Decimal("50.00"),
        merchant_name="Fulano de Tal",
        merchant_city="Sao Paulo",
        txid="AB23",
    )

    body, crc = payload[:-4], payload[-4:]
    assert _crc16_ccitt(body) == crc

    fields = _parse_tlv(payload)
    assert fields["00"] == "01"
    assert "01" not in fields  # Point of Initiation Method não faz parte do padrão Pix
    assert fields["53"] == "986"
    assert fields["54"] == "50.00"
    assert fields["58"] == "BR"
    assert fields["59"] == "Fulano de Tal"
    assert fields["60"] == "SAO PAULO"  # cidade é normalizada para maiúsculas

    merchant_account_info = _parse_tlv(fields["26"])
    assert merchant_account_info["00"] == "br.gov.bcb.pix"
    assert merchant_account_info["01"] == "organizador@example.com"

    additional_data = _parse_tlv(fields["62"])
    assert additional_data["05"] == "AB23"


def test_payload_bate_com_exemplo_oficial_do_manual_bacen():
    """Reproduz byte a byte o exemplo da seção 2.6.3 do "Manual de Padrões para
    Iniciação do Pix" (Bacen, v2.10.0), incluindo o CRC16 "1D3D" já calculado
    no manual — valida o algoritmo de CRC e a ordem/formato dos campos contra
    uma fonte externa, não só contra a própria implementação."""
    merchant_account_info = _tlv("00", "br.gov.bcb.pix") + _tlv(
        "01", "123e4567-e12b-12d1-a456-426655440000"
    )
    additional_data = _tlv("05", "***")
    payload_sem_crc = (
        _tlv("00", "01")
        + _tlv("26", merchant_account_info)
        + _tlv("52", "0000")
        + _tlv("53", "986")
        + _tlv("58", "BR")
        + _tlv("59", "Fulano de Tal")
        + _tlv("60", "BRASILIA")
        + _tlv("62", additional_data)
        + "6304"
    )

    esperado = (
        "00020126580014br.gov.bcb.pix0136123e4567-e12b-12d1-a456-426655440000"
        "5204000053039865802BR5913Fulano de Tal6008BRASILIA62070503***63041D3D"
    )
    assert payload_sem_crc + _crc16_ccitt(payload_sem_crc) == esperado


def test_txid_vazio_ou_invalido_usa_referencia_generica():
    assert _sanitizar_txid("") == "***"
    assert _sanitizar_txid("!!!") == "***"
    assert _sanitizar_txid("ab-23 cd") == "ab23cd"


@pytest.mark.parametrize(
    "valor_bruto,esperado",
    [
        ("11999999999", "+5511999999999"),
        ("5511999999999", "+5511999999999"),
        ("+55 11 99999-9999", "+5511999999999"),
    ],
)
def testnormalizar_chave_telefone(valor_bruto, esperado):
    assert normalizar_chave(valor_bruto, PixKeyType.telefone) == esperado


def testnormalizar_chave_telefone_invalida_levanta_erro():
    with pytest.raises(ValueError):
        normalizar_chave("123", PixKeyType.telefone)


def testnormalizar_chave_cpf():
    assert normalizar_chave("123.456.789-09", PixKeyType.cpf) == "12345678909"


def testnormalizar_chave_cpf_invalida_levanta_erro():
    with pytest.raises(ValueError):
        normalizar_chave("123", PixKeyType.cpf)


def testnormalizar_chave_cnpj():
    assert normalizar_chave("12.345.678/0001-95", PixKeyType.cnpj) == "12345678000195"


def test_chave_email_nao_ascii_nao_desalinha_campos_seguintes():
    # "josé@example.com" tem 16 caracteres mas 17 bytes em UTF-8 (é/0xC3 0xA9);
    # o tamanho do subcampo "01" tem que refletir bytes, não caracteres.
    payload = gerar_payload_copia_e_cola(
        pix_key="josé@example.com",
        pix_key_type=PixKeyType.email,
        amount=Decimal("10.00"),
        merchant_name="Fulano",
        merchant_city="Sao Paulo",
        txid="AB23",
    )
    fields = _parse_tlv(payload)
    merchant_account_info = _parse_tlv(fields["26"])
    assert merchant_account_info["01"] == "josé@example.com"
    assert fields["52"] == "0000"  # campo seguinte não foi corrompido pelo offset errado


def testnormalizar_chave_email_e_aleatoria_mantem_valor():
    assert normalizar_chave("  a@b.com  ", PixKeyType.email) == "a@b.com"
    assert (
        normalizar_chave("123e4567-e89b-12d3-a456-426614174000", PixKeyType.aleatoria)
        == "123e4567-e89b-12d3-a456-426614174000"
    )


def test_gerar_qr_code_image_retorna_png_valido():
    payload = gerar_payload_copia_e_cola(
        pix_key="a@b.com",
        pix_key_type=PixKeyType.email,
        amount=Decimal("10.00"),
        merchant_name="Fulano",
        merchant_city="Sao Paulo",
        txid="XYZ9",
    )
    image_bytes = gerar_qr_code_image(payload)

    image = Image.open(io.BytesIO(image_bytes))
    assert image.format == "PNG"
    assert image.width > 0 and image.height > 0


def test_br_code_pix_generator_gerar_pix_retorna_pix_payload():
    result = BrCodePixGenerator().gerar_pix(
        pix_key="a@b.com",
        pix_key_type=PixKeyType.email,
        amount=Decimal("25.50"),
        merchant_name="Fulano",
        merchant_city="Sao Paulo",
        txid="AB23",
    )

    assert isinstance(result, PixPayload)
    assert result.copia_e_cola.startswith("000201")
    assert Image.open(io.BytesIO(result.qr_code_image)).format == "PNG"
