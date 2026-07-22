"""Probe each catalog tool's vendor page for a newer version than we list.

Vendors have no version API and their download URLs rot every release, so each
manifest may carry a ``version_check`` block ({url, pattern}) describing where to
look and a regex whose first group captures the vendor's current version. This
script fetches those pages, compares with the catalog's ``version``, and reports
any tool the vendor appears to have moved past.

Runnable locally and from CI (check-versions.yml):
    uv run python scripts/check_versions.py [--json findings.json]

It never fails the run on a network/parse hiccup - a flaky vendor page must not
break the nightly. ``--json`` writes the findings (a possibly-empty list) for a
workflow step to turn into GitHub issues. Exit status is always 0.
"""

import json
import re
import sys
from pathlib import Path

import requests

# Import the shared version comparison the way the app judges "newer" (v-prefix
# tolerant, malformed -> not newer). scripts/ isn't on sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.versions import is_newer  # noqa: E402

CATALOG = Path(__file__).resolve().parents[1] / "catalog"


def _latest_upstream(check: dict) -> str | None:
    """The version the vendor page currently advertises, or None if we can't tell."""
    last_exc = None
    for _ in range(3):  # mirror _check_url's retry: one transient blip must not flap the nightly
        try:
            resp = requests.get(check["url"], timeout=60)
            resp.raise_for_status()
            m = re.search(check["pattern"], resp.text)
            return m.group(1) if m else None
        except requests.RequestException as e:
            last_exc = e  # transient -> retry; persistent -> raised after the loop
    raise last_exc


def check_tools(tools: list[dict]) -> list[dict]:
    """Findings for tools whose vendor page shows a newer version than the catalog."""
    findings = []
    for tool in tools:
        check = tool.get("version_check")
        if not check:
            print(f"skip: {tool['id']} (no version_check)")
            continue
        current = tool["version"]
        try:
            latest = _latest_upstream(check)
        except (requests.RequestException, re.error, IndexError) as e:
            # a flaky/blocked vendor page (RequestException), a bad catalog regex
            # (re.error), or a pattern with no capture group (IndexError on group(1))
            # must not fail the "always exit 0" nightly - report and move on
            print(f"warn: {tool['id']}: {check['url']} -> {e}", file=sys.stderr)
            continue
        if latest is None:
            print(
                f"warn: {tool['id']}: pattern {check['pattern']!r} matched nothing at "
                f"{check['url']} (page markup may have changed)",
                file=sys.stderr,
            )
            continue
        if is_newer(latest, current):
            print(f"OUTDATED: {tool['id']} catalog v{current} < vendor v{latest}")
            findings.append(
                {
                    "id": tool["id"],
                    "name": tool["name"],
                    "current": current,
                    "latest": latest,
                    "homepage": tool.get("homepage", check["url"]),
                    "checked_url": check["url"],
                }
            )
        else:
            print(f"ok: {tool['id']} v{current} (vendor v{latest})")
    return findings


def main() -> int:
    tools = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted((CATALOG / "tools").glob("*.json"))
    ]
    findings = check_tools(tools)

    args = sys.argv[1:]
    if "--json" in args:
        out = Path(args[args.index("--json") + 1])
        out.write_text(json.dumps(findings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {len(findings)} finding(s) to {out}")

    print(f"\n{len(findings)} tool(s) may be outdated.")
    return 0  # never fail the run; findings drive the issue-filing step


if __name__ == "__main__":
    sys.exit(main())
