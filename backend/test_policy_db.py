"""Tests for DB-sourced, fresh-read policy rules (POLICY-04).

Uses a throwaway SQLite file under `tmp_path` with its own engine/sessionmaker
+ `Base.metadata.create_all` — never touches the real DB. Driven by plain
`asyncio.run()`, matching test_gateway.py's convention (no pytest-asyncio).
"""

import asyncio
import inspect
import os

# db.py binds its engine to get_settings().database_url at import time, which
# requires GEMINI_API_KEY (no default) — set a dummy value before the first
# import, same pattern test_main.py uses via monkeypatch.setenv (02-01).
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db import Base
from models import PolicyRule
from policy_engine import Action, PolicyContext, evaluate, load_rules


def _session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def _create_all(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def test_load_rules_reads_fresh_from_db(tmp_path):
    session_factory, engine = _session_factory(tmp_path)

    async def _run():
        await _create_all(engine)
        async with session_factory() as session:
            session.add(
                PolicyRule(
                    id="R-TEST",
                    rule_type="block_tool",
                    tool_name="delete_file",
                    condition={},
                    action="DENY",
                    enabled=True,
                )
            )
            await session.commit()

            rules = await load_rules(session)
            assert len(rules) == 1
            rule = rules[0]
            assert rule.id == "R-TEST"
            assert rule.rule_type == "block_tool"
            assert rule.tool_name == "delete_file"
            assert rule.condition == {}
            assert rule.action is Action.DENY
            assert rule.enabled is True

    asyncio.run(_run())


def test_edited_rule_changes_next_evaluation_no_cache(tmp_path):
    session_factory, engine = _session_factory(tmp_path)

    async def _run():
        await _create_all(engine)
        async with session_factory() as session:
            session.add(
                PolicyRule(
                    id="R-DENY",
                    rule_type="block_tool",
                    tool_name="delete_file",
                    condition={},
                    action="DENY",
                    enabled=True,
                )
            )
            await session.commit()

            ctx = PolicyContext(
                tool_name="delete_file",
                server_name="sandbox",
                arguments={},
                conversation_id="c1",
                current_token_usage=0,
            )

            rules = await load_rules(session)
            decision = evaluate(ctx, rules)
            assert decision.action is Action.DENY

            # Live edit, no restart, no cache invalidation call.
            row = await session.get(PolicyRule, "R-DENY")
            row.enabled = False
            await session.commit()

            rules_after = await load_rules(session)
            decision_after = evaluate(ctx, rules_after)
            assert decision_after.action is not Action.DENY

    asyncio.run(_run())


def test_load_rules_is_async_and_takes_session():
    assert inspect.iscoroutinefunction(load_rules)
    sig = inspect.signature(load_rules)
    params = list(sig.parameters.keys())
    assert len(params) == 1
    assert params[0] != "path"
