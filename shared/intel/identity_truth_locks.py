"""
Canonical identity locks for reconciliation (project sync + learning dossier upserts).

OP01-001 was historically mis-labeled as Monkey D. Luffy; English Bandai authority
confirms Roronoa Zoro. Any pipeline that tries to re-assert Luffy at this code must
fail closed.
"""

from __future__ import annotations

FORBIDDEN_OP01_001_LUFFY = (
    "Refusing identity: OP01-001 is canonically Roronoa Zoro (English Bandai). "
    "Monkey D. Luffy is not a valid name for this code."
)


def is_luffy_identity_label(name: str | None) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    if "luffy" in n and "monkey" in n:
        return True
    if n in {"monkey d. luffy", "monkey d luffy"}:
        return True
    return False


def reject_forbidden_identity_name(canonical_code: str | None, name: str | None) -> None:
    code = (canonical_code or "").strip().upper()
    if code != "OP01-001":
        return
    if is_luffy_identity_label(name):
        raise ValueError(FORBIDDEN_OP01_001_LUFFY)
