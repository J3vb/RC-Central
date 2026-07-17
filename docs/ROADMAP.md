# Roadmap

Where RC Central is headed, phase by phase. The ordering logic: **trust before
growth before launch** — code signing and hash verification protect exactly the
users a README refresh and community push would bring in.

Executable plans live in `docs/superpowers/plans/`; this file is the big
picture. Items marked **(human)** need a maintainer action that can't be
automated.

## Now — v0.6.x wrap-up

- [x] Tuning page additions — landed on `dev` 2026-07-16
      (plan: `docs/superpowers/plans/2026-07-15-tuning-page-additions.md`)
- [x] Refresh README — landed on `dev` 2026-07-17
- [x] Bump GitHub Actions off deprecated Node 20 majors — landed on `dev`
      2026-07-17 (setup-uv pinned to v7: astral-sh publishes no v8 major alias)

## v0.7 — "Trust" (security & robustness, before promoting the app anywhere)

The code-side work is fully planned in
`docs/superpowers/plans/2026-07-16-v0.7-trust-hardening.md` (which also folds
in the two README/CI items above).

- [x] Publish a `.sha256` per release asset; verify it in
      `updater.fetch_update` before staging PENDING — landed on `dev`
      2026-07-17 (release-time proof pending: tag → 6 assets, v0.6→v0.7
      upgrade logs the hash check)
- [x] Pin vendor `download.sha256` for the high-blast-radius catalog entries
      (AGFRC, FlySky, Hobbywing, EdgeTX) — landed on `dev` 2026-07-17
- [x] Traversal guard on `exe_relative_path`/`setup_relative_path` — landed on
      `dev` 2026-07-17, covering both `installer._find_exe` and the
      `archive=="exe"` copy site review found unguarded
- [x] Sanity-check the fetched remote catalog's shape before trusting or
      caching it — landed on `dev` 2026-07-17, including a strict id slug
      check (id feeds `TOOLS_DIR / id` and `shutil.rmtree`)
- [x] Add ruff (dev-only) and a CI lint step — landed on `dev` 2026-07-17
- [ ] **(human)** Apply for SignPath OSS code signing, then wire the signing
      step into `build.yml` — unsigned exes trip SmartScreen, the single
      biggest blocker to community adoption. *Deferred 2026-07-17 until the
      project is more widely used; prepared application + wiring steps live
      in `docs/SIGNING.md`.*
- [ ] **(human)** EdgeTX elevated-install UAC smoke test on a real machine

## v0.8 — "Growth" (catalog & community)

- [ ] Populate `drivers[]` for tools that need USB drivers (SkyRC, Hobbywing,
      Rêve D programmers) — the per-row driver menu shipped in v0.4.0 but no
      catalog entry uses it yet
- [ ] `CONTRIBUTING.md` plus a catalog-entry PR template — catalog PRs are the
      project's growth engine and there is no on-ramp for outsiders today
- [ ] Extend `check-versions.yml` to auto-open an issue when a vendor link
      dies, instead of just failing red

## v1.0 — community launch

- [ ] Signed, documented, hardened → announce to the RC drift community
      (forums, Discord, r/rcdrift)
- [ ] Publish RC Central itself to winget/Scoop, so the launcher is
      installable the way it installs others
- [ ] Split `catalog/` into its own repo at the first outside PR (settled
      decision, 2026-07-10)

## Backlog / experimental

- Window-embedding spike on the real Rêve D exe (`spike/embed_spike.py` —
  never run). Phase 2 embedding stays opt-in per tool, and only if the spike
  proves solid.
- Split `app/main.py` (~2,100 lines) into an `app/ui/` package, one file per
  tab. Deliberately deferred — trigger: the next time a tab-level change costs
  more scrolling than thinking.
