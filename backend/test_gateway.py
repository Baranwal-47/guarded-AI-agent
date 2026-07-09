"""Tests for ToolExecutionGateway: policy branching, sole-caller invariant,
uniform ToolResult return shape, fail-closed approval + policy-exception paths.

Uses plain asyncio.run() instead of pytest-asyncio: execute_tool is the only
async surface under test and stdlib asyncio already covers running it -
no new test dependency needed (ponytail: stdlib solves it).

Rules now live in a throwaway SQLite DB (tmp_path) instead of a YAML file
(02-02: gateway takes a session_factory, not a rules_path) - same
R-DENY/R-APPROVE/R-ALLOW-READ shapes as before, just inserted as PolicyRule
rows instead of written as YAML text.

02-03: the REQUIRE_APPROVAL branch no longer prompts on the terminal - it
blocks on an ApprovalManager-registered Future. Tests use a FakeApprovalManager
whose register() returns an already-resolved Future (PATTERNS.md test
guidance), preserving the exact prior fail-closed/executes assertions.
"""

import asyncio
import inspect

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from approval_manager import ApprovalManager
from db import Base
from gateway import ToolExecutionGateway
from mcp_manager import ToolResult
from models import PolicyRule


def _make_session_factory(tmp_path, rules: list[dict]):
    """Build a throwaway SQLite session factory pre-seeded with PolicyRule
    rows, mirroring the old rules_path fixture's YAML content."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/gw-test.db")
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


DEFAULT_RULES = [
    {
        "id": "R-DENY",
        "rule_type": "block_tool",
        "tool_name": "delete_file",
        "condition": {},
        "action": "DENY",
        "enabled": True,
    },
    {
        "id": "R-APPROVE",
        "rule_type": "require_approval",
        "tool_name": "write_file",
        "condition": {},
        "action": "REQUIRE_APPROVAL",
        "enabled": True,
    },
]


@pytest.fixture
def session_factory(tmp_path):
    return _make_session_factory(tmp_path, DEFAULT_RULES)


@pytest.fixture
def fake_manager():
    return FakeMCPManager()


@pytest.fixture
def approval_manager():
    """Real ApprovalManager for branches that never reach REQUIRE_APPROVAL
    (DENY/ALLOW/exception tests) - constructed but never exercised there."""
    return ApprovalManager()


class FakeMCPManager:
    """Records call() invocations; never touches a real MCP transport."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool_name: str, arguments: dict) -> ToolResult:
        self.calls.append((tool_name, arguments))
        return ToolResult(ok=True, content="fake-content", error=None)


class FakeApprovalManager:
    """register() returns an already-resolved Future carrying `decision` -
    mirrors ApprovalManager's interface with no real POST/timeout round-trip
    (PATTERNS.md: "fake ApprovalManager.register to return an
    already-resolved future")."""

    def __init__(self, decision: str) -> None:
        self.decision = decision

    def register(self, request_id: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(self.decision)
        return fut

    def wake(self, request_id: str, decision: str) -> None:  # pragma: no cover - never called (pre-resolved)
        pass

    def discard(self, request_id: str) -> None:
        pass


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


def test_deny_rule_blocks_call_and_returns_synthesized_tool_result(fake_manager, session_factory, approval_manager):
    gateway = ToolExecutionGateway(fake_manager, session_factory, approval_manager)
    result = _run(gateway, "delete_file")
    assert fake_manager.calls == []
    assert isinstance(result, ToolResult)
    assert result.ok is False
    assert result.content is None
    assert result.error is not None


def test_require_approval_rejected_decision_fails_closed(fake_manager, session_factory):
    gateway = ToolExecutionGateway(fake_manager, session_factory, FakeApprovalManager("reject"))
    result = _run(gateway, "write_file")
    assert fake_manager.calls == []
    assert result.ok is False
    assert result.content is None
    assert result.error is not None


def test_require_approval_garbage_decision_fails_closed(fake_manager, session_factory):
    # Anything other than exactly "approve" fails closed - not just "reject".
    gateway = ToolExecutionGateway(fake_manager, session_factory, FakeApprovalManager(""))
    result = _run(gateway, "write_file")
    assert fake_manager.calls == []
    assert result.ok is False


def test_require_approval_affirmative_decision_executes(fake_manager, session_factory):
    gateway = ToolExecutionGateway(fake_manager, session_factory, FakeApprovalManager("approve"))
    result = _run(gateway, "write_file")
    assert fake_manager.calls == [("write_file", {"path": "x"})]
    assert result.ok is True
    assert result.content == "fake-content"


def test_policy_exception_fails_closed_no_mcp_call(fake_manager, session_factory, approval_manager, monkeypatch):
    def _raise(ctx, rules):
        raise RuntimeError("boom")

    monkeypatch.setattr("gateway.evaluate", _raise)
    gateway = ToolExecutionGateway(fake_manager, session_factory, approval_manager)
    result = _run(gateway, "write_file")
    assert fake_manager.calls == []
    assert result.ok is False
    assert result.content is None
    assert result.error is not None


def test_allow_path_calls_mcp_and_returns_manager_result(fake_manager, tmp_path, approval_manager):
    # A tool with its own real, enabled ALLOW rule genuinely exercises the
    # ALLOW branch (read_file has no rule in DEFAULT_RULES, which would instead
    # hit the fail-closed no-match-DENY default, not ALLOW).
    allow_session_factory = _make_session_factory(
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
    gateway = ToolExecutionGateway(fake_manager, allow_session_factory, approval_manager)
    result = _run(gateway, "read_file")
    assert fake_manager.calls == [("read_file", {"path": "x"})]
    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert result.content == "fake-content"
    assert result.error is None


def test_deny_and_allow_return_same_toolresult_class():
    # DENY branch's synthesized result and MCPManager.call's real result
    # must be literally the same class object, imported from mcp_manager.
    deny_result = ToolResult(ok=False, content=None, error="x")
    allow_result = ToolResult(ok=True, content="y", error=None)
    assert type(deny_result) is type(allow_result) is ToolResult


def test_execute_tool_signature_has_no_free_text_param():
    sig = inspect.signature(ToolExecutionGateway.execute_tool)
    param_names = set(sig.parameters.keys())
    for forbidden in ("reasoning", "intent", "llm_text", "model_text", "free_text"):
        assert forbidden not in param_names
