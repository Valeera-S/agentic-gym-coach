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
  4. Else, a name the catalog does not recognize (a typo, a non-English name:
     "卧推") cannot be checked against the bans, so it FAILS CLOSED while any
     ban is active: unsafe, no alternatives, `message` tells the coach to
     restate it with its catalog (English) identity (P76). With no active ban
     nothing could be hiding, so it stays safe (+ the review suffix).
  5. Else: safe.

Contract: deterministic, <10ms. Never assumes tendon state — only the
injury_status table is the source of truth. An empty table ⇒ everything safe.
"""

from __future__ import annotations

from models import SafetyResult, canonicalize
from models.exercise_catalog import ban_match_names, resolve_name
from models.text import clean_name

from .injuries import contraindication_hits, has_active_bans, unbanned


def check_exercise_safety(exercise: str) -> SafetyResult:
    # a blank or malformed name is not a query: invalid_input, never "safe" (P66, P70)
    clean_name(exercise)
    can_name, _mg, needs_review = canonicalize(exercise)
    # "Recognized" = a catalog identity/alias. A keyword guess ("Bench Pres") is
    # NOT recognition: it may be a misspelling of a banned exercise.
    recognized = resolve_name(exercise) is not None
    # The identity plus every name whose ban covers it across the catalog's
    # identity split (legacy merged names, generic names) — fail-closed.
    rows = contraindication_hits(ban_match_names(can_name))
    if not rows and not recognized and has_active_bans():
        return SafetyResult(
            exercise=can_name,
            safe=False,
            reason=("exercise name not recognized, so it cannot be checked against the "
                    "active contraindications"),
            message=("restate the exercise with its catalog (English) identity and check "
                     "again; do not suggest it until then"),
        )
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
    # An unrecognized alternative could not pass this gate itself (it fails
    # closed while bans are active), so it is never offered either (P76).
    alts = unbanned(a for _l, _s, row_alts in rows for a in row_alts
                    if resolve_name(a) is not None)
    return SafetyResult(
        exercise=can_name,
        safe=False,
        alternatives=alts,
        reason=f"{location}: {status} — exercise contraindicated",
        message="" if alts else ("no safe alternative is on file for this exercise "
                                 "— do not suggest it; ask the user or offer a different movement pattern"),
    )
