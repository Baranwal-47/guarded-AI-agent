"""Conversation, Message, Policy, PolicyRule, ApprovalRequest ORM models
(SQLAlchemy 2.0 declarative style).

ToolExecution, AuditLog are added by later plans in this phase, each adding
the models its own slice needs — not here.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, ForeignKey
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


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class PolicyRule(Base):
    """Columns line up 1:1 with policy_engine.Rule so load_rules() can
    convert a row straight into a Rule dataclass with no extra mapping."""

    __tablename__ = "policy_rules"

    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("policies.id"), default=None)
    rule_type: Mapped[str]
    tool_name: Mapped[str]
    condition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    action: Mapped[str]  # Action enum value: "ALLOW" / "DENY" / "REQUIRE_APPROVAL"
    enabled: Mapped[bool] = mapped_column(default=True)


class ApprovalRequest(Base):
    """A REQUIRE_APPROVAL tool call blocked on human/timeout resolution.

    status: PENDING / APPROVED / DENIED. decided_by: "human" (POST
    /approvals/{id}) / "system-timeout" (5-min auto-deny) /
    "system-restart" (startup reconciliation of orphaned PENDING rows) —
    null while still PENDING. The conditional `UPDATE ... WHERE
    status='PENDING'` in gateway.try_decide() is the sole race arbiter
    across all three deciders (RESEARCH Pattern 2); this row is the durable
    record of whichever one won.
    """

    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    tool_name: Mapped[str]
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str]
    status: Mapped[str] = mapped_column(default="PENDING")
    decided_by: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
