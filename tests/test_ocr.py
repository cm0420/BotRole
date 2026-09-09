from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.services.interfaces import OcrResult
from app.services.ocr import TesseractOcrExtractor, extrair_data, extrair_valor


def test_extrai_valor_unico_com_prefixo_rs():
    texto = "Comprovante de transferência\nValor: R$ 150,00\nData: 09/09/2026"
    assert extrair_valor(texto) == Decimal("150.00")


def test_extrai_valor_com_milhar():
    texto = "Valor pago: R$ 1.234,56"
    assert extrair_valor(texto) == Decimal("1234.56")


def test_multiplos_valores_desambiguados_pela_linha_com_palavra_chave():
    texto = "Saldo em conta: R$ 1.200,00\nValor: R$ 150,00\nTaxa: R$ 0,00"
    assert extrair_valor(texto) == Decimal("150.00")


def test_multiplos_valores_sem_desambiguacao_clara_retorna_none():
    texto = "Valor da compra: R$ 100,00\nValor total com juros: R$ 110,00"
    assert extrair_valor(texto) is None


def test_sem_nenhum_valor_retorna_none():
    texto = "Comprovante ilegível sem número nenhum aqui"
    assert extrair_valor(texto) is None


def test_valor_sem_prefixo_rs_como_fallback():
    texto = "Total: 75,90 pago com sucesso"
    assert extrair_valor(texto) == Decimal("75.90")


def test_extrai_data_ano_com_4_digitos():
    assert extrair_data("Pago em 09/09/2026 às 14:32") == date(2026, 9, 9)


def test_extrai_data_ano_com_2_digitos():
    assert extrair_data("09/09/26") == date(2026, 9, 9)


def test_data_invalida_retorna_none():
    assert extrair_data("31/02/2026") is None


def test_sem_data_retorna_none():
    assert extrair_data("nenhuma data aqui") is None


def test_tesseract_ocr_extractor_usa_extrair_valor_e_extrair_data():
    # Sem o binário do Tesseract instalado localmente (só na imagem Docker,
    # seção 3), mocka pytesseract pra validar a integração sem depender dele.
    imagem_fake = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # bytes só pra Image.open não sobrar vazio
    with patch("app.services.ocr.Image.open") as mock_open, patch(
        "app.services.ocr.pytesseract.image_to_string", return_value="Valor: R$ 42,00\n09/09/2026"
    ) as mock_ocr:
        result = TesseractOcrExtractor().extrair(imagem_fake)

    mock_open.assert_called_once()
    mock_ocr.assert_called_once_with(mock_open.return_value, lang="por")
    assert result == OcrResult(extracted_value=Decimal("42.00"), extracted_date=date(2026, 9, 9))


def test_tesseract_ocr_extractor_sem_valor_reconhecivel_retorna_none():
    with patch("app.services.ocr.Image.open"), patch(
        "app.services.ocr.pytesseract.image_to_string", return_value="texto sem valor nenhum"
    ):
        result = TesseractOcrExtractor().extrair(b"fake")

    assert result == OcrResult(extracted_value=None, extracted_date=None)
