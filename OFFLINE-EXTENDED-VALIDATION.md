# Claude Agent SDK — extended offline validation (RC2.1)

The RC2.1 candidate adds adversarial hook, replay, concurrency and persistence tests along with a narrow authorization-ledger correctness fix. No Anthropic API calls are required.

**Confirmed on Windows:** 59/59 automated tests with `claude-agent-sdk==0.2.165`, plus four real-Core scenarios, a clean isolated source/wheel build, passing Twine checks and clean-wheel import.

## Security-ledger fix

An over-limit second tool call was denied by `PreToolUse`, but could previously leave an unnecessary pending-reconciliation entry. RC2.1 cancels **only the newly created pre-execution claim** when the authorization counter rejects that attempt. A tool call already authorized or executed retains the claim to prevent blind retries.

## Extended coverage

- Exact tool and argument commitment matching; wrong-input rejection.
- Policy blocked, missing approval, host approval and rejection.
- Replay detection, duplicate callback denial and forged post-tool callback rejection.
- Cross-workspace authorization separation and concurrent invocation limits.
- SQLite authorization state surviving process restarts.
- Tool result persistence failures and durable reconciliation instead of false completion.
- SDK-native options construction; guard-controlled options cannot be silently overridden by caller kwargs.

## Reproduce on Windows

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe examples\offline_core_durability_e2e.py
```

The durability example needs a running LoopGrid Core at `http://127.0.0.1:8000`. It uses a fresh workspace and disposable SQLite state; no real money moves.

**Independent local offline run:** 57 passed and 2 SDK-dependent tests skipped because that environment lacks the official SDK package. Windows installed-SDK run: **59 passed** with no skips.

**Next gate:** Actual Claude model-to-MCP tool invocation, currently deferred due to Anthropic API credits. See [`VALIDATION.md`](VALIDATION.md).
