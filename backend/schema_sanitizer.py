"""Pure JSON-Schema sanitizer mapping MCP tool schemas onto Gemini's supported subset.

Gemini's function-calling schema hard-rejects (400s the whole tool-calling turn,
not just the one bad tool) several standard JSON Schema keywords MCP servers
commonly emit — see research/PITFALLS.md Pitfall 4. This module recursively
strips those keywords before a schema is used to build a Gemini
FunctionDeclaration, and renames `oneOf` to `anyOf` (Gemini already treats
`anyOf`/`oneOf` the same, silently loosening `oneOf`'s exclusivity semantics —
this makes that loosening explicit instead of leaving it to a runtime 400).
"""

import logging

logger = logging.getLogger(__name__)

_UNSUPPORTED_KEYS = frozenset(
    {"additionalProperties", "$schema", "title", "default", "pattern", "propertyNames"}
)
# Stripping these changes validation behavior (not just noise) — warn so it's visible.
_WARN_ON_STRIP = frozenset({"pattern", "default"})


def sanitize_schema(schema: dict, _path: str = "$") -> dict:
    """Return a new schema dict with Gemini-unsupported keys removed, recursively.

    Does not mutate the input. Recurses into `properties` (each value), `items`,
    `anyOf`/`oneOf`/`allOf` lists, and `$defs`. Renames `oneOf` -> `anyOf`.
    """
    result: dict = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_KEYS:
            if key in _WARN_ON_STRIP:
                logger.warning(
                    "sanitize_schema: stripped '%s' at %s (behavior change, not just noise)", key, _path
                )
            continue

        out_key = "anyOf" if key == "oneOf" else key

        if key == "properties" and isinstance(value, dict):
            result[out_key] = {
                prop_name: sanitize_schema(prop_schema, f"{_path}.properties.{prop_name}")
                if isinstance(prop_schema, dict)
                else prop_schema
                for prop_name, prop_schema in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            result[out_key] = sanitize_schema(value, f"{_path}.items")
        elif key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
            result[out_key] = [
                sanitize_schema(item, f"{_path}.{out_key}[{i}]") if isinstance(item, dict) else item
                for i, item in enumerate(value)
            ]
        elif key == "$defs" and isinstance(value, dict):
            result[out_key] = {
                def_name: sanitize_schema(def_schema, f"{_path}.$defs.{def_name}")
                if isinstance(def_schema, dict)
                else def_schema
                for def_name, def_schema in value.items()
            }
        else:
            result[out_key] = value

    return result
