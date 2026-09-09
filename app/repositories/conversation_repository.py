from typing import Protocol

from sqlalchemy.orm import Session

from app.db.models import ConversationState


class ConversationRepository(Protocol):
    def obter_por_telefone(self, phone: str) -> ConversationState | None: ...
    def salvar(self, state: ConversationState) -> ConversationState: ...
    def apagar(self, state: ConversationState) -> None: ...
    def commit(self) -> None: ...


class SqlAlchemyConversationRepository:
    """Implementação concreta de ConversationRepository via SQLAlchemy."""

    def __init__(self, session: Session):
        self._session = session

    def obter_por_telefone(self, phone: str) -> ConversationState | None:
        return self._session.get(ConversationState, phone)

    def salvar(self, state: ConversationState) -> ConversationState:
        self._session.add(state)
        self._session.flush()
        return state

    def apagar(self, state: ConversationState) -> None:
        self._session.delete(state)
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()
