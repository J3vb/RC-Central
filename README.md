# RC Central

One app to install and launch all your RC drift setup tools — servo programmers,
ESC config software, and friends. Pick a tool from the catalog, click Install,
click Launch. No hunting through vendor download pages.

**Status: early development.** First supported tool: Rêve D RS-ST / RS-ST PRO
servo software (needs the RS-PGCB/RS-PGCA USB programmer).

## How it works

RC Central never re-hosts vendor software. The catalog is a set of JSON
manifests pointing at each vendor's **official** download URL. The app downloads
straight from the vendor, unzips to `%LOCALAPPDATA%\RCCentral\tools\`, and
launches the tool — the same model as Ninite and Scoop.

Already have a tool downloaded? Use the action button's dropdown →
**Locate existing install…**, point it at the exe, and enter the version — RC
Central tracks and launches your copy without re-downloading it.

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
