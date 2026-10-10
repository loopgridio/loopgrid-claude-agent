# Security and trust model

LoopGrid's hook adapter provides a **decision-bound application gate**, not a sandbox. The host creates authentic agent identity, authority, policy verdict and review evidence; model-generated text does not become proof of any of those facts.

**Pre-execution:** `PreToolUse` runs before Claude SDK permission checks. The adapter matches the complete canonical argument digest and exact tool name, requires successful `tool_requested` persistence, atomically reserves a unique tool-call ID and consumes a one-use authorization. Any failure returns a native SDK `deny`. The `options()` helper registers wildcard hooks for built-in and MCP tools and uses `dontAsk` plus a narrow allowlist. Do not remove, replace, or mutate the hook configuration. Do not run the same action through an unprotected SDK client.

**External action boundary:** Claude's tools perform the action; LoopGrid does not. An allowed hook is not proof a tool ran. A returned tool response is not independent proof of a payment, refund, or other downstream business effect. Such outcomes require a receipt verified by the host and recorded separately.

**Post-execution:** If `PostToolUse` persistence fails, the action may already have happened. The adapter emits a `continue_=False` response and stores a reconciliation marker. **Never automatically retry** such actions. For `PostToolUseFailure`, the adapter likewise stops and flags uncertain side effects without recording a successful tool execution.

**Local durability:** `SQLiteAuthorizationStore` and `SQLiteClaimStore` support independent Python processes using a secure local SQLite path. They are not a distributed consensus mechanism. Post-write failures between Core and local SQLite are ambiguous; operators must reconcile with Core and the external service. Do not reset invocation counters to "fix" errors.

**Hook composition:** This adapter purposely controls `hooks`, `allowed_tools`, `permission_mode`, and `setting_sources`. Other SDK hooks or policies may change input data, and plugins or custom subagents may introduce execution pathways. Validate actual SDK lifecycle coverage and subagent behavior on the deployed version; use defense in depth for destructive actions.

**Review authenticity:** The app must authenticate and authorize reviewer IDs. A host `approval_handler` must not rely on the model's own text or fabricated identity. Synthetic reviewers in examples demonstrate mechanics, not production identity proof.

**Privacy:** Tool args, tool results, and assistant model contents are hashed locally by default; the host decision and submitted outcome payloads may contain intentionally supplied business data. Protect Core, database and signing keys and control who can export evidence.

**Verification:** Valid Ed25519 signatures and SHA-256 chains prove the integrity of captured evidence, not completeness of uninstrumented work, business correctness, or verified timestamp provenance. External timestamps require a separate configured capability.
