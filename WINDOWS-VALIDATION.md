# Windows validation — LoopGrid Claude Agent SDK v0.1.0

**Validated on Windows:** Python 3.12, Claude Agent SDK 0.2.165, LoopGrid Core `0.8.1-design-partner`.

All credit-free gates are complete: **59/59 tests**, four real-Core scenarios (including durability), isolated wheel/sdist build, both Twine checks, and clean-wheel install/import. The optional follow-up live-runtime validation is the **real Claude model → MCP action → LoopGrid verification** run, postponed while Anthropic API credits are unavailable. The initial distribution is v0.1.0; its validation record distinguishes completed checks from the pending live-runtime scenario.

## Reproduce automated and Core integration checks

From the repository root, with the same virtual environment as the original validation:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
```

With LoopGrid Core available at `http://127.0.0.1:8000`:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/ready -TimeoutSec 30
.\.venv\Scripts\python.exe examples\native_core_hook_e2e.py
.\.venv\Scripts\python.exe examples\native_core_security_e2e.py
.\.venv\Scripts\python.exe examples\native_core_recovery_e2e.py
.\.venv\Scripts\python.exe examples\offline_core_durability_e2e.py
```

The deterministic Core tests exercise SDK hook configuration and local sandbox callbacks, and use no Claude model tokens. The sandbox moves no real money.

## Build and clean-wheel check

```powershell
Remove-Item .\dist -Recurse -Force -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m twine check .\dist\*

py -3.12 -m venv .venv-wheelcheck
$wheel = (Get-ChildItem .\dist -Filter '*.whl' | Select-Object -First 1).FullName
.\.venv-wheelcheck\Scripts\python.exe -m pip install $wheel
.\.venv-wheelcheck\Scripts\python.exe -m pip check
.\.venv-wheelcheck\Scripts\python.exe -c "from loopgrid_claude_agent import LoopGridClaudeAgent, VERSION; print(VERSION, LoopGridClaudeAgent.__name__)"
```

**Confirmed output:** both Twine checks `PASSED`; clean import `0.1.0 LoopGridClaudeAgent`.

## Additional funded live-runtime validation

```powershell
.\.venv\Scripts\python.exe examples\native_claude_runtime_e2e.py
```

The example prints bounded SDK error details, MCP connection status and tool-use diagnostics. Previous live attempts did not reach a model tool call because the CLI returned `Not logged in`, followed by `Credit balance is too low` after an API key was supplied. Do not mark this runtime scenario as passed until an authenticated Claude tool call and Core verification actually succeed. Public release documentation must maintain this distinction.

See [`VALIDATION.md`](VALIDATION.md) and [`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md).
