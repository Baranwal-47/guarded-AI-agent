"""Conversation + Message ORM models (SQLAlchemy 2.0 declarative style).

Only the two tables this plan's slice needs. Policy, PolicyRule,
ApprovalRequest, ToolExecution, AuditLog are added by later plans in this
phase, each adding the models its own slice needs — not here.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    role: Mapped[str]
    content: Mapped[str]
    # Indexed for ordered replay (eager select().order_by(created_at) at
    # startup — Pitfall 5, no lazy relationship traversal).
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, index=True)
