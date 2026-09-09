"""add pix key collection fields to conversation_state

Revision ID: 018ffb8d09b1
Revises: bf2c9f8e85af
Create Date: 2026-09-09 10:04:37.976318

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '018ffb8d09b1'
down_revision: Union[str, Sequence[str], None] = 'bf2c9f8e85af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # pix_key_type já existe (criado na migration bf2c9f8e85af) — create_type=False
    # evita um CREATE TYPE duplicado ao adicionar a coluna.
    op.add_column('conversation_states', sa.Column('pending_num_people', sa.Integer(), nullable=True))
    op.add_column(
        'conversation_states',
        sa.Column(
            'pending_pix_key_type',
            sa.Enum('cpf', 'cnpj', 'email', 'telefone', 'aleatoria', name='pix_key_type', create_type=False),
            nullable=True,
        ),
    )
    op.execute("ALTER TYPE conversation_step ADD VALUE 'aguardando_tipo_chave_pix'")
    op.execute("ALTER TYPE conversation_step ADD VALUE 'aguardando_chave_pix'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('conversation_states', 'pending_pix_key_type')
    op.drop_column('conversation_states', 'pending_num_people')
    # ConversationState é estado efêmero (não é dado do rolê em si): uma linha
    # parada num passo que deixará de existir é descartável — o organizador
    # simplesmente recomeça a pergunta. Sem isso, o cast abaixo falharia com
    # "invalid input value for enum" pra qualquer linha nesses dois passos.
    op.execute(
        "DELETE FROM conversation_states "
        "WHERE step IN ('aguardando_tipo_chave_pix', 'aguardando_chave_pix')"
    )
    # Postgres não suporta remover valor de enum diretamente: recria o tipo
    # só com os 3 valores originais.
    op.execute("ALTER TYPE conversation_step RENAME TO conversation_step_old")
    op.execute(
        "CREATE TYPE conversation_step AS ENUM "
        "('aguardando_valor', 'aguardando_num_pessoas', 'aguardando_confirmacao_encerramento')"
    )
    op.execute(
        "ALTER TABLE conversation_states "
        "ALTER COLUMN step TYPE conversation_step USING step::text::conversation_step"
    )
    op.execute("DROP TYPE conversation_step_old")
