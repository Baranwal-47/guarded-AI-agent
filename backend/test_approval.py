"""Tests for the durable async approval workflow: ApprovalManager, the
try_decide() race-arbiter, the gateway's REQUIRE_APPROVAL Future-block +
timer, and startup reconciliation (APPROVAL-01/02/03).

Uses plain asyncio.run() (no pytest-asyncio), matching test_gateway.py's
convention. Throwaway tmp_path SQLite + own sessionmaker + create_all,
never touching the real DB.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from approval_manager import ApprovalManager
from db import Base
from gateway import ToolExecutionGateway, reconcile_pending_approvals, try_decide
from mcp_manager import ToolResult
from models import ApprovalRequest, PolicyRule


def _make_session_factory(tmp_path, rules: list[dict]):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/approval-test.db")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            for r in rules:
                session.add(PolicyRule(**r))
            await session.commit()

    asyncio.run(_setup())
    return session_factory


APPROVE_RULE = [
    {
        "id": "R-APPROVE",
        "rule_type": "require_approval",
        "tool_name": "write_file",
        "condition": {},
        "action": "REQUIRE_APPROVAL",
        "enabled": True,
    }
]


class FakeMCPManager:
    """Records call() invocations; never touches a real MCP transport."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool_name: str, arguments: dict) -> ToolResult:
        self.calls.append((tool_name, arguments))
        return ToolResult(ok=True, content="fake-content", error=None)


async def _pending_request_id(session_factory) -> str:
    async with session_factory() as session:
        return (
            await session.execute(select(ApprovalRequest.id).where(ApprovalRequest.status == "PENDING"))
        ).scalar_one()


def test_approve_unblocks_and_executes(tmp_path):
    session_factory = _make_session_factory(tmp_path, APPROVE_RULE)
    fake_manager = FakeMCPManager()
    approval_manager = ApprovalManager()
    gateway = ToolExecutionGateway(fake_manager, session_factory, approval_manager, timeout_seconds=300)

    async def _run():
        task = asyncio.create_task(
            gateway.execute_tool(
                tool_name="write_file",
                server_name="sandbox",
                arguments={"path": "x"},
                conversation_id="c1",
                token_usage=0,
            )
        )
        await asyncio.sleep(0.05)  # let the gateway insert the PENDING row + register the Future
        request_id = await _pending_request_id(session_factory)
        async with session_factory() as session:
            won = await try_decide(session, request_id, "APPROVED", "human")
        assert won is True
        approval_manager.wake(request_id, "approve")
        return await task

    result = asyncio.run(_run())
    assert fake_manager.calls == [("write_file", {"path": "x"})]
    assert result.ok is True


def test_reject_fails_closed_no_mcp_call(tmp_path):
    session_factory = _make_session_factory(tmp_path, APPROVE_RULE)
    fake_manager = FakeMCPManager()
    approval_manager = ApprovalManager()
    gateway = ToolExecutionGateway(fake_manager, session_factory, approval_manager, timeout_seconds=300)

    async def _run():
        task = asyncio.create_task(
            gateway.execute_tool(
                tool_name="write_file",
                server_name="sandbox",
                arguments={"path": "x"},
                conversation_id="c1",
                token_usage=0,
            )
        )
        await asyncio.sleep(0.05)
        request_id = await _pending_request_id(session_factory)
        async with session_factory() as session:
            won = await try_decide(session, request_id, "DENIED", "human")
        assert won is True
        approval_manager.wake(request_id, "reject")
        return await task

    result = asyncio.run(_run())
    assert fake_manager.calls == []
    assert result.ok is False


def test_duplicate_decision_is_noop(tmp_path):
    session_factory = _make_session_factory(tmp_path, [])

    async def _run():
        async with session_factory() as session:
            session.add(
                ApprovalRequest(
                    id="req-1",
                    tool_name="write_file",
                    arguments={"path": "x"},
                    reason="test",
                    status="PENDING",
                )
            )
            await session.commit()

        async with session_factory() as session:
            first = await try_decide(session, "req-1", "APPROVED", "human")
        async with session_factory() as session:
            second = await try_decide(session, "req-1", "DENIED", "human")

        async with session_factory() as session:
            row = await session.get(ApprovalRequest, "req-1")
            return first, second, row.status, row.decided_by

    first, second, status, decided_by = asyncio.run(_run())
    assert first is True
    assert second is False  # rowcount 0 - duplicate is a no-op, not an exception
    assert status == "APPROVED"
    assert decided_by == "human"


def test_timeout_wins_when_no_human_decision(tmp_path):
    session_factory = _make_session_factory(tmp_path, APPROVE_RULE)
    fake_manager = FakeMCPManager()
    approval_manager = ApprovalManager()
    # Injected sub-second timeout - never wait on the real 300s default.
    gateway = ToolExecutionGateway(fake_manager, session_factory, approval_manager, timeout_seconds=0.05)

    result = asyncio.run(
        gateway.execute_tool(
            tool_name="write_file",
            server_name="sandbox",
            arguments={"path": "x"},
            conversation_id="c1",
            token_usage=0,
        )
    )
    assert fake_manager.calls == []
    assert result.ok is False


def test_startup_reconciliation_denies_orphans(tmp_path):
    session_factory = _make_session_factory(tmp_path, [])

    async def _run():
        async with session_factory() as session:
            session.add(
                ApprovalRequest(id="orphan-1", tool_name="write_file", arguments={}, reason="x", status="PENDING")
            )
            session.add(
                ApprovalRequest(id="orphan-2", tool_name="delete_file", arguments={}, reason="y", status="PENDING")
            )
            await session.commit()

        async with session_factory() as session:
            reconciled = await reconcile_pending_approvals(session)

        async with session_factory() as session:
            row1 = await session.get(ApprovalRequest, "orphan-1")
            row2 = await session.get(ApprovalRequest, "orphan-2")
            return reconciled, row1.status, row1.decided_by, row2.status, row2.decided_by

    reconciled, status1, decided_by1, status2, decided_by2 = asyncio.run(_run())
    assert reconciled == 2
    assert status1 == "DENIED"
    assert decided_by1 == "system-restart"
    assert status2 == "DENIED"
    assert decided_by2 == "system-restart"
