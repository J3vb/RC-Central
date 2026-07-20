# Roadmap

Where RC Central is headed, phase by phase. The ordering logic: **trust before
growth before launch** — code signing and hash verification protect exactly the
users a README refresh and community push would bring in.

Executable plans live in `docs/superpowers/plans/`; this file is the big
picture. Items marked **(human)** need a maintainer action that can't be
automated.

## Now — UI consolidation (post-0.7.0)

- [x] Merge Garage, Gearing, and Tuning into one **Workshop** tab with a shared
      active-car header (tab bar 7 → 4), and fold the log viewer into Settings as
      a Preferences|Log pair — landed on `dev` 2026-07-17
      (spec: `docs/superpowers/specs/2026-07-17-workshop-merge-design.md`)
- [x] Inline the gear-ratio chart into Gearing and move the pinion sweep to a
      dialog — landed on `dev` 2026-07-17
- [x] Editable FDR reverse-solve (target FDR → nearest whole tooth) — landed on dev 2026-07-17, shipped in v0.7.1
- [x] UI polish round (11 tasks) — landed on `dev` 2026-07-19/20
      (plan: `.superpowers/sdd/polish-plan.md`, local-only). Instant startup:
      the window seeds from `catalog.cached_catalog()` (pure disk read) and a
      daemon thread refreshes it in the background. Plus car-delete confirm,
      accent-coloured compare diffs, a reported outcome for manual update
      checks, plain-language download-failure dialogs, empty-state hints,
      minimum window/dialog sizes, an About dialog, and Garage button
      mnemonics.

### Earlier — v0.6.x wrap-up

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
      2026-07-17; release-time proof done at v0.7.0: 6 assets published,
      `.sha256` files match GitHub's asset digests, and a live v0.6→v0.7
      self-update swapped in a binary matching the release hash. The new
      updater's own hash-check log line first fires on the next release.
- [x] Pin vendor `download.sha256` for the high-blast-radius catalog entries
      (AGFRC, FlySky, Hobbywing, EdgeTX) — landed on `dev` 2026-07-17
- [x] Traversal guard on `exe_relative_path`/`setup_relative_path` — landed on
      `dev` 2026-07-17, covering both `installer._find_exe` and the
      `archive=="exe"` copy site review found unguarded
- [x] Sanity-check the fetched remote catalog's shape before trusting or
      caching it — landed on `dev` 2026-07-17, including a strict id slug
      check (id feeds `TOOLS_DIR / id` and `shutil.rmtree`)
- [x] Add ruff (dev-only) and a CI lint step — landed on `dev` 2026-07-17
- [x] Sign each release binary with a self-managed **Ed25519** key (private key
      in a GitHub Actions secret, public key pinned in the app); verify the
      `.sig` in `updater.fetch_update` before staging PENDING, failing closed.
      Closes the self-update provenance gap — a compromised release or CI token
      can't forge a signature — without a CA, form, or cost. Same model as
      Sparkle/WinSparkle. Landed on `dev` 2026-07-17; release-time proof done
      at v0.7.1: 9 assets published (3 platforms × binary/`.sha256`/`.sig`),
      windows-x64 `.sha256` matches, and its `.sig` verifies against the key
      pinned in `app/updater.py`. The updater's own sig-check log line first
      fires on the next release.
- [x] **(human)** EdgeTX elevated-install UAC smoke test on a real machine —
      passed 2026-07-20. Full chain from source: download → pinned sha256 →
      extract → `CreateProcess` WinError 740 → `ShellExecuteExW('runas')` → UAC
      accepted → NSIS `/S /D=` silent install → exit 0 → state written →
      `bin/companion.exe` resolved. The decline path (WinError 1223) was already
      proven; both branches are now covered. Note the tested `/D=` path had no
      spaces, so the verbatim-args fix is exercised but not stress-tested.

*Out of scope: public-trust (Authenticode / SmartScreen) code signing. It needs
a CA identity check (SignPath's form, Azure's US/Canada-only validation, or a
paid cert) for a marginal fresh-install-warning benefit, while the self-managed
update signing above already protects existing users from a malicious update.
Dropped 2026-07-17; `docs/SIGNING.md` is retained (annotated) with the parked application answers.*

## v0.8 — "Growth" (catalog & community)

- [x] ~~Populate `drivers[]` for tools that need USB drivers~~ — resolved as
      won't-do 2026-07-17: Windows 10/11 auto-installs the common USB-serial
      bridges (CP210x/CH340/FTDI) from Windows Update on plug-in, confirmed
      in practice (Rêve D works driver-free). The mechanism stays; driver
      links are added per user report only (policy in `CONTRIBUTING.md`)
- [x] `CONTRIBUTING.md` plus a catalog-entry PR template — landed on `dev`
      2026-07-17
- [x] Auto-open an issue when a vendor link dies, instead of just failing
      red — landed on `dev` 2026-07-17 in `validate-catalog.yml` (where the
      nightly URL check actually runs, not `check-versions.yml`)

## v1.0 — community launch

- [ ] Update-signed, documented, hardened → announce to the RC drift community
      (forums, Discord, r/rcdrift)
- [ ] Publish RC Central itself to winget/Scoop, so the launcher is
      installable the way it installs others
- [ ] Split `catalog/` into its own repo at the first outside PR (settled
      decision, 2026-07-10)

## Backlog / experimental

- ~~Window-embedding spike on the real Rêve D exe~~ — dropped 2026-07-17; the
  spike files were removed. External launch stays the model.
- ~~Split `app/main.py` into an `app/ui/` package~~ — done, PR #13
  (2026-07-17): main.py is a pure entry point, one module per tab under
  `app/ui/`.
