import os
import uuid
from datetime import datetime, timezone

# Precisa rodar antes de qualquer import de app.* — app/core/config.py
# instancia Settings() (e app/db/base.py cria a engine, validando a URL) no
# carregamento do módulo. Testes usam só fakes/mocks, nunca conectam de
# verdade, mas o valor ainda precisa ser sintaticamente parseável.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("WHATSAPP_TOKEN", "test-token")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "test-phone-id")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "x")

import pytest

from app.db.models import ConversationState, Participant, Role
from app.services.interfaces import PixPayload
from app.services.role_service import RoleService


class FakeRoleRepository:
    def __init__(self):
        self.roles: dict[uuid.UUID, Role] = {}
        self.committed = False

    def existe_codigo(self, role_code):
        return any(r.role_code == role_code for r in self.roles.values())

    def obter_por_id(self, role_id):
        return self.roles.get(role_id)

    def obter_por_codigo(self, role_code):
        return next((r for r in self.roles.values() if r.role_code == role_code), None)

    def obter_mais_recente_por_organizador(self, organizer_phone):
        candidatos = [r for r in self.roles.values() if r.organizer_phone == organizer_phone]
        return max(candidatos, key=lambda r: r.created_at) if candidatos else None

    def criar(self, role):
        if role.id is None:
            role.id = uuid.uuid4()
        if role.created_at is None:
            role.created_at = datetime.now(timezone.utc)
        self.roles[role.id] = role
        return role

    def apagar(self, role):
        self.roles.pop(role.id, None)

    def commit(self):
        self.committed = True


class FakeParticipantRepository:
    def __init__(self):
        self.participants: dict[tuple[uuid.UUID, str], Participant] = {}
        self.committed = False

    def obter_por_role_e_telefone(self, role_id, phone):
        return self.participants.get((role_id, phone))

    def salvar(self, participant):
        if participant.id is None:
            participant.id = uuid.uuid4()
        self.participants[(participant.role_id, participant.phone)] = participant
        return participant

    def listar_por_role(self, role_id):
        return [p for p in self.participants.values() if p.role_id == role_id]

    def commit(self):
        self.committed = True


class FakeConversationRepository:
    def __init__(self):
        self.states: dict[str, ConversationState] = {}

    def obter_por_telefone(self, phone):
        return self.states.get(phone)

    def salvar(self, state):
        self.states[state.phone] = state
        return state

    def apagar(self, state):
        self.states.pop(state.phone, None)

    def commit(self):
        pass


class FakePixGenerator:
    def __init__(self):
        self.chamadas = []

    def gerar_pix(self, pix_key, pix_key_type, amount, merchant_name, merchant_city, txid):
        self.chamadas.append(
            dict(
                pix_key=pix_key,
                pix_key_type=pix_key_type,
                amount=amount,
                merchant_name=merchant_name,
                merchant_city=merchant_city,
                txid=txid,
            )
        )
        return PixPayload(copia_e_cola=f"FAKE|{pix_key}|{amount}|{txid}", qr_code_image=b"fake-png")


@pytest.fixture()
def repos():
    return FakeRoleRepository(), FakeParticipantRepository(), FakeConversationRepository()


@pytest.fixture()
def pix_generator():
    return FakePixGenerator()


@pytest.fixture()
def role_service(repos, pix_generator):
    role_repo, participant_repo, conversation_repo = repos
    return RoleService(role_repo, participant_repo, conversation_repo, pix_generator)
