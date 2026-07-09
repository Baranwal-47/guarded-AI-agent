"""Policy Engine tests — precedence, fail-closed, structured-args-only.

Covers every bullet in 01-02-PLAN.md Task 2 <behavior>.
"""

import dataclasses

from policy_engine import Action, PolicyContext, Rule, evaluate, load_rules


def make_ctx(**overrides):
    defaults = dict(
        tool_name="write_file",
        server_name="sandbox-file-manager",
        arguments={},
        conversation_id="conv-1",
        current_token_usage=0,
    )
    defaults.update(overrides)
    return PolicyContext(**defaults)


def test_precedence_conflict_deny_wins():
    rules = [
        Rule(id="A", rule_type="require_approval", tool_name="delete_file",
             condition={}, action=Action.REQUIRE_APPROVAL, enabled=True),
        Rule(id="B", rule_type="block_tool", tool_name="delete_file",
             condition={}, action=Action.DENY, enabled=True),
    ]
    decision = evaluate(make_ctx(tool_name="delete_file"), rules)
    assert decision.action == Action.DENY
    assert set(decision.matched_rule_ids) == {"A", "B"}


def test_precedence_conflict_deny_wins_reversed_order():
    """Order-independence: reversing the rule list yields the same DENY."""
    rules = [
        Rule(id="B", rule_type="block_tool", tool_name="delete_file",
             condition={}, action=Action.DENY, enabled=True),
        Rule(id="A", rule_type="require_approval", tool_name="delete_file",
             condition={}, action=Action.REQUIRE_APPROVAL, enabled=True),
    ]
    decision = evaluate(make_ctx(tool_name="delete_file"), rules)
    assert decision.action == Action.DENY
    assert set(decision.matched_rule_ids) == {"A", "B"}


def test_require_approval_only_match():
    rules = [
        Rule(id="C", rule_type="require_approval", tool_name="write_file",
             condition={}, action=Action.REQUIRE_APPROVAL, enabled=True),
    ]
    decision = evaluate(make_ctx(tool_name="write_file"), rules)
    assert decision.action == Action.REQUIRE_APPROVAL
    assert decision.matched_rule_ids == ["C"]


def test_input_validation_violation_blocks():
    rules = [
        Rule(id="D", rule_type="input_validation", tool_name="write_file",
             condition={"prefix": "reports/", "arg": "path"}, action=Action.DENY, enabled=True),
    ]
    decision = evaluate(make_ctx(tool_name="write_file", arguments={"path": "secrets/x.txt"}), rules)
    assert decision.action == Action.DENY
    assert "D" in decision.matched_rule_ids
    assert "D" in decision.reason


def test_input_validation_compliant_does_not_block():
    rules = [
        Rule(id="D", rule_type="input_validation", tool_name="write_file",
             condition={"prefix": "reports/", "arg": "path"}, action=Action.DENY, enabled=True),
    ]
    decision = evaluate(make_ctx(tool_name="write_file", arguments={"path": "reports/q1.txt"}), rules)
    # Rule D specifically does not match/block. No other rule exists, so the
    # overall result is the fail-closed default (never implicit ALLOW).
    assert "D" not in decision.matched_rule_ids
    assert decision.action != Action.ALLOW


def test_token_budget_over_limit_blocks():
    rules = [
        Rule(id="E", rule_type="token_budget", tool_name="resolve-library-id",
             condition={"max_tokens": 100}, action=Action.REQUIRE_APPROVAL, enabled=True),
    ]
    decision = evaluate(make_ctx(tool_name="resolve-library-id", current_token_usage=150), rules)
    assert decision.action == Action.REQUIRE_APPROVAL
    assert "E" in decision.matched_rule_ids


def test_token_budget_under_limit_does_not_block():
    rules = [
        Rule(id="E", rule_type="token_budget", tool_name="resolve-library-id",
             condition={"max_tokens": 100}, action=Action.REQUIRE_APPROVAL, enabled=True),
    ]
    decision = evaluate(make_ctx(tool_name="resolve-library-id", current_token_usage=50), rules)
    assert "E" not in decision.matched_rule_ids
    assert decision.action != Action.ALLOW


def test_zero_matching_rules_fail_closed_never_allow():
    rules = [
        Rule(id="Z", rule_type="block_tool", tool_name="delete_file",
             condition={}, action=Action.DENY, enabled=True),
    ]
    decision = evaluate(make_ctx(tool_name="list_files"), rules)
    assert decision.action in (Action.DENY, Action.REQUIRE_APPROVAL)
    assert decision.action != Action.ALLOW
    assert decision.matched_rule_ids == []


def test_policy_context_has_no_free_text_field():
    """POLICY-01 / Pitfall 5: no reasoning/user_intent/llm_text field may exist."""
    fields = {f.name for f in dataclasses.fields(PolicyContext)}
    assert fields == {"tool_name", "server_name", "arguments", "conversation_id", "current_token_usage"}
    for forbidden in ("reasoning", "user_intent", "llm_text", "text", "message"):
        assert forbidden not in fields


def test_malformed_rule_condition_fails_closed_not_raises():
    """A rule with a malformed condition (missing 'prefix') must not raise
    out of evaluate() — it's caught and treated as a fail-closed match."""
    rules = [
        Rule(id="M", rule_type="input_validation", tool_name="write_file",
             condition={}, action=Action.ALLOW, enabled=True),
    ]
    decision = evaluate(make_ctx(tool_name="write_file", arguments={"path": "x"}), rules)
    assert decision.action == Action.DENY
    assert "M" in decision.matched_rule_ids


def test_disabled_rule_never_matches():
    rules = [
        Rule(id="N", rule_type="block_tool", tool_name="delete_file",
             condition={}, action=Action.DENY, enabled=False),
    ]
    decision = evaluate(make_ctx(tool_name="delete_file"), rules)
    assert "N" not in decision.matched_rule_ids
    assert decision.action != Action.ALLOW


def test_load_rules_reads_seed_yaml_and_conflict_resolves_to_deny():
    rules = load_rules("policy_rules.yaml")
    decision = evaluate(make_ctx(tool_name="delete_file", server_name="sandbox-file-manager"), rules)
    assert decision.action == Action.DENY
    assert len(decision.matched_rule_ids) >= 2
