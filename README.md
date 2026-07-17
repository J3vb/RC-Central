# RC Central

One app to install and launch all your RC drift setup tools — servo programmers,
ESC config software, and friends. Pick a tool from the catalog, click Install,
click Launch. No hunting through vendor download pages.

**Status: beta.** The catalog covers 12 entries: 7 installable tools — servo
programmers (Rêve D, AGFRC), ESC suites (Hobbywing, SkyRC, Acuvance), and
radio tools (FlySky Noble, EdgeTX Companion) — plus 5 info cards for drift
gear that has no PC software (Sanwa PGS, Futaba GYD550, Yokomo RPX, Rêve D
REVOX, Onisiki).

## How it works

RC Central never re-hosts vendor software. The catalog is a set of JSON
manifests pointing at each vendor's **official** download URL. The app downloads
straight from the vendor, unzips to the per-user data directory
(`%LOCALAPPDATA%\RCCentral\tools\` on Windows, `~/.local/share/RCCentral/tools/`
on Linux), and launches the tool — the same model as Ninite and Scoop.

Already have a tool downloaded? Use the action button's dropdown →
**Locate existing install…**, point it at the exe, and enter the version — RC
Central tracks and launches your copy without re-downloading it.

## Beyond the launcher

- **Manuals** — official manual and support links for every catalog tool,
  cross-platform.
- **Garage** — car profiles with specs, gearing, and a setup log; compare two
  cars side by side, named gearing presets, JSON import/export, one-click
  backup/restore.
- **Gear Calculator** — rollout/ratio math, reverse solve (target rollout →
  pinion), and a pinion what-if table.
- **Tuning** — drift chassis tuning chart with per-setting explainers, a
  shock-oil conversion table, a gyro guide, and a per-car tuning log.
- **Self-update** — checks GitHub Releases on startup (toggle in Settings)
  and swaps in the new build on exit.
- **Dark & light themes** (Settings tab).

## Supported platforms

Prebuilt binaries are released for **Windows x64**, **Windows ARM64**, and
**Linux x64**. The Gear Calculator and Garage are fully cross-platform; the
**Tools** tab (which installs and launches vendor programmer software) is
Windows-only, since that software ships as Windows executables — on Windows on
ARM they run under the OS's x86/x64 emulation. On Linux the Tools tab is hidden
and RC Central is the gearing calculator and garage.

## Run from source

```
uv sync
uv run python -m app.main
```

Tests: `uv run pytest`

## Adding a tool to the catalog

1. Copy `catalog/tools/reved-rs-st-pro.json` as a template.
2. Fill in the vendor's official download URL (never a mirror), version, and
   the exe name inside the archive. Validate against `catalog/schema.json`.
3. Open a PR — CI validates the schema and checks the URL is alive.

## For vendors

We only link to your official downloads and send users to your site. If you'd
like an entry changed or removed, open an issue and we'll handle it promptly.

## License

MIT — see [LICENSE](LICENSE). Catalog data (URLs, versions) is factual metadata.
