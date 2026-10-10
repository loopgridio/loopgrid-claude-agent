# LoopGrid Claude Agent SDK v0.1.0 — release checklist

This repository packages the independently maintained **LoopGrid × Claude Agent SDK** integration for its first public release. Package: `loopgrid-claude-agent`; Python import: `loopgrid_claude_agent`.

## Validated for the initial release

- [x] Windows Python 3.12, Claude Agent SDK `0.2.165`: **59/59 tests passed**.
- [x] Real LoopGrid Core: native SDK options/hooks with deterministic callback inputs, six security cases, post-tool recovery, SQLite restart and replay durability.
- [x] Signed Core verification, `evidence_complete` lifecycle, and authorized in-process sandbox execution.
- [x] Wheel/sdist builds, Twine validation, and clean-wheel installation/import from the previous RC2.1 Python implementation.
- [x] Reviewed publication source only, no local virtual environments or databases.
- [ ] **Additional funded runtime validation:** actual Claude model → MCP sandbox tool → native post-tool hook → signed Core. API access was denied due to Anthropic's credit balance; no model-initiated tool call has yet been verified. This must never be reported as passed.

## Push and verify the repository

1. Create an empty public `loopgridio/loopgrid-claude-agent` GitHub repository (do not initialize it with new files).
2. Extract the final GitHub-upload ZIP and open the inner `loopgrid-claude-agent` folder in PowerShell.
3. Push source to `main`:

   ```powershell
   git init -b main
   git add .
   git commit -m "feat: initial Claude Agent SDK integration v0.1.0"
   git remote add origin https://github.com/loopgridio/loopgrid-claude-agent.git
   git push -u origin main
   ```

4. Confirm GitHub Actions **Tests** matrix and package build pass on the exact commit. Those jobs do not require a model API key.
5. Check the GitHub About page and repository links.

## Publish v0.1.0 only after green CI

1. Review the public README/VALIDATION: do not describe the *model-initiated* Claude runtime as verified.
2. Set up GitHub Actions environment `pypi` in this repository.
3. Configure PyPI Trusted Publisher (or pending publisher when creating the project): owner `loopgridio`, repo `loopgrid-claude-agent`, workflow `release.yml`, environment `pypi`.
4. If the source or dependencies changed, repeat `pytest`, `python -m build`, `twine check dist/*`, and clean-wheel import as appropriate.
5. Create GitHub Release `v0.1.0` **only when prepared to publish**, because `.github/workflows/release.yml` automatically sends version `0.1.0` to PyPI using trusted publishing.
6. Confirm the PyPI project, and install and import `loopgrid-claude-agent==0.1.0` in a new environment from the public index.
7. Add the website integration entry only after the public package works. Describe supported capabilities accurately and keep the unverified model-driven runtime scenario documented in the validation record.

## Additional live-model validation (when credits available)

With authenticated Claude SDK and running LoopGrid Core, execute `examples/native_claude_runtime_e2e.py`. Require exactly one real SDK-initiated sandbox action, both native hook phases, host-verified receipt, complete signed Core evidence and independent verification. Save sanitized evidence of the outcome to `VALIDATION.md`. A published release is not proof that this scenario has passed.

## Integrity and security expectations

Do not disable the wildcard `PreToolUse` deny gate or relax exact tool/argument matching. A post-tool evidence failure must remain a reconciliation event, not a false success. Signed records prove the integrity of captured evidence, not external business correctness without a trusted receipt.
