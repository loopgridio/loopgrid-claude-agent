# RC2: Claude runtime diagnosis after Windows Step 3

The original RC1 live example raised `AssertionError: {receipts: [], gateError: None, postError: None}` but silently dropped all SDK response messages. That output does **not** establish that an SDK tool call was attempted or that the LoopGrid gate was involved.

RC2 modifies **only the live sandbox example and its test instructions**. No security/authorization Core adapter, test, or dependency changes.

Run once with a working authenticated Claude SDK session and LoopGrid Core:

```powershell
.\.venv\Scripts\python.exe examples\native_claude_runtime_e2e.py
```

Look for:

- `MCP connection status`: `loopgrid_sandbox` should be connected and expose `sandbox_refund`.
- `SDK message: ... ResultMessage`: `is_error`, `subtype`, and bounded error/result previews reveal model/CLI problems that RC1 concealed.
- `SDK message: ... AssistantMessage`: a `ToolUseBlock` should name `mcp__loopgrid_sandbox__sandbox_refund`.
- `gateError` / `postError`: if set, identify a LoopGrid hook rejection/failure (not simply a missing model tool call).

The exact next fix depends on the *observed* cause. Never disable the PreToolUse gate, weaken tool argument commitments, enable `bypassPermissions`, or claim an executed tool based on a model text response. The initial v0.1.0 release may be distributed with this model-driven scenario recorded as unverified; do not claim the scenario passed until real tool invocation, post-tool evidence, and Core verification succeed.
