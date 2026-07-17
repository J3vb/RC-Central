# Code signing (SignPath)

Status: SignPath Foundation OSS application prepared 2026-07-17
(form at <https://signpath.org/apply.html>; approval reportedly takes ~1 week).
The README's "Code signing policy" section is the repo-side prerequisite —
required attribution, team roles, and privacy statement.

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
