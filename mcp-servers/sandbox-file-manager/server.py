"""Sandbox File Manager MCP server (stdio transport).

Exposes 5 file-management tools, each confined to SANDBOX_ROOT by
`_resolve_within_sandbox`. This confinement is enforced server-side,
independent of any external policy engine (defense-in-depth, SANDBOX-02).
"""

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("sandbox-file-manager")

SANDBOX_ROOT = (Path(__file__).parent / "sandbox").resolve()
SANDBOX_ROOT.mkdir(exist_ok=True)


def _resolve_within_sandbox(path: str) -> Path:
    """Resolve `path` relative to SANDBOX_ROOT and reject any escape."""
    candidate = (SANDBOX_ROOT / path).resolve()
    if candidate != SANDBOX_ROOT and SANDBOX_ROOT not in candidate.parents:
        raise ValueError(f"path '{path}' is outside the sandbox root")
    return candidate


@mcp.tool()
def list_files(subdir: str = ".") -> str:
    """List file and directory names under the sandbox (or a subdirectory of it)."""
    try:
        target = _resolve_within_sandbox(subdir)
    except ValueError:
        return f"ERROR: path '{subdir}' is outside the sandbox root"
    if not target.exists():
        return f"ERROR: '{subdir}' does not exist"
    if not target.is_dir():
        return f"ERROR: '{subdir}' is not a directory"
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
    return "\n".join(entries) if entries else "(empty)"


@mcp.tool()
def read_file(path: str) -> str:
    """Read and return the text contents of a file inside the sandbox."""
    try:
        target = _resolve_within_sandbox(path)
    except ValueError:
        return f"ERROR: path '{path}' is outside the sandbox root"
    if not target.exists():
        return f"ERROR: '{path}' does not exist"
    if not target.is_file():
        return f"ERROR: '{path}' is not a file"
    try:
        return target.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"read_file failed for {path}: {exc}", file=sys.stderr)
        return f"ERROR: could not read '{path}'"


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write text content to a file inside the sandbox, creating or overwriting it."""
    try:
        target = _resolve_within_sandbox(path)
    except ValueError:
        return f"ERROR: path '{path}' is outside the sandbox root"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        print(f"write_file failed for {path}: {exc}", file=sys.stderr)
        return f"ERROR: could not write '{path}'"
    return f"OK: wrote {len(content)} bytes to '{path}'"


@mcp.tool()
def move_file(source: str, destination: str) -> str:
    """Move/rename a file within the sandbox. Both source and destination are confined."""
    try:
        src = _resolve_within_sandbox(source)
    except ValueError:
        return f"ERROR: path '{source}' is outside the sandbox root"
    try:
        dst = _resolve_within_sandbox(destination)
    except ValueError:
        return f"ERROR: path '{destination}' is outside the sandbox root"
    if not src.exists():
        return f"ERROR: '{source}' does not exist"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
    except OSError as exc:
        print(f"move_file failed for {source} -> {destination}: {exc}", file=sys.stderr)
        return f"ERROR: could not move '{source}' to '{destination}'"
    return f"OK: moved '{source}' to '{destination}'"


@mcp.tool()
def delete_file(path: str) -> str:
    """Delete a file inside the sandbox."""
    try:
        target = _resolve_within_sandbox(path)
    except ValueError:
        return f"ERROR: path '{path}' is outside the sandbox root"
    if not target.exists():
        return f"ERROR: '{path}' does not exist"
    if not target.is_file():
        return f"ERROR: '{path}' is not a file"
    try:
        target.unlink()
    except OSError as exc:
        print(f"delete_file failed for {path}: {exc}", file=sys.stderr)
        return f"ERROR: could not delete '{path}'"
    return f"OK: deleted '{path}'"


if __name__ == "__main__":
    mcp.run()
