import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class PixKeyType(str, enum.Enum):
    cpf = "cpf"
    cnpj = "cnpj"
    email = "email"
    telefone = "telefone"
    aleatoria = "aleatoria"


class ParticipantStatus(str, enum.Enum):
    pendente = "pendente"
    comprovante_enviado = "comprovante_enviado"
    confirmado = "confirmado"


class ConversationStep(str, enum.Enum):
    aguardando_valor = "aguardando_valor"
    aguardando_num_pessoas = "aguardando_num_pessoas"
    aguardando_confirmacao_encerramento = "aguardando_confirmacao_encerramento"


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organizer_phone: Mapped[str] = mapped_column(String, index=True)
    total_value: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    num_people: Mapped[int] = mapped_column()
    value_per_person: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    role_code: Mapped[str] = mapped_column(String(4), index=True)
    pix_key: Mapped[str] = mapped_column(String)
    pix_key_type: Mapped[PixKeyType] = mapped_column(SAEnum(PixKeyType, name="pix_key_type"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    participants: Mapped[list["Participant"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (UniqueConstraint("role_id", "phone", name="uq_participant_role_phone"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    phone: Mapped[str] = mapped_column(String)
    status: Mapped[ParticipantStatus] = mapped_column(
        SAEnum(ParticipantStatus, name="participant_status"), default=ParticipantStatus.pendente
    )
    receipt_image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    extracted_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    role: Mapped["Role"] = relationship(back_populates="participants")


class ConversationState(Base):
    __tablename__ = "conversation_states"

    phone: Mapped[str] = mapped_column(String, primary_key=True)
    step: Mapped[ConversationStep] = mapped_column(SAEnum(ConversationStep, name="conversation_step"))
    pending_total_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    pending_role_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
