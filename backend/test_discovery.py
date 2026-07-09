"""Sanitizer unit tests (offline) + live MCP discovery/round-trip script.

Run `uv run pytest test_discovery.py -k sanitize -x -q` for the offline
sanitizer tests, or `uv run python test_discovery.py` for the live discovery
script that connects to both real MCP servers (network + subprocess required).
"""

import copy
import logging

from schema_sanitizer import sanitize_schema

DIRTY_SCHEMA = {
    "type": "object",
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Dirty",
    "additionalProperties": False,
    "description": "top level",
    "properties": {
        "path": {
            "type": "string",
            "pattern": "^[a-z]+$",
            "default": "foo",
            "description": "a path",
        },
        "nested": {
            "type": "object",
            "additionalProperties": False,
            "propertyNames": {"pattern": "^[a-z]+$"},
            "properties": {
                "inner": {
                    "oneOf": [{"type": "string"}, {"type": "number"}],
                },
            },
        },
    },
    "items": {
        "type": "string",
        "default": "x",
    },
    "required": ["path"],
    "enum": ["a", "b"],
}


def test_sanitize_removes_unsupported_keys_recursively():
    clean = sanitize_schema(DIRTY_SCHEMA)
    assert "$schema" not in clean
    assert "title" not in clean
    assert "additionalProperties" not in clean
    assert "pattern" not in clean["properties"]["path"]
    assert "default" not in clean["properties"]["path"]
    assert "additionalProperties" not in clean["properties"]["nested"]
    assert "propertyNames" not in clean["properties"]["nested"]
    assert "default" not in clean["items"]


def test_sanitize_renames_oneof_to_anyof():
    clean = sanitize_schema(DIRTY_SCHEMA)
    inner = clean["properties"]["nested"]["properties"]["inner"]
    assert "oneOf" not in inner
    assert inner["anyOf"] == [{"type": "string"}, {"type": "number"}]


def test_sanitize_preserves_supported_keys():
    clean = sanitize_schema(DIRTY_SCHEMA)
    assert clean["type"] == "object"
    assert clean["required"] == ["path"]
    assert clean["enum"] == ["a", "b"]
    assert clean["description"] == "top level"
    assert clean["properties"]["path"]["type"] == "string"
    assert clean["properties"]["path"]["description"] == "a path"
    assert clean["items"]["type"] == "string"


def test_sanitize_does_not_mutate_input():
    original = copy.deepcopy(DIRTY_SCHEMA)
    sanitize_schema(DIRTY_SCHEMA)
    assert DIRTY_SCHEMA == original


def test_sanitize_warns_on_stripped_pattern_and_default(caplog):
    with caplog.at_level(logging.WARNING):
        sanitize_schema(DIRTY_SCHEMA)
    messages = "\n".join(r.message for r in caplog.records)
    assert "pattern" in messages
    assert "default" in messages
