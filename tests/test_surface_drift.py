"""Surface-drift guard (audit T5c) — AGENTS.md's three-places rule has no
manual check, and both drift incidents found in the tree (ghost coach_ingest,
wrong recovery bands in a docstring) entered through the persona/docstring
side. These tests make that drift fail CI.
"""

import inspect
import re
from pathlib import Path

import coach_tools
import mcp_server

_REPO = Path(__file__).resolve().parent.parent


def test_every_dispatch_command_has_mcp_wrapper():
    for cmd in coach_tools.DISPATCH:
        assert hasattr(mcp_server, f"coach_{cmd}"), cmd


def test_mcp_wrapper_defaults_come_from_coach_tools():
    """Defaults are single-sourced in coach_tools; wrappers import them."""
    cases = [
        ("coach_trend", "window_days", coach_tools.DEFAULT_TREND_WINDOW_DAYS),
        ("coach_bodyweight_history", "window_days", coach_tools.DEFAULT_BODYWEIGHT_WINDOW_DAYS),
        ("coach_sessions", "limit", coach_tools.DEFAULT_SESSIONS_LIMIT),
        ("coach_memory_search", "limit", coach_tools.DEFAULT_SEARCH_LIMIT),
        ("coach_memory_save", "kind", coach_tools.DEFAULT_MEMORY_KIND),
        ("coach_log_session", "kind", coach_tools.DEFAULT_SESSION_KIND),
    ]
    for tool, param, expected in cases:
        default = inspect.signature(getattr(mcp_server, tool)).parameters[param].default
        assert default == expected, f"{tool}.{param} drifted from coach_tools"


def test_recovery_docstring_matches_code_bands():
    # M2: the docstring once taught "0-59 rest, 60-84 light, 85+ train".
    doc = inspect.getdoc(mcp_server.coach_recovery) or ""
    assert "deload" in doc and "autoregulate" in doc and "push-ready" in doc
    assert "60-84" not in doc and "85+" not in doc


def test_persona_documents_only_real_tools():
    text = (_REPO / "docs" / "COACH_PROMPT.md").read_text(encoding="utf-8")
    assert "coach_ingest" not in text  # M1: the ghost tool must stay gone
    for name in sorted(set(re.findall(r"coach_[a-z_]+", text))):
        if name in ("coach_", "coach_doctrine"):  # generic mention / documented MCP-only asymmetry
            continue
        assert hasattr(mcp_server, name), f"persona references nonexistent tool {name}"


def test_persona_exposes_pre_recovery_score():
    # M3: the sessions.pre_recovery_score column was dead from the MCP surface.
    text = (_REPO / "docs" / "COACH_PROMPT.md").read_text(encoding="utf-8")
    assert "pre_recovery_score" in text
    sig = inspect.signature(mcp_server.coach_log_session)
    assert "pre_recovery_score" in sig.parameters


def test_coach_trend_description_states_the_current_direction_rule():
    import mcp_server
    desc = mcp_server.coach_trend.__doc__
    assert "per exercise identity" in desc and "chart primary" in desc
    assert "identity_directions" in desc
    assert "whenever heavy sets" not in desc and "when heavy sets exist in both halves" not in desc


# --- P72: tool counts stated in prose must track the real surface ------------

_COUNT_DOCS = ("AGENTS.md", "SPEC.md", "docs/adapters.md")
_TOOL_COUNT = re.compile(r"\b(\d+)\s+(?:coach\s+)?tools\b", re.IGNORECASE)
_SUBCOMMAND_COUNT = re.compile(r"\b(\d+)\s+subcommands\b|\bsubcommands\s*\((\d+)\)", re.IGNORECASE)


def _mcp_coach_tools() -> set[str]:
    return {n for n in dir(mcp_server)
            if n.startswith("coach_") and n != "coach_doctrine" and callable(getattr(mcp_server, n))}


def _stated_counts(pattern: re.Pattern) -> list[tuple[str, int]]:
    found = []
    for rel in _COUNT_DOCS:
        text = (_REPO / rel).read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            found.append((rel, int(next(g for g in m.groups() if g))))
    return found


def test_mcp_wrappers_match_dispatch_one_to_one():
    assert _mcp_coach_tools() == {f"coach_{c}" for c in coach_tools.DISPATCH}


def test_documented_tool_counts_equal_the_real_surface():
    stated = _stated_counts(_TOOL_COUNT)
    assert stated, "no 'N tools' count found: the guard's pattern drifted from the docs"
    assert {rel for rel, _ in stated} == set(_COUNT_DOCS)  # each doc states it somewhere
    expected = len(_mcp_coach_tools())
    assert expected == len(coach_tools.DISPATCH)
    for rel, n in stated:
        assert n == expected, f"{rel} says {n} tools; the surface has {expected}"


def test_documented_subcommand_counts_equal_dispatch():
    stated = _stated_counts(_SUBCOMMAND_COUNT)
    assert stated, "no 'N subcommands' count found: the guard's pattern drifted from the docs"
    for rel, n in stated:
        assert n == len(coach_tools.DISPATCH), (
            f"{rel} says {n} subcommands; DISPATCH has {len(coach_tools.DISPATCH)}")


def test_persona_has_the_low_friction_logging_flow():
    # UX3: the label / template / habit-confirmation behavior lives in the persona only
    text = (_REPO / "docs" / "COACH_PROMPT.md").read_text(encoding="utf-8")
    start = text.index("## Logging with little effort")
    assert start < text.index("## Standardized intake assessment")
    section = text[start:text.index("\n## ", start + 1)]
    for needle in ("coach_session_labels", "coach_session_template", "coach_log_session",
                   "days_without_entry", "14 days", "never copied"):
        assert needle in section, needle
