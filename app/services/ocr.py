import io
import re
from datetime import date
from decimal import Decimal

import pytesseract
from PIL import Image

from app.services.interfaces import OcrResult

_VALOR_COM_PREFIXO_RE = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2})")
_VALOR_SEM_PREFIXO_RE = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d{3})*,\d{2})(?!\d)")
_DATA_RE = re.compile(r"\b(\d{2}/\d{2}/(?:\d{4}|\d{2}))\b")
_PALAVRAS_CHAVE_VALOR = ("valor", "total")


def _parse_valor_brl(texto_valor: str) -> Decimal:
    return Decimal(texto_valor.replace(".", "").replace(",", "."))


def _linha_tem_palavra_chave(linha: str) -> bool:
    linha_lower = linha.lower()
    return any(palavra in linha_lower for palavra in _PALAVRAS_CHAVE_VALOR)


def _escolher_valor(texto: str, padrao: re.Pattern[str]) -> Decimal | None:
    todos = padrao.findall(texto)
    if len(todos) == 1:
        return _parse_valor_brl(todos[0])
    if not todos:
        return None
    # Mais de um candidato: só arrisca se exatamente uma linha com "valor"/
    # "total" tiver exatamente um candidato — senão é ambíguo (seção 2 do
    # CLAUDE.md: "não fingir certeza sobre pagamento").
    candidatos_em_linhas_chave = {
        match
        for linha in texto.splitlines()
        if _linha_tem_palavra_chave(linha)
        for match in padrao.findall(linha)
    }
    if len(candidatos_em_linhas_chave) == 1:
        return _parse_valor_brl(next(iter(candidatos_em_linhas_chave)))
    return None


def extrair_valor(texto: str) -> Decimal | None:
    """Extrai o valor pago do texto OCR do comprovante, ou None se for ambíguo/ausente."""
    valor = _escolher_valor(texto, _VALOR_COM_PREFIXO_RE)
    if valor is not None:
        return valor
    return _escolher_valor(texto, _VALOR_SEM_PREFIXO_RE)


def extrair_data(texto: str) -> date | None:
    """Extrai a primeira data (DD/MM/AAAA ou DD/MM/AA) encontrada no texto OCR."""
    match = _DATA_RE.search(texto)
    if not match:
        return None
    dia, mes, ano = match.group(1).split("/")
    ano_completo = ano if len(ano) == 4 else f"20{ano}"
    try:
        return date(int(ano_completo), int(mes), int(dia))
    except ValueError:
        return None


class TesseractOcrExtractor:
    """Implementação concreta de OcrExtractor via Tesseract (idioma português)."""

    def extrair(self, image_bytes: bytes) -> OcrResult:
        """Lê o comprovante e tenta extrair valor e data pagos; None quando não dá pra confiar."""
        imagem = Image.open(io.BytesIO(image_bytes))
        texto = pytesseract.image_to_string(imagem, lang="por")
        return OcrResult(extracted_value=extrair_valor(texto), extracted_date=extrair_data(texto))
