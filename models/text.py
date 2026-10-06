"""Input-boundary checks for free text (P39, P40).

Applies to what a caller sends: exercise names, notes, tempo, post_feedback and
the bodyweight scale / notes. Readers keep tolerating whatever is already stored.
"""

from __future__ import annotations

import unicodedata

MAX_NAME_LEN = 200
_ALLOWED_CONTROL = (chr(10), chr(9))  # newline, tab


def clean_text(v: str | None, what: str) -> str | None:
    """Reject text DuckDB or a client could not carry: a lone surrogate (cannot
    be encoded as UTF-8) and control characters other than newline and tab."""
    if v is None:
        return v
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{what} contains a character that cannot be encoded as UTF-8 "
                         "(a lone surrogate); resend it as valid text") from None
    for ch in v:
        if ch not in _ALLOWED_CONTROL and unicodedata.category(ch) == "Cc":
            raise ValueError(f"{what} contains a control character (U+{ord(ch):04X}); "
                             "only newline and tab are allowed")
    return v


def clean_name(v: str) -> str:
    """An exercise name: non-blank after strip, at most MAX_NAME_LEN characters."""
    if len(v) > MAX_NAME_LEN:
        raise ValueError(f"exercise name is too long ({len(v)} characters; "
                         f"at most {MAX_NAME_LEN})")
    clean_text(v, "exercise name")
    if not v.strip():
        raise ValueError("exercise name must not be blank")
    return v
