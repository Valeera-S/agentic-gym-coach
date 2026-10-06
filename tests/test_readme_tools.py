"""README discovery guard — a user who reads README.md should learn about every
tool the MCP server registers. A new tool without a README mention fails here
(the three-places rule in AGENTS.md, plus the user-facing fourth)."""

import asyncio
import re
from pathlib import Path

import mcp_server

_README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")


def _section(title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", _README, re.S | re.M)
    assert m, f"README.md has no '## {title}' section"
    return m.group(1)


def test_every_registered_tool_is_named_in_what_the_coach_can_do():
    registered = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert registered, "no tools registered?"
    section = _section("What the coach can do")
    missing = sorted(n for n in registered if not re.search(rf"\b{n}\b", section))
    assert not missing, f"README 'What the coach can do' omits: {missing}"


def test_readme_names_no_nonexistent_tool():
    registered = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    named = set(re.findall(r"\bcoach_[a-z_]+\b", _section("What the coach can do")))
    assert named <= registered, sorted(named - registered)
