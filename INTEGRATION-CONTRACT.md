# LoopGrid Claude Agent SDK — integration contract v0.1.0

**Decision:** an authenticated host starts a scoped decision bound to exactly one tool name and exact canonical arguments, with an invocation limit (default one).

**Policy:** only a persisted `auto_allowed` verdict, or `human_approval_required` plus authenticated persisted approval, can proceed. `blocked` always denies.

**PreToolUse:** universal SDK wildcard hook checks name + args, persists `tool_requested`, claims call ID, consumes invocation authorization, and returns allow; otherwise returns SDK deny. The SDK and application must keep these hooks installed.

**PostToolUse:** only a previously reserved, matching call ID/name/args can write `tool_executed` with result commitment. No external outcome is inferred.

**PostToolUseFailure:** stops the run; records an uncertain failed/reserved claim and requests reconciliation. No successful execution asserted.

**Model response:** host wraps `client.receive_response()` in `hooks.observe_messages()`, capturing AssistantMessage commitments (with model metadata).

**Outcome:** independent host verifies receipt and then explicitly calls `record_observed_outcome`. Core handles signed evidence and verification.

**Transport:** `loopgrid>=0.8,<0.9`; LoopGrid Core validation target `0.8.1-design-partner`; no Core modifications or alternative signer.
