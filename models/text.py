"""Input-boundary checks for free text (P39, P40).

Applies to what a caller sends: exercise names, notes, tempo, post_feedback and
the bodyweight scale / notes. Readers keep tolerating whatever is already stored.
"""

from __future__ import annotations

import unicodedata

MAX_NAME_LEN = 200
# Free text (notes, post_feedback, bodyweight scale/notes) may carry line breaks:
# newline, carriage return (Windows CRLF line endings) and tab. Names and tempo
# are single-line identifiers and allow no control character at all (P57).
FREE_TEXT_CONTROL = (chr(10), chr(13), chr(9))


def clean_text(v: str | None, what: str, *, allowed: tuple[str, ...] = FREE_TEXT_CONTROL) -> str | None:
    """Reject text DuckDB or a client could not carry: a lone surrogate (cannot
    be encoded as UTF-8) and control characters other than `allowed` (default:
    newline, carriage return, tab; pass `allowed=()` for a single-line field)."""
    if v is None:
        return v
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{what} contains a character that cannot be encoded as UTF-8 "
                         "(a lone surrogate); resend it as valid text") from None
    for ch in v:
        if ch not in allowed and unicodedata.category(ch) == "Cc":
            ok = ("only newline, carriage return and tab are allowed" if allowed
                  else "no control characters are allowed in this field")
            raise ValueError(f"{what} contains a control character (U+{ord(ch):04X}); {ok}")
    return v


def clean_name(v: str) -> str:
    """An exercise name: non-blank after strip, at most MAX_NAME_LEN characters."""
    if len(v) > MAX_NAME_LEN:
        raise ValueError(f"exercise name is too long ({len(v)} characters; "
                         f"at most {MAX_NAME_LEN})")
    clean_text(v, "exercise name", allowed=())
    if not v.strip():
        raise ValueError("exercise name must not be blank")
    return v
