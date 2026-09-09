import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.core.exceptions import RoleNaoEncontradoError
from app.db.models import Participant, ParticipantStatus, PixKeyType, Role
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.participant_repository import ParticipantRepository
from app.repositories.role_repository import RoleRepository
from app.services.interfaces import PixGenerator, PixPayload

_ALFABETO_ROLE_CODE = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # sem I, O, 0, 1 (seção 6.2)
_TAMANHO_ROLE_CODE = 4
_TOLERANCIA_OCR = Decimal("0.01")
_CENTAVOS = Decimal("0.01")

# Nome/cidade do QR Pix são cosméticos: o app de quem paga sempre mostra o
# nome registrado no DICT pra chave, ignorando esses campos do BR Code
# (Manual de Padrões para Iniciação do Pix, Bacen) — por isso são fixos em
# vez de perguntados ao organizador (seção 6.3 do CLAUDE.md).
PIX_MERCHANT_NAME = "Rolezin"
PIX_MERCHANT_CITY = "BRASIL"


@dataclass(frozen=True)
class RoleCriado:
    role: Role
    pix_payload: PixPayload


class RoleService:
    """Regra de negócio pura do rolê: criar, registrar pagamento, encerrar."""

    def __init__(
        self,
        role_repository: RoleRepository,
        participant_repository: ParticipantRepository,
        conversation_repository: ConversationRepository,
        pix_generator: PixGenerator,
    ):
        self._role_repository = role_repository
        self._participant_repository = participant_repository
        self._conversation_repository = conversation_repository
        self._pix_generator = pix_generator

    def _gerar_role_code(self) -> str:
        """Gera um código de 4 caracteres, único entre os rolês existentes (seção 6.2)."""
        while True:
            codigo = "".join(random.choices(_ALFABETO_ROLE_CODE, k=_TAMANHO_ROLE_CODE))
            if not self._role_repository.existe_codigo(codigo):
                return codigo

    def criar_role(
        self,
        total_value: Decimal,
        num_people: int,
        organizer_phone: str,
        pix_key: str,
        pix_key_type: PixKeyType,
    ) -> RoleCriado:
        """Cria o rolê: calcula valor/pessoa, gera código e QR Pix, apaga o estado da conversa."""
        value_per_person = (total_value / num_people).quantize(_CENTAVOS, rounding=ROUND_HALF_UP)
        role_code = self._gerar_role_code()

        pix_payload = self._pix_generator.gerar_pix(
            pix_key=pix_key,
            pix_key_type=pix_key_type,
            amount=value_per_person,
            merchant_name=PIX_MERCHANT_NAME,
            merchant_city=PIX_MERCHANT_CITY,
            txid=role_code,
        )

        role = Role(
            organizer_phone=organizer_phone,
            total_value=total_value,
            num_people=num_people,
            value_per_person=value_per_person,
            role_code=role_code,
            pix_key=pix_key,
            pix_key_type=pix_key_type,
        )
        self._role_repository.criar(role)

        conversation_state = self._conversation_repository.obter_por_telefone(organizer_phone)
        if conversation_state is not None:
            self._conversation_repository.apagar(conversation_state)

        self._role_repository.commit()
        return RoleCriado(role=role, pix_payload=pix_payload)

    def registrar_pagamento(
        self,
        role_id: uuid.UUID,
        phone: str,
        receipt_image_url: str,
        extracted_value: Decimal | None,
    ) -> Participant:
        """Cria ou atualiza o Participant do rolê, com status definido pela conferência via OCR."""
        role = self._role_repository.obter_por_id(role_id)
        if role is None:
            raise RoleNaoEncontradoError(role_id)

        participant = self._participant_repository.obter_por_role_e_telefone(role_id, phone)
        if participant is None:
            participant = Participant(role_id=role_id, phone=phone)

        participant.receipt_image_url = receipt_image_url
        participant.extracted_value = extracted_value

        valor_bate = extracted_value is not None and abs(extracted_value - role.value_per_person) <= _TOLERANCIA_OCR
        if valor_bate:
            participant.status = ParticipantStatus.confirmado
            participant.confirmed_at = datetime.now(timezone.utc)
        else:
            # Reenvio com valor que não bate (ou sem OCR) reverte uma
            # confirmação anterior — o status reflete sempre o último
            # comprovante enviado, nunca um histórico acumulado.
            participant.status = ParticipantStatus.comprovante_enviado
            participant.confirmed_at = None

        self._participant_repository.salvar(participant)
        self._participant_repository.commit()
        return participant

    def confirmar_encerramento(self, role_id: uuid.UUID) -> None:
        """Apaga o rolê (participantes em cascade) e o estado da conversa, numa única transação."""
        role = self._role_repository.obter_por_id(role_id)
        if role is None:
            raise RoleNaoEncontradoError(role_id)

        conversation_state = self._conversation_repository.obter_por_telefone(role.organizer_phone)

        self._role_repository.apagar(role)
        if conversation_state is not None:
            self._conversation_repository.apagar(conversation_state)

        self._role_repository.commit()
