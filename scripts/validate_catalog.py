"""Validate catalog entries against the schema; optionally health-check download URLs.

Used by CI (validate-catalog.yml) and runnable locally:
    uv run python scripts/validate_catalog.py [--check-urls]
"""

import json
import sys
from pathlib import Path

import jsonschema
import requests

CATALOG = Path(__file__).resolve().parents[1] / "catalog"


def main() -> int:
    check_urls = "--check-urls" in sys.argv[1:]
    schema = json.loads((CATALOG / "schema.json").read_text(encoding="utf-8"))
    failures = []
    entries = sorted((CATALOG / "tools").glob("*.json"))
    if not entries:
        failures.append("no catalog entries found")
    for f in entries:
        tool = json.loads(f.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(tool, schema)
        except jsonschema.ValidationError as e:
            failures.append(f"{f.name}: {e.message}")
            continue
        if tool["id"] != f.stem:
            failures.append(f"{f.name}: id {tool['id']!r} must match filename")
        if check_urls:
            url = tool["download"]["url"]
            try:
                # stream + close: headers only, no multi-MB body. HEAD is useless here:
                # vendors serve "file not found" HTML pages with HTTP 200.
                with requests.get(url, stream=True, timeout=60) as resp:
                    if resp.status_code >= 400:
                        failures.append(f"{f.name}: {url} -> HTTP {resp.status_code}")
                    elif "text/html" in resp.headers.get("content-type", ""):
                        failures.append(
                            f"{f.name}: {url} -> served a web page, not a file (link moved?)"
                        )
            except requests.RequestException as e:
                failures.append(f"{f.name}: {url} -> {e}")
        print(f"ok: {f.name}")
    for msg in failures:
        print(f"FAIL: {msg}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
