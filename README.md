# RC Central

[![Build](https://github.com/J3vb/RC-Central/actions/workflows/build.yml/badge.svg)](https://github.com/J3vb/RC-Central/actions/workflows/build.yml)
[![Release](https://img.shields.io/github/v/release/J3vb/RC-Central)](https://github.com/J3vb/RC-Central/releases/latest)
![Status](https://img.shields.io/badge/status-beta-orange)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Platforms](https://img.shields.io/badge/platforms-Windows%20x64%20%7C%20ARM64%20%7C%20Linux%20x64-informational)
[![License: MIT](https://img.shields.io/github/license/J3vb/RC-Central)](LICENSE)

One desktop app to install and launch all your radio-controlled (RC) drift car
setup tools — servo programmers, ESC configuration software, and radio
utilities. Pick a tool from the catalog, click Install, click Launch. No hunting
through vendor download pages. It also bundles a gearing calculator, a drift
tuning guide, and a car garage for tracking your setups.

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
- **Workshop** — Garage, Gearing, and Tuning in one tab, all sharing the active
  car picked in its header:
  - **Garage** — car profiles with specs, gearing, and a setup log; compare two
    cars side by side, named gearing presets, JSON import/export, one-click
    backup/restore.
  - **Gearing** — rollout/ratio math, reverse solve (target rollout or target
    FDR → pinion), an inline gear-ratio chart, and a pinion what-if table.
  - **Tuning** — drift chassis tuning chart with per-setting explainers, a
    shock-oil conversion table, a gyro guide, and a per-car tuning log.
- **Self-update** — checks GitHub Releases on startup (toggle in Settings)
  and swaps in the new build on exit.
- **Dark & light themes** and a built-in log viewer, under the **Settings** tab.

## Supported platforms

Prebuilt binaries are released for **Windows x64**, **Windows ARM64**, and
**Linux x64**. The Workshop (Garage, Gearing, Tuning) and Manuals are fully
cross-platform; the **Tools** tab (which installs and launches vendor programmer
software) is Windows-only, since that software ships as Windows executables — on
Windows on ARM they run under the OS's x86/x64 emulation. On Linux the Tools tab
is hidden and RC Central is the Workshop and Manuals.

## Run from source

```sh
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

## Privacy policy

RC Central does not transfer any information to other networked systems
unless requested by the user or the person operating it. Its only network
activity is:

- checking GitHub Releases for app updates on startup (can be disabled in
  Settings),
- fetching the community tool catalog from this repository on GitHub,
- downloading tools from official vendor URLs when you click Install.

## License

MIT — see [LICENSE](LICENSE). Catalog data (URLs, versions) is factual metadata.
