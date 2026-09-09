"""Teste de integração: precisa de Postgres real (`docker compose up -d postgres`).

Cobre a exigência explícita da seção 9 do CLAUDE.md: confirmar_encerramento
remove Role e Participants juntos (via ON DELETE CASCADE do banco, que uma
fake de repository não consegue simular).
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Base, Participant, ParticipantStatus, PixKeyType
from app.repositories.conversation_repository import SqlAlchemyConversationRepository
from app.repositories.participant_repository import SqlAlchemyParticipantRepository
from app.repositories.role_repository import SqlAlchemyRoleRepository
from app.services.interfaces import PixPayload
from app.services.role_service import RoleService


class _FakePixGenerator:
    def gerar_pix(self, **kwargs):
        return PixPayload(copia_e_cola="fake", qr_code_image=b"fake")


@pytest.fixture()
def db_session():
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(engine)


def test_confirmar_encerramento_remove_role_e_participants_via_cascade(db_session):
    role_repo = SqlAlchemyRoleRepository(db_session)
    participant_repo = SqlAlchemyParticipantRepository(db_session)
    conversation_repo = SqlAlchemyConversationRepository(db_session)
    service = RoleService(role_repo, participant_repo, conversation_repo, _FakePixGenerator())

    criado = service.criar_role(
        total_value=Decimal("100.00"),
        num_people=2,
        organizer_phone="5511999999999",
        pix_key="a@b.com",
        pix_key_type=PixKeyType.email,
    )
    role_id = criado.role.id

    participant_repo.salvar(
        Participant(role_id=role_id, phone="5511888888888", status=ParticipantStatus.confirmado)
    )
    participant_repo.commit()
    assert len(participant_repo.listar_por_role(role_id)) == 1

    service.confirmar_encerramento(role_id)

    assert role_repo.obter_por_id(role_id) is None
    assert participant_repo.listar_por_role(role_id) == []


def test_registrar_pagamento_persiste_e_le_de_volta(db_session):
    role_repo = SqlAlchemyRoleRepository(db_session)
    participant_repo = SqlAlchemyParticipantRepository(db_session)
    conversation_repo = SqlAlchemyConversationRepository(db_session)
    service = RoleService(role_repo, participant_repo, conversation_repo, _FakePixGenerator())

    criado = service.criar_role(
        total_value=Decimal("100.00"),
        num_people=2,
        organizer_phone="5511999999999",
        pix_key="a@b.com",
        pix_key_type=PixKeyType.email,
    )

    service.registrar_pagamento(criado.role.id, "5511888888888", "http://img", Decimal("50.00"))

    participante = participant_repo.obter_por_role_e_telefone(criado.role.id, "5511888888888")
    assert participante is not None
    assert participante.status == ParticipantStatus.confirmado
