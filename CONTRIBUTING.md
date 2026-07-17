# Contributing to RC Central

Catalog entries are the project's growth engine — adding a tool is the most
valuable PR you can open, and you don't need to write any Python to do it.

## Adding a tool to the catalog

1. Copy an existing manifest in `catalog/tools/` as your template:
   - `reved-rs-st-pro.json` — portable tool inside a zip
   - `agfrc-servo-programmer.json` — bare portable exe (`"archive": "exe"`)
   - `edgetx-companion.json` — zip containing a silent-capable installer
   - `sanwa-pgs-servos.json` — info-only card (hardware with no PC software)
2. The filename must equal the `id` field: lowercase letters, digits, and
   hyphens only (`my-cool-tool.json` → `"id": "my-cool-tool"`). The id
   becomes a folder name on users' machines, so the schema and the app both
   reject anything else.
3. Fill in the fields — the schema (`catalog/schema.json`) is the authority:
   - `download.url` — the vendor's **official** download URL. Never a
     mirror, never a re-host, no exceptions. Many vendor URLs embed the
     version and rot on the next release: when updating a tool, bump
     `download.url`, `version`, and `download.sha256` together.
   - `download.sha256` — required policy for bare exes and installers
     (highest blast radius), optional for portable zips. Compute it with:
     `python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" <file>`
   - `install.exe_relative_path` — path of the exe inside the archive.
     For installers, `setup_relative_path` + `setup_args` (use `{dest}` for
     the install target).
   - `links[]` — official manual / support page (these may be HTML pages).
   - `version_check` — a vendor page URL + regex so CI can flag when the
     vendor ships a newer version.
   - `drivers[]` — leave empty by default. Windows 10/11 auto-installs the
     common USB-serial bridges (CP210x, CH340, FTDI) from Windows Update on
     plug-in. Only add a driver link when a real user reports the device
     not enumerating without it.
4. Regenerate the aggregate catalog:
   `uv run python scripts/validate_catalog.py --write`
   (CI fails the PR if `catalog/catalog.json` is stale.)
5. If you can, test on Windows: `uv run python -m app.main`, install and
   launch your tool from the Tools tab. Say so in the PR.

Info-only cards (hardware with no PC software) skip `download`/`install`
entirely — just `links[]` and a homepage.

## Working on the app itself

```sh
uv sync
uv run pytest        # full suite
uv run ruff check .  # lint (CI-gated)
uv run python -m app.main
```

- Logic modules (`app/catalog.py`, `app/installer.py`, `app/updater.py`)
  stay Qt-free.
- No new runtime dependencies without discussion in an issue first.
- PRs target the `dev` branch; `main` is the release branch.

## For vendors

We only link to your official downloads and send users to your site. Open an
issue if you'd like an entry changed or removed and we'll handle it promptly.
