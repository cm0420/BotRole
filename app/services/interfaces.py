from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from app.db.models import PixKeyType


@dataclass(frozen=True)
class PixPayload:
    copia_e_cola: str
    qr_code_image: bytes


class PixGenerator(Protocol):
    def gerar_pix(
        self,
        pix_key: str,
        pix_key_type: PixKeyType,
        amount: Decimal,
        merchant_name: str,
        merchant_city: str,
        txid: str,
    ) -> PixPayload: ...


@dataclass(frozen=True)
class OcrResult:
    extracted_value: Decimal | None
    extracted_date: date | None


class OcrExtractor(Protocol):
    def extrair(self, image_bytes: bytes) -> OcrResult: ...
