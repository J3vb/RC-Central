"""Sign a release binary with the Ed25519 update key, writing '<binary>.sig'.

CI-only. The private key comes from the ``UPDATE_SIGNING_KEY`` secret (base64 of
the 32-byte Ed25519 seed). The app verifies the resulting ``.sig`` against the
public key pinned in ``app/updater.py`` (``_UPDATE_PUBLIC_KEY``) before staging
any self-update, so a compromised release or CI token can't ship a forged build.

    UPDATE_SIGNING_KEY=<base64 seed> uv run python scripts/sign_release.py <binary>
"""

import base64
import os
import sys
from pathlib import Path

from nacl.signing import SigningKey


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: sign_release.py <binary>", file=sys.stderr)
        return 2
    key_b64 = os.environ.get("UPDATE_SIGNING_KEY")
    if not key_b64:
        print("UPDATE_SIGNING_KEY is not set", file=sys.stderr)
        return 1

    binary = Path(sys.argv[1])
    signature = SigningKey(base64.b64decode(key_b64)).sign(binary.read_bytes()).signature
    out = binary.with_name(binary.name + ".sig")
    out.write_bytes(signature)  # raw 64-byte detached signature
    print(f"signed {binary.name} -> {out.name} ({len(signature)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
