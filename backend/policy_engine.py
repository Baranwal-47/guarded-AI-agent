"""Policy Engine — the real security boundary.

Pure module: evaluate(context, rules) -> PolicyDecision. No MCP, side
effects, or I/O beyond load_rules() reading rules. Consumes only structured
tool-call facts (PolicyContext) — never the model's free text (POLICY-01 /
Pitfall 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from models import PolicyRule

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class Action(Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


# Fixed precedence, most restrictive first (POLICY-02). Gather-all-then-reduce
# over this tuple — never first-match — so behavior is order-independent
# (Pitfall 6).
_PRECEDENCE = (Action.DENY, Action.REQUIRE_APPROVAL, Action.ALLOW)


@dataclass(frozen=True)
class PolicyContext:
    """Structured tool-call facts only. No reasoning/user_intent/llm_text
    field may ever be added here — that would reopen the prompt-injection
    bypass this module exists to close (POLICY-01)."""

    tool_name: str
    server_name: str
    arguments: dict[str, Any]
    conversation_id: str
    current_token_usage: int


@dataclass(frozen=True)
class PolicyDecision:
    action: Action
    reason: str
    matched_rule_ids: list[str]


@dataclass(frozen=True)
class Rule:
    id: str
    rule_type: str
    tool_name: str
    condition: dict[str, Any]
    action: Action
    enabled: bool


async def load_rules(session: "AsyncSession") -> list[Rule]:
    """Read rules fresh from the DB on every call — no cache/lru_cache.

    Rule content can change live (dashboard, Phase 2); this module must
    never memoize it (Pitfall 8 / T-01-02-STALE).
    """
    result = await session.execute(select(PolicyRule))
    return [
        Rule(
            id=row.id,
            rule_type=row.rule_type,
            tool_name=row.tool_name,
            condition=row.condition or {},
            action=Action[row.action],
            enabled=row.enabled,
        )
        for row in result.scalars()
    ]


def _matches(rule: Rule, ctx: PolicyContext) -> bool:
    if rule.tool_name != ctx.tool_name:
        return False
    if rule.rule_type in ("block_tool", "require_approval"):
        return True
    if rule.rule_type == "input_validation":
        arg_name = rule.condition.get("arg", "path")
        prefix = rule.condition["prefix"]  # missing key -> caught by caller, fail-closed
        value = ctx.arguments.get(arg_name, "")
        return not str(value).startswith(prefix)
    if rule.rule_type == "token_budget":
        return ctx.current_token_usage >= rule.condition["max_tokens"]
    return False


def evaluate(context: PolicyContext, rules: list[Rule]) -> PolicyDecision:
    """Gather every enabled matching rule, then reduce by fixed precedence.

    A rule whose match test raises (malformed condition) is treated as a
    matched, fail-closed (DENY) rule rather than propagating the exception.
    An empty matched set is also fail-closed DENY — never implicit ALLOW
    (POLICY-05).
    """
    matched: list[tuple[str, Action]] = []
    for rule in rules:
        if not rule.enabled:
            continue
        try:
            if _matches(rule, context):
                matched.append((rule.id, rule.action))
        except Exception:
            # Malformed condition etc. -> treat as a matched, fail-closed rule.
            matched.append((rule.id, Action.DENY))

    if not matched:
        return PolicyDecision(
            action=Action.DENY,
            reason="no matching rule (fail-closed default)",
            matched_rule_ids=[],
        )

    actions_present = {action for _, action in matched}
    winning_action = next(a for a in _PRECEDENCE if a in actions_present)
    matched_rule_ids = [rule_id for rule_id, _ in matched]
    reason = f"{winning_action.value}: matched rule(s) {matched_rule_ids}"
    return PolicyDecision(
        action=winning_action,
        reason=reason,
        matched_rule_ids=matched_rule_ids,
    )
