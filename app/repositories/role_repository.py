import uuid
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Role


class RoleRepository(Protocol):
    def existe_codigo(self, role_code: str) -> bool: ...
    def obter_por_id(self, role_id: uuid.UUID) -> Role | None: ...
    def obter_por_codigo(self, role_code: str) -> Role | None: ...
    def obter_mais_recente_por_organizador(self, organizer_phone: str) -> Role | None: ...
    def criar(self, role: Role) -> Role: ...
    def apagar(self, role: Role) -> None: ...
    def commit(self) -> None: ...


class SqlAlchemyRoleRepository:
    """Implementação concreta de RoleRepository via SQLAlchemy.

    Compartilha a mesma Session com os outros repositories injetados numa
    mesma chamada de role_service — commit() num deles finaliza a transação
    inteira (necessário pro apagamento atômico da seção 8 do CLAUDE.md).
    """

    def __init__(self, session: Session):
        self._session = session

    def existe_codigo(self, role_code: str) -> bool:
        return self._session.execute(select(Role.id).where(Role.role_code == role_code)).first() is not None

    def obter_por_id(self, role_id: uuid.UUID) -> Role | None:
        return self._session.get(Role, role_id)

    def obter_por_codigo(self, role_code: str) -> Role | None:
        return self._session.execute(select(Role).where(Role.role_code == role_code)).scalar_one_or_none()

    def obter_mais_recente_por_organizador(self, organizer_phone: str) -> Role | None:
        return self._session.execute(
            select(Role).where(Role.organizer_phone == organizer_phone).order_by(Role.created_at.desc()).limit(1)
        ).scalar_one_or_none()

    def criar(self, role: Role) -> Role:
        self._session.add(role)
        self._session.flush()
        return role

    def apagar(self, role: Role) -> None:
        self._session.delete(role)
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()
