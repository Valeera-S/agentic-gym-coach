"""MCP server — exposes the coach tools over stdio for ANY MCP-capable runtime.

Claude Code, ZCode, Cursor, Codex CLI, Continue, etc. attach this
one server instead of per-runtime tool glue. It wraps the SAME handlers as
the CLI dispatcher (coach_tools.py) — change handlers there, never here.
Signature defaults are imported from coach_tools so they cannot drift.

Plus `coach_doctrine(topic)`: procedural disclosure over MCP — returns the
routing table, or a knowledge file's content, for runtimes that cannot read
the repo's files directly.

Run:    python mcp_server.py          (stdio transport)
Config: see docs/adapters.md and .mcp.json
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mcp.server.mcpserver import MCPServer  # noqa: E402  (mcp 2.x: FastMCP → MCPServer)

import coach_tools  # noqa: E402

mcp = MCPServer(name="gym-coach")

# Bounded chapter delivery — routers stay light, content arrives on demand.
_MAX_DOCTRINE_CHARS = 14_000
_KNOWLEDGE = ROOT / "docs" / "knowledge"

# Book + edition of each vendored knowledge base, stamped on every doctrine
# response so a cited number never travels without the edition it came from.
# Copied from each SKILL.md header (tests/test_doctrine_tool.py checks they
# agree). The nutrition book is the 2015 first edition while the training book
# is the current 2nd edition — the caution makes that visible to the Coach.
_BOOK_SOURCES: dict[str, str] = {
    "helms-training-pyramid": (
        "The Muscle & Strength Pyramid: Training, 2nd edition "
        "(Helms, Morgan & Valdez)"
    ),
    "helms-nutrition-pyramid": (
        "The Muscle & Strength Nutrition Pyramid, 1st edition (v1.0, 2015; "
        "Helms, Morgan & Valdez). CAUTION: a 2nd edition may revise these "
        "recommendations — say so when citing its numbers"
    ),
}
# The docstring has always promised these two shorthands; they now resolve.
_TOPIC_ALIASES = {
    "training": "helms-training-pyramid/SKILL.md",
    "nutrition": "helms-nutrition-pyramid/SKILL.md",
}


def _book_source(skill_dir: str) -> str:
    return _BOOK_SOURCES.get(skill_dir, f"{skill_dir} — edition not recorded")


def _run(cmd: str, args: dict) -> dict:
    """Dispatch to the shared handler; preserve the halt-and-report posture
    with the documented three-code error contract (see coach_tools docstring)."""
    try:
        fn = coach_tools.DISPATCH[cmd]
    except KeyError:
        return {"error": "invalid_input", "exception": "UnknownCommand",
                "detail": f"unknown command '{cmd}'"}
    try:
        return fn(args)
    except Exception as e:  # surface the failure, never fabricate
        return coach_tools.error_payload(e)


@mcp.tool()
def coach_log_session(date: str, exercises: list[dict], phase: str | None = None,
                      post_feedback: str | None = None,
                      pre_recovery_score: int | None = None,
                      kind: str = coach_tools.DEFAULT_SESSION_KIND) -> dict:
    """Persist a session (validated, canonicalized, anomaly-flagged).

    kind: 'training' (default) | 'habit' — a standing daily item done outside
    training (e.g. 60 bodyweight squats every morning): it counts toward volume
    but never toward recovery's training load, the session-gap signal, deload
    block state or the phase fallback (pain logged in a habit still lowers recovery).

    exercises: [{name, sets, reps[], rpe[], weight_kg[] | (weight[] + unit), load_type?, tempo?,
    form_quality?, pain_flag?, notes?}] — arrays are per-set, equal length; weight_kg null =
    bodyweight/unrecorded. Instead of weight_kg send the numbers as read off the equipment:
    weight[] + unit ('kg' | 'lb'; 1 lb = 0.45359237 kg), never both. load_type: per_hand
    (dumbbells) | per_side (twin-stack cable) | total (bar / plate-loaded) | machine_stack |
    bodyweight — how the reading was taken. per_hand / per_side mean BOTH limbs each lift that
    load (tonnage counts it twice); a single-arm or one-side movement uses total.
    """
    return _run("log_session", {"date": date, "exercises": exercises,
                                "phase": phase, "post_feedback": post_feedback,
                                "pre_recovery_score": pre_recovery_score, "kind": kind})


@mcp.tool()
def coach_safety_check(exercise: str) -> dict:
    """Deterministic injury gate: safe/unsafe + alternatives. If safe=false, the exercise MUST NOT be suggested."""
    return _run("safety_check", {"exercise": exercise})


@mcp.tool()
def coach_recovery(date: str | None = None) -> dict:
    """Heuristic 0-100 pre-session recovery score for a date (<60 deload, <80 autoregulate, <100 may train, 100 push-ready). Reads only days BEFORE the date — a session already logged that day is excluded (excludes_query_date: true)."""
    return _run("recovery", {"date": date})


@mcp.tool()
def coach_trend(muscle: str,
                window_days: int = coach_tools.DEFAULT_TREND_WINDOW_DAYS,
                end_date: str | None = None) -> dict:
    """Effective hard sets, avg RPE, est 1RM (≤6-rep sets), trend direction (judged per exercise identity, counting only exercises for which the muscle is a chart primary: identities with heavy (<=6-rep) sets in both window halves vote on est-1RM, otherwise direction falls to each exercise's own performance: more load at >= the same reps, more reps at the same load, or a load step whose heavier sets all stay >= 6 reps without a lower Epley estimate — HEURISTIC, never reported; bodyweight exercises compare by dominance only; unlogged weights are unknown, never 0; detail.direction_basis and detail.identity_directions show which basis and which exercises decided), and stall flag for a muscle group over a window (detail block: tonnage = total external load, x2 for per_hand/per_side readings; load_type_unknown_sets; unloaded/overlap sets)."""
    return _run("trend", {"muscle": muscle, "window_days": window_days, "end_date": end_date})


@mcp.tool()
def coach_snapshot() -> dict:
    """Compute the 4-week phase snapshot and UPSERT today's phase_snapshots row (re-running rewrites today's anchor; incl. block_state: time since last deload, and session_gap: weeks since the last logged training session (habit sessions excluded))."""
    return _run("snapshot", {})


@mcp.tool()
def coach_intake_status() -> dict:
    """Run the standardized intake scan: collected vs missing bucket-list fields (each citing its book source), per-domain readiness (training/nutrition soft gates), and weeks since the last logged training session (habit sessions excluded). Read-only — ask the user only for what's missing."""
    return _run("intake_status", {})


@mcp.tool()
def coach_sessions(limit: int = coach_tools.DEFAULT_SESSIONS_LIMIT) -> dict:
    """List recent sessions as {sessions, count} (each: id, date, phase, pre_recovery_score, post_feedback, kind, needs_review = number of exercises still needing review). Lean by design — use coach_session_detail for the exercises."""
    return _run("sessions", {"limit": limit})


@mcp.tool()
def coach_session_detail(session_id: str | None = None, date: str | None = None) -> dict:
    """Read back logged session(s) in full — give exactly ONE of session_id or date (a date may hold several sessions).

    Returns {sessions: [...]}: every exercise with its `index` (the handle coach_session_amend takes),
    its identity and raw_name as typed, sets/reps/rpe,
    weight_kg plus the user's own numbers (weight_as_entered + unit_as_entered), load_type
    (load_type_unknown when not recorded), muscle_group + muscle_source, and needs_review with the reason.
    Every stored entry is listed: a nameless one (older rows) has name null and needs_review.
    Unknown id or date -> invalid_input.
    """
    return _run("session_detail", {"session_id": session_id, "date": date})


@mcp.tool()
def coach_session_amend(session_id: str, date: str, exercises: list[dict],
                        phase: str | None = None, kind: str | None = None,
                        post_feedback: str | None = None,
                        pre_recovery_score: int | None = None,
                        clear: list[str] | None = None) -> dict:
    """Replace a logged session's date and exercises (same id). ONLY after showing the user the
    current entry (coach_session_detail) and the correction, and getting their explicit yes.

    date + exercises replace the stored ones and are validated/canonicalized like a fresh log (exceptions below)
    (malformed input is rejected before any write). Every other field omitted OR null KEEPS its stored
    value (phase, kind, post_feedback, pre_recovery_score) — pass one only to change it. To REMOVE a
    stored post_feedback / pre_recovery_score, name it in `clear` (e.g. clear=["pre_recovery_score"]).
    Exercises take the logging fields (name, sets, reps, rpe, weight_kg | weight + unit, load_type,
    tempo, form_quality, pain_flag, notes, muscle_group, confirm_muscle) plus EXACTLY ONE of
    `index` (the stored exercise it restates/edits, from coach_session_detail) or `new: true`;
    unreferenced stored exercises are removed and every one is listed in the reply's
    `removed_exercises` (index, name, ...; nameless older entries included). Read-back-only keys (raw_name, muscle_source,
    entered_weight, ...) -> invalid_input. An exercise restated unchanged keeps what was recorded
    (raw name, weight + unit, load_type, muscle provenance; an older entry whose name the catalog
    now maps to another identity is canonicalized, with that name kept as its raw_name). Omit
    muscle_group to re-derive it (fixes an old guess); confirm_muscle=true records it as the user's;
    on a rename (same index, different exercise) a muscle_group equal to the old exercise's (even
    one the user had set; confirm_muscle=true keeps it, a different muscle_group is your own
    choice), or any load_type copied from it, is not applied (flagged amend_not_applied where
    that changes the result).
    Returns the session-level values now stored.
    The complete previous version is written to the audit trail. Unknown id -> invalid_input.
    """
    return _run("session_amend", {"session_id": session_id, "date": date, "exercises": exercises,
                                  "phase": phase, "kind": kind, "post_feedback": post_feedback,
                                  "pre_recovery_score": pre_recovery_score, "clear": clear or []})


@mcp.tool()
def coach_session_delete(session_id: str) -> dict:
    """Permanently delete one logged session. ONLY after echoing the session (coach_session_detail)
    to the user and getting their explicit confirmation. Its complete row is written to the audit
    trail first. Unknown id -> invalid_input."""
    return _run("session_delete", {"session_id": session_id})


@mcp.tool()
def coach_bodyweight_log(date: str, condition: str, weight: float | None = None,
                         unit: str | None = None, weight_kg: float | None = None,
                         scale: str | None = None, notes: str | None = None) -> dict:
    """Record one bodyweight reading (several per date are fine). `condition` is REQUIRED:
    morning_fasted | fed | post_workout | unknown (unknown only when the user truly doesn't know).
    Send the number as read off the scale: weight + unit ('kg' | 'lb'; 1 lb = 0.45359237 kg), or weight_kg.
    Bounds 0 < kg <= 400. scale = free text (e.g. "test scale"). Does NOT change profile.bodyweight_kg."""
    return _run("bodyweight_log", {"date": date, "condition": condition, "weight": weight,
                                   "unit": unit, "weight_kg": weight_kg, "scale": scale,
                                   "notes": notes})


@mcp.tool()
def coach_bodyweight_history(window_days: int = coach_tools.DEFAULT_BODYWEIGHT_WINDOW_DAYS,
                             end_date: str | None = None) -> dict:
    """Bodyweight readings over a window (window_days calendar days ending at end_date inclusive, default today)
    plus, PER CONDITION, the mean, the reading count (`readings`) and the number of distinct days behind it (`days`; the mean is of daily values) for the last 7 days and for the whole window.
    Conditions are never mixed in one average - compare like with like (Nutrition ch02 weekly averages)."""
    return _run("bodyweight_history", {"window_days": window_days, "end_date": end_date})


@mcp.tool()
def coach_injuries_list() -> dict:
    """Read the injury_status table as {injuries, count} (each: location, status, severity, contraindications, alternatives)."""
    return _run("injuries_list", {})


@mcp.tool()
def coach_injuries_seed(location: str, status: str, severity: int,
                        contraindicated_exercises: list[str] | None = None,
                        safe_alternatives: list[str] | None = None) -> dict:
    """Insert an injury record. Only when the user explicitly reports a new injury or state change.

    Exercise names are canonicalized against the catalog; unmapped names are
    stored verbatim and returned in `needs_review` — confirm them with the user.
    """
    return _run("injuries_seed", {"location": location, "status": status, "severity": severity,
                                  "contraindicated_exercises": contraindicated_exercises or [],
                                  "safe_alternatives": safe_alternatives or []})


@mcp.tool()
def coach_profile_get() -> dict:
    """Get the user profile (goals, training age, schedule, priorities). {profile: null} ⇒ the intake scan reports everything missing."""
    return _run("profile_get", {})


@mcp.tool()
def coach_profile_set(profile: dict) -> dict:
    """Create/update the user profile (validated, versioned; goal changes audited). Echo it to the user after setting. An all-empty profile is refused. An unknown field (also inside goals / weekly_availability) is invalid_input, never dropped; `updated_at` from coach_profile_get is accepted and ignored."""
    return _run("profile_set", profile)


@mcp.tool()
def coach_memory_save(text: str,
                      kind: str = coach_tools.DEFAULT_MEMORY_KIND,
                      tags: list[str] | None = None) -> dict:
    """Save a long-term memory note. ONLY on an explicit user command ('save this') — never auto-write."""
    return _run("memory_save", {"text": text, "kind": kind, "tags": tags or []})


@mcp.tool()
def coach_memory_search(query: str | None = None, tags: list[str] | None = None,
                        limit: int = coach_tools.DEFAULT_SEARCH_LIMIT) -> dict:
    """Search long-term memory notes (substring AND any-tag, newest first); returns {notes, count}."""
    return _run("memory_search", {"query": query, "tags": tags, "limit": limit})


@mcp.tool()
def coach_doctrine(topic: str | None = None) -> str:
    """Procedural disclosure over MCP: list the knowledge routing table, or return a knowledge file's content.

    Every response names the book and edition it comes from; the nutrition book
    is the 1st edition (2015) and is flagged as possibly revised by its 2nd.

    topic: 'index' (default) | 'training' | 'nutrition' | a file path relative
    to docs/knowledge/ (e.g. 'helms-training-pyramid/chapters/ch04-level-3-progression.md').
    """
    if topic in (None, "", "index"):
        lines = ["Knowledge routing (docs/knowledge/):"]
        for skill_dir in sorted(_KNOWLEDGE.iterdir()):
            if skill_dir.is_dir():
                lines.append(f"  {skill_dir.name}/ — {skill_dir.name}/SKILL.md (chapter + topic index)")
                lines.append(f"      book: {_book_source(skill_dir.name)}")
        lines.append("Pass a relative path (e.g. 'helms-training-pyramid/chapters/ch03-...md') to fetch content.")
        return "\n".join(lines)
    candidate = (_KNOWLEDGE / _TOPIC_ALIASES.get(topic, topic)).resolve()
    if not candidate.is_relative_to(_KNOWLEDGE) or not candidate.is_file():
        return f"error: unknown doctrine topic '{topic}' — call with 'index' first"
    text = candidate.read_text(encoding="utf-8")
    if len(text) > _MAX_DOCTRINE_CHARS:
        text = text[:_MAX_DOCTRINE_CHARS] + "\n...[truncated — read the file directly for the rest]"
    skill_dir = candidate.relative_to(_KNOWLEDGE).parts[0]
    return f"[Source: {_book_source(skill_dir)}]\n\n{text}"


if __name__ == "__main__":
    mcp.run()
