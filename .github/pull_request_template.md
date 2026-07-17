<!-- Thanks for contributing! Delete the section that doesn't apply. -->

## Catalog entry

- [ ] `download.url` is the vendor's **official** download URL (no mirrors,
      no re-hosts)
- [ ] Filename matches the `id` field (lowercase slug)
- [ ] `sha256` pinned if the download is a bare exe or an installer
- [ ] Manual / support links added under `links[]`
- [ ] Regenerated the aggregate: `uv run python scripts/validate_catalog.py --write`
- [ ] Tested install + launch on Windows (say which OS), or noted that you
      couldn't

## App change

- [ ] `uv run pytest` green
- [ ] `uv run ruff check .` clean
- [ ] No new runtime dependencies (or discussed in an issue first)

What does this PR do, and why?
