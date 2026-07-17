# Code signing (SignPath)

Status: **deferred 2026-07-17** — the maintainer decided to wait until the
project is more widely used before applying (a week-old project scores poorly
on the application's "Reputation" question). Everything below is ready for
when that day comes.

## Applying

The application is a form at <https://signpath.org/apply.html> (approval
reportedly takes ~1 week). It requires the maintainer's name, two legal
consents, and a captcha — plus these prepared answers:

- **Project name:** RC Central
- **Repository / Homepage:** <https://github.com/J3vb/RC-Central>
- **Download URL:** <https://github.com/J3vb/RC-Central/releases>
- **Privacy policy URL:** the README's `#privacy-policy` anchor (kept in the
  README; verify it still exists on `main`)
- **Tagline:** One app to install, update, and launch RC drift car setup
  tools - servo programmers, ESC suites, and radio tools.
- **Description:** MIT-licensed Windows launcher hub for RC drift setup
  software; community JSON catalog of vendor tools downloaded only from
  official vendor URLs (Ninite/Scoop model); plus gearing calculators,
  garage, and tuning guides. PyInstaller builds for Windows x64/ARM64 via
  GitHub Releases, built on GitHub Actions with per-asset SHA-256 files and
  hash-verified self-update. Artifacts to sign: `RCCentral-windows-x64.exe`,
  `RCCentral-windows-arm64.exe`.
- **Reputation:** update with current stars/downloads/community activity at
  apply time — this is the field a young project fails on.

Before submitting, re-add the repo-side prerequisite to the README, above the
privacy policy section (required attribution + team roles):

> ## Code signing policy
>
> Free code signing for Windows binaries provided by
> [SignPath.io](https://signpath.io), certificate by the
> [SignPath Foundation](https://signpath.org).
>
> Team roles: [J3vb](https://github.com/J3vb) (maintainer) acts as committer,
> reviewer, and release approver.

SignPath Foundation also requires MFA on the GitHub account and a linked
Code of Conduct.

## After approval

1. Create the SignPath organization/project when the approval mail arrives;
   note the **organization id**, **project slug**, and create a CI **API token**.
   Enable MFA on the SignPath account (Foundation requirement).
2. Add repo secrets: `SIGNPATH_ORG_ID`, `SIGNPATH_API_TOKEN` (project slug can
   live in the workflow).
3. In `.github/workflows/build.yml`, after the `mv` step and **before**
   `sha256sum` (hashes must cover the *signed* exe), on Windows jobs only:

   ```yaml
   - uses: signpath/github-action-submit-signing-request@v1
     if: startsWith(github.ref, 'refs/tags/') && runner.os == 'Windows'
     with:
       api-token: ${{ secrets.SIGNPATH_API_TOKEN }}
       organization-id: ${{ secrets.SIGNPATH_ORG_ID }}
       project-slug: rc-central
       signing-policy-slug: release-signing
       github-artifact-id: ...   # per the action's docs at wiring time
       wait-for-completion: true
   ```

   Check the action's current README when wiring — the artifact plumbing
   (upload-unsigned → sign → download-signed) has changed between versions.
4. SignPath requires the signed binary's file metadata (product name/version)
   to be set: PyInstaller already stamps these via `RCCentral.spec` — verify
   with `Get-ItemProperty .\RCCentral.exe | fl VersionInfo` before submitting
   the policy.
5. Release-time proof: tag → confirm the Windows assets are signed
   (`signtool verify /pa RCCentral-windows-x64.exe`) and that the `.sha256`
   files match the signed binaries.

Linux binaries are unaffected. The self-updater needs no change: it verifies
the published `.sha256`, which is computed after signing.
