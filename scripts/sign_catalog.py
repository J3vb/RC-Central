"""Sign catalog/catalog.json with the Ed25519 update key, writing 'catalog.json.sig'.

The remote catalog is the app's control plane (it names every download URL and the
exe each tool launches), so ``app/catalog.py`` verifies this detached signature
against the pinned public key before trusting a fetched catalog — falling back to
the bundled manifests on any missing or bad signature. Re-run this whenever
catalog.json changes and commit the regenerated ``.sig`` alongside it, or let
``.github/workflows/sign-catalog.yml`` do it automatically on push to main.

The private key comes from the same ``UPDATE_SIGNING_KEY`` secret that signs release
binaries (base64 of the 32-byte Ed25519 seed); see scripts/sign_release.py.

    UPDATE_SIGNING_KEY=<base64 seed> uv run python scripts/sign_catalog.py
"""

import base64
import os
import sys
from pathlib import Path

from nacl.signing import SigningKey

CATALOG = Path(__file__).resolve().parents[1] / "catalog" / "catalog.json"


def main() -> int:
    key_b64 = os.environ.get("UPDATE_SIGNING_KEY")
    if not key_b64:
        print("UPDATE_SIGNING_KEY is not set", file=sys.stderr)
        return 1

    # Sign the LF-normalized bytes: raw.githubusercontent serves the git blob (always
    # LF), so a CRLF working copy on Windows (core.autocrlf=true) must not sign CRLF that
    # the app would then fail to verify against the LF it fetches. No-op on Linux CI.
    payload = CATALOG.read_bytes().replace(b"\r\n", b"\n")
    signature = SigningKey(base64.b64decode(key_b64)).sign(payload).signature
    out = CATALOG.with_name(CATALOG.name + ".sig")
    out.write_bytes(signature)  # raw 64-byte detached signature
    print(f"signed {CATALOG.name} -> {out.name} ({len(signature)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
