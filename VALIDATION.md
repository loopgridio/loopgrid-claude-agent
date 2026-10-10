# Claude Agent SDK v0.1.0 — validation record

## Confirmed on Windows (Python 3.12, Claude Agent SDK 0.2.165)

- `python -m pip check`: **No broken requirements**.
- `python -m pytest -q`: **59 passed**.
- `examples/native_core_hook_e2e.py`: **PASS** — native hook registration, one deterministic sandbox action, real Core `0.8.1-design-partner`, `evidence_complete`, coverage score 100, signed verification valid.
- `examples/native_core_security_e2e.py`: **6/6 PASS** — blocked, missing approval, human rejected, human approved, wrong arguments, and pre-execution evidence-write failure; each verified against real Core.
- `examples/native_core_recovery_e2e.py`: **PASS** — action occurred, post-execution evidence failed, downstream reconciliation required; no false completion.
- `examples/offline_core_durability_e2e.py`: **PASS** — SQLite restart, replay denial, one authorized sandbox action with signed Core verification; separate post-execution outage with durable reconciliation and retry denied.
- `python -m build`: **PASS** — isolated-build wheel (`.whl`) and source archive (`.tar.gz`).
- `python -m twine check .\dist\*`: **PASSED** for both distributions.
- **Clean wheel installation:** **PASS** — new `.venv-wheelcheck` environment, dependency resolution, `pip check`, and `from loopgrid_claude_agent import LoopGridClaudeAgent, VERSION` printed `0.1.0 LoopGridClaudeAgent`.

The real-Core scripts use real SDK hook option construction and deterministic callback inputs; they do **not** initiate a paid Claude model turn. The simulator does not move money.

## Actual Claude model runtime

The diagnostic Claude Agent SDK `0.2.165` session started and returned:

1. Without runtime authentication: `Not logged in · Please run /login`.
2. With an `ANTHROPIC_API_KEY`: `Credit balance is too low`.

No model-initiated tool call ran during either attempt. The available evidence points to external API access/billing; it does not prove or disprove live tool-hook compatibility. This additional live-runtime validation remains pending.

When funded API credits are available, from a checkout with LoopGrid Core running:

```powershell
.\.venv\Scripts\python.exe examples\native_claude_runtime_e2e.py
```

**Pass criteria:** exactly one authorized tool action, native pre/post-hook evidence, model-output commitment, observed sandbox receipt, `evidence_complete`, complete applicable coverage and independent Core verification. Inspect failure diagnostics rather than weakening permission gates.

## Public release checks

- GitHub repository pushed and GitHub Actions green on the exact release commit.
- Dependencies constrained to the tested Claude SDK 0.2.x line, starting at `0.2.165`.
- PyPI Trusted Publisher configured for `loopgridio/loopgrid-claude-agent`, `release.yml`, environment `pypi`.
- Wheel/sdist built, Twine metadata passed, and clean-wheel import checked.
- Publish `v0.1.0` as the initial release without claiming the real model-to-MCP scenario has passed.
- Public PyPI clean installation confirmed after release.

**Status: offline and Core-backed validation complete for v0.1.0.** Actual Claude model-to-MCP dispatch remains unverified and is recorded above for follow-up. PyPI uses the standard Beta maturity classifier for this initial release. See [`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md).
