"""safety_gate — deterministic exercise safety check against injury_status.

Flow:
  1. Canonicalize the input exercise name (so aliases hit the ban list).
  2. Query the CURRENT injury_status rows (latest per location; a current
     `resolved` row lifts that location, older rows are history) that are
     active/resolving/chronic_baseline and whose
     contraindicated_exercises ban that identity or any name covering it
     (exercise_catalog.ban_match_names): a pre-split legacy name ("Bench
     Press" stored before "Dumbbell Bench Press" became its own identity)
     still blocks every identity it used to cover, and a generic query
     ("Bench Press") is blocked by a ban on any of its variants. Stored
     entries are matched case/whitespace-insensitively and by their current
     canonical identity, so a ban stored as the user said it ("skull
     crusher") still blocks the canonical query ("Skull Crusher").
  3. If a match: unsafe + the matching rows' safe_alternatives, merged and
     filtered so none is itself banned (empty list + `message` if none survive). The caller
     MUST NOT suggest the exercise; offer the alternatives.
  4. Else: safe. When the name itself didn't map to a catalog entry, the
     reason says so — an unmapped name may be a misspelling of a banned
     exercise, so the coach confirms with the user (fail-open residual,
     adversarial F1 / audit Q4).

Contract: deterministic, <10ms. Never assumes tendon state — only the
injury_status table is the source of truth. An empty table ⇒ everything safe.
"""

from __future__ import annotations

from models import SafetyResult, canonicalize
from models.exercise_catalog import ban_match_names

from .injuries import contraindication_hits, unbanned


def check_exercise_safety(exercise: str) -> SafetyResult:
    can_name, _mg, needs_review = canonicalize(exercise)
    # The identity plus every name whose ban covers it across the catalog's
    # identity split (legacy merged names, generic names) — fail-closed.
    rows = contraindication_hits(ban_match_names(can_name))
    if not rows:
        reason = "no active contraindication"
        if needs_review:
            reason += (" — exercise name not in the catalog; confirm the spelling "
                       "and the injury ban list with the user before suggesting")
        return SafetyResult(exercise=can_name, safe=True, reason=reason)
    location, status, _alts = rows[0]
    # Alternatives are merged from EVERY row that bans this exercise (stable:
    # row order, then list order) and then filtered through the gate against
    # all current bans — a banned alternative is never offered (P33).
    alts = unbanned(a for _l, _s, row_alts in rows for a in row_alts)
    return SafetyResult(
        exercise=can_name,
        safe=False,
        alternatives=alts,
        reason=f"{location}: {status} — exercise contraindicated",
        message="" if alts else ("no safe alternative is on file for this exercise "
                                 "— do not suggest it; ask the user or offer a different movement pattern"),
    )
