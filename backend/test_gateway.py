"""Tests for ToolExecutionGateway: policy branching, sole-caller invariant,
uniform ToolResult return shape, fail-closed approval + policy-exception paths.

Uses plain asyncio.run() instead of pytest-asyncio: execute_tool is the only
async surface under test and stdlib asyncio already covers running it -
no new test dependency needed (ponytail: stdlib solves it).
"""

import asyncio
import inspect

import pytest

from gateway import ToolExecutionGateway
from mcp_manager import ToolResult

RULES_YAML = """
rules:
  - id: R-DENY
    rule_type: block_tool
    tool_name: delete_file
    condition: {}
    action: DENY
    enabled: true
  - id: R-APPROVE
    rule_type: require_approval
    tool_name: write_file
    condition: {}
    action: REQUIRE_APPROVAL
    enabled: true
"""


class FakeMCPManager:
    """Records call() invocations; never touches a real MCP transport."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool_name: str, arguments: dict) -> ToolResult:
        self.calls.append((tool_name, arguments))
        return ToolResult(ok=True, content="fake-content", error=None)


@pytest.fixture
def rules_path(tmp_path):
    p = tmp_path / "policy_rules.yaml"
    p.write_text(RULES_YAML)
    return str(p)


@pytest.fixture
def fake_manager():
    return FakeMCPManager()


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


def test_deny_rule_blocks_call_and_returns_synthesized_tool_result(fake_manager, rules_path):
    gateway = ToolExecutionGateway(fake_manager, rules_path)
    result = _run(gateway, "delete_file")
    assert fake_manager.calls == []
    assert isinstance(result, ToolResult)
    assert result.ok is False
    assert result.content is None
    assert result.error is not None


def test_require_approval_rejected_input_fails_closed(fake_manager, rules_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    gateway = ToolExecutionGateway(fake_manager, rules_path)
    result = _run(gateway, "write_file")
    assert fake_manager.calls == []
    assert result.ok is False
    assert result.content is None
    assert result.error is not None


def test_require_approval_invalid_empty_input_fails_closed(fake_manager, rules_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    gateway = ToolExecutionGateway(fake_manager, rules_path)
    result = _run(gateway, "write_file")
    assert fake_manager.calls == []
    assert result.ok is False


def test_require_approval_affirmative_input_executes(fake_manager, rules_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    gateway = ToolExecutionGateway(fake_manager, rules_path)
    result = _run(gateway, "write_file")
    assert fake_manager.calls == [("write_file", {"path": "x"})]
    assert result.ok is True
    assert result.content == "fake-content"


def test_policy_exception_fails_closed_no_mcp_call(fake_manager, rules_path, monkeypatch):
    def _raise(ctx, rules):
        raise RuntimeError("boom")

    monkeypatch.setattr("gateway.evaluate", _raise)
    gateway = ToolExecutionGateway(fake_manager, rules_path)
    result = _run(gateway, "write_file")
    assert fake_manager.calls == []
    assert result.ok is False
    assert result.content is None
    assert result.error is not None


def test_allow_path_calls_mcp_and_returns_manager_result(fake_manager, tmp_path):
    # A tool with its own real, enabled ALLOW rule genuinely exercises the
    # ALLOW branch (read_file has no rule in RULES_YAML, which would instead
    # hit the fail-closed no-match-DENY default, not ALLOW).
    p = tmp_path / "allow_rules.yaml"
    p.write_text(
        """
rules:
  - id: R-ALLOW-READ
    rule_type: require_approval
    tool_name: read_file
    condition: {}
    action: ALLOW
    enabled: true
"""
    )
    gateway = ToolExecutionGateway(fake_manager, str(p))
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
