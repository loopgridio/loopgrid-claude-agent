# LoopGrid × Claude Agent SDK

**Verifiable decision evidence and policy enforcement for consequential Claude agent actions.**

Give Claude agents the freedom to use real tools—and give your application an independently checkable record of *what was authorized, what ran, and what happened afterward*.

**LoopGrid adds a decision-level evidence layer to the native Claude Agent SDK lifecycle:**

**Authority → Policy → Human review (when required) → Tool request → Tool execution → Observed outcome → Signed verification**

It works with the Claude Agent SDK's built-in and MCP tools, uses native tool hooks, and keeps the Claude model and existing tool implementations in place. LoopGrid Core signs and chains the resulting evidence for independent verification.

## Why LoopGrid

A conventional agent trace can describe an interaction. LoopGrid links consequential actions to their authorization and outcome evidence, so an operator or auditor can independently verify the captured record.

| Capability | What you get |
| --- | --- |
| **Native Claude hooks** | `PreToolUse`, `PostToolUse`, and `PostToolUseFailure` lifecycle integration |
| **Decision-scoped authorization** | Exact tool-name and canonical-argument matching; fail-closed policy checks before execution |
| **Human-in-the-loop evidence** | Host-authenticated approval and rejection records when a decision requires review |
| **Execution provenance** | Correlation by tool-use ID, response commitments, failure reporting, and model-output commitments |
| **Durable action safety** | One-use authorizations, replay/concurrency protection, optional SQLite persistence across local process restarts |
| **Outcome evidence** | Separately recorded, host-verified receipts rather than treating model text as a completed action |
| **Independent verification** | Ed25519 signatures and SHA-256-linked evidence from LoopGrid Core |
| **Reconciliation** | Explicit handling of uncertain actions when evidence cannot be persisted after tool execution |

Designed for teams building Claude agents that act through business APIs, internal tools, MCP servers, and automated workflows—especially where policy, review, and proof matter as much as the model's answer.

## Installation

**From the current source checkout:**

```bash
python -m pip install -e ".[dev]"
```

**After the public v0.1.0 release:**

```bash
pip install loopgrid-claude-agent==0.1.0
```

Requires Python 3.11+, the official `claude-agent-sdk` Python package, the `loopgrid` Python SDK, and a reachable LoopGrid Core instance. The SDK dependencies are declared in `pyproject.toml`.

## Integrate in four steps

**1. Create a decision with exact action authority and a policy verdict.** Your trusted application—not the model—chooses the tool, arguments, scope, and policy.

```python
from loopgrid import LoopGrid
from loopgrid_claude_agent import LoopGridClaudeAgent

workspace_id = "YOUR_EXISTING_WORKSPACE_ID"
core = LoopGrid(base_url="http://127.0.0.1:8000", workspace_id=workspace_id)
bridge = LoopGridClaudeAgent(client=core, workspace_id=workspace_id, agent_id="refund-agent")

decision_id = bridge.start_decision(
    decision_type="refund_simulation",
    agent={"id": "refund-agent", "version": "1"},
    authority={"acting_for": "sandbox", "scope": ["refund:simulate"]},
    model={"provider": "anthropic", "name": "claude-agent-sdk"},
    context={"prompt_version": "refund-v1"},
    proposed_action={
        "tool": "mcp__loopgrid_sandbox__sandbox_refund",
        "arguments": {"amount": 25},
    },
    policy={"policy_id": "refund-policy", "version": "1", "decision": "auto_allowed"},
)["decision_id"]
```

**2. Attach the native enforcement hooks to the Claude session.**

```python
from claude_agent_sdk import ClaudeSDKClient

hooks = bridge.hooks(decision_id)
options = hooks.options(mcp_servers={"loopgrid_sandbox": your_mcp_server})

async with ClaudeSDKClient(options=options) as agent:
    await agent.query("Simulate the authorized refund once using the sandbox tool.")
    async for message in hooks.observe_messages(agent.receive_response()):
        pass
```

Here, `your_mcp_server` is the SDK MCP server object your application supplies. The hooks deny other tool names, unexpected arguments, and unauthorized repeat calls.

**3. Add genuine downstream outcome evidence.**

```python
# Host application validates a receipt from the actual downstream system.
bridge.record_observed_outcome(
    decision_id,
    observer="sandbox-ledger",
    receipt_id=verified_receipt["id"],
    outcome=verified_receipt,
)
```

`verified_receipt` is supplied by your application after verification—not generated from Claude's summary.

**4. Inspect the signed decision record.**

```python
record = bridge.get_decision(decision_id)
evidence = bridge.export_evidence(decision_id)
```

For a **complete runnable example**, including creation of the in-process MCP sandbox tool, workspace setup, lifecycle assertions, and signed verification, use [`examples/native_claude_runtime_e2e.py`](examples/native_claude_runtime_e2e.py).

## Human approval and durable authorization

For sensitive actions, record a real host-authenticated approval before execution:

```python
bridge.record_human_review(
    decision_id,
    reviewer="authenticated-user-123",
    approved=True,
    reason="approved support ticket 123",
)
```

Use a decision policy of `human_approval_required` for this path, or pass a host-owned asynchronous `approval_handler` to `bridge.hooks(...)` returning a `ReviewVerdict`. A Claude-generated statement is never accepted as a reviewer identity.

For persistence across local worker restarts:

```python
bridge = LoopGridClaudeAgent.with_sqlite(
    client=core,
    workspace_id=workspace_id,
    agent_id="refund-agent",
    sqlite_path="./loopgrid-claude-authorizations.db",
)

pending = bridge.pending_reconciliations()
```

Keep the SQLite file secure and reuse the same file across restarted workers. The integration prevents blind re-execution when an action's downstream outcome needs reconciliation.

## See the evidence lifecycle in action

### Offline and real-Core validation — no Claude API credits

With LoopGrid Core running at `http://127.0.0.1:8000`:

```bash
python -m pytest -q
python examples/native_core_hook_e2e.py
python examples/native_core_security_e2e.py
python examples/native_core_recovery_e2e.py
python examples/offline_core_durability_e2e.py
```

These exercise native SDK hook construction, deterministic SDK-hook inputs, the real LoopGrid Core, authorization, security scenarios, durability, signed verification, and failure recovery. They do **not** run a Claude model.

### Full Claude model and MCP sandbox example

```bash
python examples/native_claude_runtime_e2e.py
```

This exercises an actual Claude SDK session with a harmless in-process MCP refund simulator and real Core. It requires an authenticated Anthropic API account with available credits. The sandbox moves **no real money**.

## Validation status

**Validated on Windows with Python 3.12 and Claude Agent SDK 0.2.165:** 59/59 automated tests, six real-Core security scenarios, native hook/Core verification, restart and replay durability, post-execution reconciliation, dependency checks, and wheel/sdist builds with both distributions passing `twine check`.

**Live-runtime validation:** The authenticated, model-driven Claude → MCP tool → signed Core verification scenario has not yet been completed because Anthropic API credits were unavailable. The offline, SDK-compatibility, and Core-backed tests above passed. See [`VALIDATION.md`](VALIDATION.md) for the full validation record.

## Documentation

- [Integration contract](INTEGRATION-CONTRACT.md) — evidence events, authorization, and hook mapping
- [Security and trust model](SECURITY.md) — operator-owned approval, tool boundaries, and recovery
- [Windows validation and clean-wheel check](WINDOWS-VALIDATION.md)
- [Extended offline validation](OFFLINE-EXTENDED-VALIDATION.md)
- [LoopGrid](https://loopgrid.io) · [Claude Agent SDK for Python](https://github.com/anthropics/claude-agent-sdk-python) · [Claude SDK hooks](https://code.claude.com/docs/en/agent-sdk/hooks)

This is an independently maintained LoopGrid integration for the Claude Agent SDK.
