import uuid
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Participant


class ParticipantRepository(Protocol):
    def obter_por_role_e_telefone(self, role_id: uuid.UUID, phone: str) -> Participant | None: ...
    def salvar(self, participant: Participant) -> Participant: ...
    def listar_por_role(self, role_id: uuid.UUID) -> list[Participant]: ...
    def commit(self) -> None: ...


class SqlAlchemyParticipantRepository:
    """Implementação concreta de ParticipantRepository via SQLAlchemy."""

    def __init__(self, session: Session):
        self._session = session

    def obter_por_role_e_telefone(self, role_id: uuid.UUID, phone: str) -> Participant | None:
        return self._session.execute(
            select(Participant).where(Participant.role_id == role_id, Participant.phone == phone)
        ).scalar_one_or_none()

    def salvar(self, participant: Participant) -> Participant:
        self._session.add(participant)
        self._session.flush()
        return participant

    def listar_por_role(self, role_id: uuid.UUID) -> list[Participant]:
        return list(self._session.execute(select(Participant).where(Participant.role_id == role_id)).scalars())

    def commit(self) -> None:
        self._session.commit()
