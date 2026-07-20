"""Validate catalog entries against the schema; optionally health-check download URLs.

Used by CI (validate-catalog.yml) and runnable locally:
    uv run python scripts/validate_catalog.py [--check-urls] [--write]

--write regenerates catalog/catalog.json (the aggregated file the app fetches
remotely); run it after adding or editing a tool. CI fails if it is stale.
"""

import json
import sys
from pathlib import Path

import jsonschema
import requests

CATALOG = Path(__file__).resolve().parents[1] / "catalog"
BUNDLE = CATALOG / "catalog.json"


def _check_url(url: str, attempts: int = 3, expect_file: bool = True) -> str | None:
    """Health-check a URL. Returns a failure message, or None if reachable.

    A definitive answer fails at once: HTTP >=400, or (when ``expect_file``) an HTML
    page where a file is expected (dead vendor links serve a "file not found" page
    with HTTP 200). Info-card ``links`` point at manuals/product pages, which are
    legitimately HTML, so they pass ``expect_file=False``. A network error is retried
    instead - vendor sites intermittently time out or throttle CI datacenter IPs, and
    one transient blip must not be mistaken for link rot. Only a network error that
    persists across every attempt is reported.
    """
    last_exc = None
    for _ in range(attempts):
        try:
            # stream + close: headers only, no multi-MB body. HEAD is useless here:
            # vendors serve "file not found" HTML pages with HTTP 200.
            with requests.get(url, stream=True, timeout=60) as resp:
                if resp.status_code == 403:
                    # bot-blocking, not link rot: vendors (Onisiki) 403 CI
                    # datacenter IPs while serving the same URL fine elsewhere;
                    # real removals show up as 404
                    return None
                if resp.status_code >= 400:
                    return f"HTTP {resp.status_code}"
                if expect_file and "text/html" in resp.headers.get("content-type", ""):
                    return "served a web page, not a file (link moved?)"
                return None
        except requests.RequestException as e:
            last_exc = e  # transient -> retry; persistent -> reported after the loop
    return f"{last_exc} (after {attempts} attempts)"


def main() -> int:
    check_urls = "--check-urls" in sys.argv[1:]
    schema = json.loads((CATALOG / "schema.json").read_text(encoding="utf-8"))
    failures = []
    tools = []
    entries = sorted((CATALOG / "tools").glob("*.json"))
    if not entries:
        failures.append("no catalog entries found")
    for f in entries:
        tool = json.loads(f.read_text(encoding="utf-8"))
        tools.append(tool)
        try:
            jsonschema.validate(tool, schema)
        except jsonschema.ValidationError as e:
            failures.append(f"{f.name}: {e.message}")
            continue
        if tool["id"] != f.stem:
            failures.append(f"{f.name}: id {tool['id']!r} must match filename")
        if check_urls:
            if "download" in tool:  # info-only cards have no download to check
                url = tool["download"]["url"]
                problem = _check_url(url)
                if problem:
                    failures.append(f"{f.name}: {url} -> {problem}")
            # homepage is where users land from the Website button, so a dead one is a
            # real (if quieter) failure than a dead download. NOTE this catches hard
            # deaths only - 404, DNS, a gone domain. It canNOT catch a soft 404, where
            # the vendor serves HTTP 200 with a "product not found" page: that is
            # exactly what yokomo-rpx-esc did, and no status-based check will see it.
            if tool.get("homepage"):
                problem = _check_url(tool["homepage"], expect_file=False)
                if problem:
                    failures.append(f"{f.name}: {tool['homepage']} -> {problem}")
            for link in tool.get("links", []):
                problem = _check_url(link["url"], expect_file=False)
                if problem:
                    failures.append(f"{f.name}: {link['url']} -> {problem}")
        print(f"ok: {f.name}")
    bundle = json.dumps(tools, ensure_ascii=False, indent=2) + "\n"
    if "--write" in sys.argv[1:]:
        BUNDLE.write_text(bundle, encoding="utf-8")
        print(f"wrote {BUNDLE.name}")
    elif not BUNDLE.exists() or BUNDLE.read_text(encoding="utf-8") != bundle:
        failures.append(
            "catalog.json is stale - run: uv run python scripts/validate_catalog.py --write"
        )
    for msg in failures:
        print(f"FAIL: {msg}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
