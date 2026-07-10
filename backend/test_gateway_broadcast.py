"""RED/GREEN gate for 03-01 Task 2: gateway.execute_tool must broadcast +
AuditLog every lifecycle event from the single choke point, in the exact
order the locked ws_event_schema defines, without changing the
ALLOW/DENY/REQUIRE_APPROVAL outcome (T-03-01, 03-01-PLAN.md <behavior>).

Reuses test_gateway.py's Fake collaborators/session-factory helper directly
(plain functions/classes, not pytest fixtures - no cross-file fixture
plumbing needed, ponytail).
"""

import asyncio

from sqlalchemy import select

from gateway import ToolExecutionGateway
from models import AuditLog
from test_gateway import DEFAULT_RULES, FakeApprovalManager, FakeMCPManager, _make_session_factory


class FakeBroadcast:
    """Collects every broadcast event in order; can be told to raise to
    prove a broadcast failure never changes the outcome (T-03-01)."""

    def __init__(self, raise_on_send: bool = False) -> None:
        self.events: list[dict] = []
        self.raise_on_send = raise_on_send

    async def __call__(self, event: dict) -> None:
        if self.raise_on_send:
            raise RuntimeError("dead socket")
        self.events.append(event)


def _run(gateway, tool_name):
    return asyncio.run(
        gateway.execute_tool(
            tool_name=tool_name,
            server_name="sandbox",
            arguments={"path": "x"},
            conversation_id="c1",
            token_usage=0,
        )
    )


def _audit_count(session_factory) -> int:
    async def _count():
        async with session_factory() as session:
            rows = (await session.execute(select(AuditLog))).scalars().all()
            return len(rows)

    return asyncio.run(_count())


def test_allow_path_emits_four_events_and_four_audit_rows(tmp_path):
    session_factory = _make_session_factory(
        tmp_path,
        [
            {
                "id": "R-ALLOW-READ",
                "rule_type": "require_approval",
                "tool_name": "read_file",
                "condition": {},
                "action": "ALLOW",
                "enabled": True,
            }
        ],
    )
    broadcast = FakeBroadcast()
    gateway = ToolExecutionGateway(FakeMCPManager(), session_factory, FakeApprovalManager("approve"), broadcast=broadcast)
    result = _run(gateway, "read_file")
    assert result.ok is True
    assert [e["type"] for e in broadcast.events] == [
        "tool_requested",
        "policy_decided",
        "execution_started",
        "execution_completed",
    ]
    assert _audit_count(session_factory) == 4


def test_deny_path_emits_three_events_no_execution_started(tmp_path):
    session_factory = _make_session_factory(tmp_path, DEFAULT_RULES)
    broadcast = FakeBroadcast()
    gateway = ToolExecutionGateway(FakeMCPManager(), session_factory, FakeApprovalManager("approve"), broadcast=broadcast)
    result = _run(gateway, "delete_file")
    assert result.ok is False
    types = [e["type"] for e in broadcast.events]
    assert types == ["tool_requested", "policy_decided", "execution_failed"]
    assert _audit_count(session_factory) == 3


def test_require_approval_approve_emits_six_events_in_order(tmp_path):
    session_factory = _make_session_factory(tmp_path, DEFAULT_RULES)
    broadcast = FakeBroadcast()
    gateway = ToolExecutionGateway(FakeMCPManager(), session_factory, FakeApprovalManager("approve"), broadcast=broadcast)
    result = _run(gateway, "write_file")
    assert result.ok is True
    assert [e["type"] for e in broadcast.events] == [
        "tool_requested",
        "policy_decided",
        "approval_required",
        "approval_granted",
        "execution_started",
        "execution_completed",
    ]
    assert _audit_count(session_factory) == 6


def test_require_approval_reject_emits_execution_failed(tmp_path):
    session_factory = _make_session_factory(tmp_path, DEFAULT_RULES)
    broadcast = FakeBroadcast()
    gateway = ToolExecutionGateway(FakeMCPManager(), session_factory, FakeApprovalManager("reject"), broadcast=broadcast)
    result = _run(gateway, "write_file")
    assert result.ok is False
    assert [e["type"] for e in broadcast.events] == [
        "tool_requested",
        "policy_decided",
        "approval_required",
        "approval_rejected",
        "execution_failed",
    ]


def test_broadcast_failure_never_changes_outcome_or_audit_rows(tmp_path):
    session_factory = _make_session_factory(
        tmp_path,
        [
            {
                "id": "R-ALLOW-READ",
                "rule_type": "require_approval",
                "tool_name": "read_file",
                "condition": {},
                "action": "ALLOW",
                "enabled": True,
            }
        ],
    )
    broadcast = FakeBroadcast(raise_on_send=True)
    gateway = ToolExecutionGateway(FakeMCPManager(), session_factory, FakeApprovalManager("approve"), broadcast=broadcast)
    result = _run(gateway, "read_file")
    assert result.ok is True
    assert broadcast.events == []  # every send raised, none collected
    assert _audit_count(session_factory) == 4  # audit writes are independent of broadcast success
