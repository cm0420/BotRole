from decimal import Decimal, InvalidOperation


def parse_valor_br(texto: str) -> Decimal | None:
    """Converte texto em formato BR (vírgula ou ponto decimal) pra Decimal; None se não for número."""
    texto = texto.strip()
    normalizado = texto.replace(".", "").replace(",", ".") if "," in texto else texto
    try:
        return Decimal(normalizado)
    except InvalidOperation:
        return None


def parse_inteiro(texto: str) -> int | None:
    """Converte texto num inteiro; None se não for um inteiro válido."""
    try:
        return int(texto.strip())
    except ValueError:
        return None
