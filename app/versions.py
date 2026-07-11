"""Compare dotted version strings ("5.1.1", "v2.0.2").

Shared by the hub self-updater (app/updater.py) and the catalog version-check
script (scripts/check_versions.py), so both judge "is this newer?" the same way.
"""


def _parse(v: str) -> tuple[int, ...]:
    """A version string as an int tuple; leading v/V is ignored. Raises on non-numeric parts."""
    return tuple(int(x) for x in v.lstrip("vV").split("."))


def is_newer(candidate: str, current: str) -> bool:
    """True if ``candidate`` is a strictly higher version than ``current``.

    Non-numeric or malformed versions (a git-hash tag, a marketing name) compare
    as not-newer rather than raising, so a weird value never triggers a bogus
    "update available".
    """
    try:
        return _parse(candidate) > _parse(current)
    except ValueError:
        return False
