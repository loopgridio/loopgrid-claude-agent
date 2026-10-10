"""Claude hooks + REAL LoopGrid Core + SQLite crash/restart tests. NO Claude model calls.

The only executed actions are deterministic, in-process sandbox functions.
Run from the repo root with the venv in place and LoopGrid Core on port 8000.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions
from loopgrid import LoopGrid
from loopgrid_claude_agent import LoopGridClaudeAgent
from native_core_hook_e2e import BASE, core_json


TOOL = 'mcp__loopgrid_sandbox__sandbox_refund'


def decision(bridge: LoopGridClaudeAgent, policy: str, label: str) -> str:
    return bridge.start_decision(
        decision_type='sandbox_refund',
        agent={'id': 'claude-offline-durability', 'version': '0.1.0'},
        authority={'acting_for': 'sandbox-ledger', 'scope': ['refund:simulate'], 'limit': 30},
        model={'name': 'synthetic-hook-driver-no-api-calls', 'provider': 'test'},
        context={'prompt_version': 'claude-offline-durable-e2e-v1', 'scenario': label},
        proposed_action={'tool': TOOL, 'arguments': {'amount': 25}},
        policy={'policy_id': 'sandbox-durable-rule', 'version': '1', 'decision': policy},
        metadata={'sandbox': True, 'real_money_moved': False},
    )['decision_id']


def call(did: str, event: str, uid: str, amount: int = 25, **extra):
    return {'hook_event_name': event, 'tool_name': TOOL, 'tool_input': {'amount': amount},
            'tool_use_id': uid, 'session_id': 'durability-offline-' + did[-6:], **extra}


async def model_event(hooks):
    # This is synthetic model evidence, explicitly not an authenticated Claude model turn.
    AssistantMessage = type('AssistantMessage', (), {})
    msg = AssistantMessage()
    msg.model = 'synthetic-no-api'
    msg.content = ['sandbox exercise -- no real model usage']
    async def items():
        yield msg
    async for _ in hooks.observe_messages(items()):
        pass


def verified_complete(sdk, did):
    record = sdk.get_decision(did)
    verification = record.get('verification', {})
    summary = record.get('summary', {}).get('lifecycle', {})
    state = summary.get('state') if isinstance(summary, dict) else summary
    coverage = record.get('coverage', {})
    assert verification.get('valid') is True and not verification.get('failures'), record
    assert state == 'evidence_complete', (state, record)
    assert coverage.get('score') == 100 and coverage.get('complete') is True, record
    return record


async def main():
    ready = core_json('/ready')
    assert ready.get('ready') is True, ready
    wid = core_json('/api/v1/workspaces',
                    {'name': 'claude-durability-' + uuid.uuid4().hex[:8]})['workspace_id']
    sdk = LoopGrid(base_url=BASE, workspace_id=wid)
    counter = {'executions': 0}

    with tempfile.TemporaryDirectory(prefix='loopgrid-claude-durable-') as temp:
        db = str(Path(temp) / 'hook-ledger.sqlite3')
        a = LoopGridClaudeAgent.with_sqlite(sqlite_path=db, client=sdk,
                                            workspace_id=wid, agent_id='claude-offline-durability')
        did = decision(a, 'human_approval_required', 'approved-persisted-after-restart')
        a.record_human_review(did, reviewer='authenticated-host-demo-reviewer',
                              approved=True, reason='sandbox test only')
        hooks = a.hooks(did)
        opts = hooks.options(max_turns=1)
        assert isinstance(opts, ClaudeAgentOptions)
        assert opts.allowed_tools == [TOOL]
        assert all(opts.hooks.get(e) for e in ('PreToolUse', 'PostToolUse', 'PostToolUseFailure'))
        await model_event(hooks)
        uid = 'sandbox-call-' + uuid.uuid4().hex
        pre = call(did, 'PreToolUse', uid)
        allow = await hooks.pre_tool_use(pre, uid, {})
        assert allow.get('hookSpecificOutput', {}).get('permissionDecision') == 'allow', hooks.last_gate_error

        # Deterministic sandbox action; a model or network operation is not used.
        counter['executions'] += 1
        receipt = {'id': 'sandbox-' + uuid.uuid4().hex[:10], 'amount': 25,
                   'status': 'succeeded', 'real_money_moved': False}

        # Simulate stopping one process and starting another with the same SQLite file.
        b = LoopGridClaudeAgent.with_sqlite(sqlite_path=db, client=sdk,
                                            workspace_id=wid, agent_id='claude-offline-durability')
        restarted_hooks = b.hooks(did)
        replay = await restarted_hooks.pre_tool_use(pre, uid, {})
        assert replay.get('hookSpecificOutput', {}).get('permissionDecision') == 'deny'
        new_attempt = call(did, 'PreToolUse', uid + '-another')
        replay2 = await restarted_hooks.pre_tool_use(new_attempt, uid + '-another', {})
        assert replay2.get('hookSpecificOutput', {}).get('permissionDecision') == 'deny'
        post = call(did, 'PostToolUse', uid, tool_response=receipt)
        assert await restarted_hooks.post_tool_use(post, uid, {}) == {}, restarted_hooks.last_post_error
        b.record_observed_outcome(did, observer='sandbox-ledger', receipt_id=receipt['id'],
                                  outcome={'status': 'succeeded', 'amount': 25, 'sandbox': True,
                                           'real_money_moved': False})
        record = verified_complete(sdk, did)
        events = [event.get('event_type') for event in record.get('events', [])]
        assert events.count('tool_requested') == 1, events
        assert events.count('tool_executed') == 1, events
        assert events.count('outcome_observed') == 1, events
        assert counter['executions'] == 1
        assert not b.pending_reconciliations()
        print('PASS: SQLite restart + replay blocked + one sandbox execution + signed Core verification')

        # A separate case: a tool returned, but Core cannot persist its result.
        class FailAfterExecution:
            def __init__(self, real):
                self.real = real
            def __getattr__(self, attr):
                return getattr(self.real, attr)
            def add_event(self, decision_id, event_type, payload, **kwargs):
                if event_type == 'tool_executed':
                    raise ConnectionError('intentional post-execution persistence outage')
                return self.real.add_event(decision_id, event_type, payload, **kwargs)

        c = LoopGridClaudeAgent.with_sqlite(sqlite_path=db, client=sdk,
                                            workspace_id=wid, agent_id='claude-offline-durability')
        fail_id = decision(c, 'auto_allowed', 'lost-tool-execution-evidence')
        failed_hooks = c.hooks(fail_id)
        await model_event(failed_hooks)
        failed_uid = 'sandbox-fail-' + uuid.uuid4().hex
        failed_pre = call(fail_id, 'PreToolUse', failed_uid)
        authorized = await failed_hooks.pre_tool_use(failed_pre, failed_uid, {})
        assert authorized.get('hookSpecificOutput', {}).get('permissionDecision') == 'allow'
        counter['executions'] += 1
        c.client = FailAfterExecution(sdk)
        lost_post = call(fail_id, 'PostToolUse', failed_uid,
                         tool_response={'id': 'sandbox-possibly-applied', 'real_money_moved': False})
        halted = await failed_hooks.post_tool_use(lost_post, failed_uid, {})
        assert halted.get('continue_') is False
        recovered = LoopGridClaudeAgent.with_sqlite(sqlite_path=db, client=sdk,
                                                    workspace_id=wid, agent_id='claude-offline-durability')
        pending = [x for x in recovered.pending_reconciliations() if x.decision_id == fail_id]
        assert len(pending) == 1 and pending[0].state == 'reconcile', pending
        retry_event = call(fail_id, 'PreToolUse', 'unauthorized-retry-' + uuid.uuid4().hex)
        denied = await recovered.hooks(fail_id).pre_tool_use(retry_event, retry_event['tool_use_id'], {})
        assert denied.get('hookSpecificOutput', {}).get('permissionDecision') == 'deny'
        partial = sdk.get_decision(fail_id)
        assert partial.get('verification', {}).get('valid') is True, partial
        partial_life = partial.get('summary', {}).get('lifecycle', {})
        partial_state = partial_life.get('state') if isinstance(partial_life, dict) else partial_life
        assert partial_state != 'evidence_complete', partial_state
        print('PASS: post-execution outage + durable reconciliation + retry denied + no false complete')

    print(json.dumps({
        'framework': 'claude-agent-sdk',
        'validation': 'deterministic SDK hooks + real Core + SQLite restart (NO model calls)',
        'coreVersion': ready.get('version'), 'workspaceId': wid,
        'verifiedDecisionId': did, 'reconciliationDecisionId': fail_id,
        'sandboxExecutionsAcrossBothCases': counter['executions'],
        'realMoneyMoved': False, 'anthropicApiUsed': False,
        'status': 'PASS',
    }, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
