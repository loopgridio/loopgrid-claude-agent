"""Additional independent offline security and durability regression tests.

Does not send prompts to Anthropic or use any API credits. Uses the deterministic
Core substitute in conftest.py; separate examples exercise the real LoopGrid Core.
"""
import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from loopgrid_claude_agent import LoopGridClaudeAgent, ReviewVerdict, EvidenceGateError
from loopgrid_claude_agent.authorization import SQLiteAuthorizationStore
from loopgrid_claude_agent.claims import SQLiteClaimStore
from test_integration import begin, pre, post, req, permitted, denied, run


def test_denied_wrong_event_never_reserves(core):
    bridge, did = begin(core)
    h = bridge.hooks(did)
    callback = req(event='PostToolUse')
    assert denied(run(h.pre_tool_use(callback, 'tool-1', {})))
    assert bridge.authorizations.get('ws-test', did).remaining == 1
    assert not bridge.pending_reconciliations()


@pytest.mark.parametrize('corruption', [
    {'tool_name': 'mcp__untrusted__refund'},
    {'tool_input': {'command': 'echo sandbox', 'amount': 999}},
    {'tool_input': {'command': 'echo sandbox', 'nested': {'flag': True}}},
    {'tool_input': None},
    {'tool_use_id': ''},
    {'tool_use_id': None},
    {'tool_use_id': 1234},
])
def test_tool_input_or_identity_corruption_fails_closed(core, corruption):
    bridge, did = begin(core)
    h = bridge.hooks(did)
    event = {**req(), **corruption}
    # Feed explicit use-id only when it agrees with the payload; this tests
    # absent and wrong SDK callback identifiers separately.
    uid = event.get('tool_use_id') if isinstance(event.get('tool_use_id'), str) else None
    assert denied(run(h.pre_tool_use(event, uid, {})))
    assert bridge.authorizations.get('ws-test', did).remaining == 1
    assert not bridge.pending_reconciliations()


def test_success_then_duplicate_post_cannot_record_twice(core):
    bridge, did = begin(core)
    h = bridge.hooks(did)
    assert permitted(pre(h))
    assert post(h, {'receipt': 'first'}) == {}
    assert post(h, {'receipt': 'forged-second'}).get('continue_') is False
    assert sum(t == 'tool_executed' for t, _ in core.decisions[did]['events']) == 1
    assert bridge.pending_reconciliations() == []


def test_post_tool_call_identity_mismatch_never_records_execution(core):
    bridge, did = begin(core)
    h = bridge.hooks(did)
    assert permitted(pre(h))
    forged = req(event='PostToolUse')
    forged['tool_response'] = {'receipt': 'fake'}
    forged['tool_use_id'] = 'other-call'
    assert run(h.post_tool_use(forged, 'tool-1', {})).get('continue_') is False
    assert sum(t == 'tool_executed' for t, _ in core.decisions[did]['events']) == 0
    assert bridge.pending_reconciliations()


def test_failed_tool_cannot_invent_outcome(core):
    bridge, did = begin(core)
    h = bridge.hooks(did)
    assert permitted(pre(h))
    failure = req(event='PostToolUseFailure')
    failure['error'] = 'sandbox reported failure after attempted action'
    halted = run(h.post_tool_use_failure(failure, 'tool-1', {}))
    assert halted.get('continue_') is False
    assert bridge.pending_reconciliations()[0].state == 'failed'
    with pytest.raises(EvidenceGateError):
        bridge.record_observed_outcome(did, observer='test', receipt_id='r', outcome={'ok': True})


def test_untrusted_reviewer_identity_type_fails_closed(core):
    bridge, did = begin(core, policy='human_approval_required')
    async def untrusted(*_args):
        return ReviewVerdict(reviewer_id='', approved=True)
    assert denied(pre(bridge.hooks(did, approval_handler=untrusted)))
    assert core.decisions[did]['review'] is None


def test_post_evidence_outage_does_not_allow_outcome(core):
    bridge, did = begin(core)
    h = bridge.hooks(did)
    assert permitted(pre(h))
    core.fail_on.add('tool_executed')
    result = post(h, {'receipt': 'possibly executed'})
    assert result.get('continue_') is False
    assert bridge.pending_reconciliations()[0].state == 'reconcile'
    with pytest.raises(EvidenceGateError):
        bridge.record_observed_outcome(did, observer='external', receipt_id='r', outcome={'ok': True})


def test_sqlite_multiworker_concurrency_one_execution_gate(core, tmp_path):
    path = tmp_path / 'shared-local.sqlite3'
    leader = LoopGridClaudeAgent.with_sqlite(client=core, sqlite_path=str(path),
                                            workspace_id='ws-test', agent_id='agent')
    _, did = begin(core, bridge=leader)
    def worker(i):
        bridge = LoopGridClaudeAgent.with_sqlite(client=core, sqlite_path=str(path),
                                                workspace_id='ws-test', agent_id='agent')
        return pre(bridge.hooks(did), uid=f'parallel-{i}')
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(worker, range(24)))
    assert sum(map(permitted, results)) == 1
    assert leader.authorizations.get('ws-test', did).remaining == 0
    # Some non-winning claims may remain 'reserved' because cross-store writes
    # are not a single distributed transaction; do not call them executed.
    assert sum(t == 'tool_executed' for t, _ in core.decisions[did]['events']) == 0


def test_sqlite_cross_workspace_same_decision_id_isolation(core, tmp_path):
    path = tmp_path / 'isolated.sqlite3'
    a = LoopGridClaudeAgent.with_sqlite(client=core, sqlite_path=str(path),
                                       workspace_id='tenant-a', agent_id='agent')
    b = LoopGridClaudeAgent.with_sqlite(client=core, sqlite_path=str(path),
                                       workspace_id='tenant-b', agent_id='agent')
    a.authorizations.register('tenant-a', 'dec-shared', 'Bash', 'abc', 1)
    a.authorizations.set_policy('tenant-a', 'dec-shared', 'auto_allowed')
    assert b.authorizations.get('tenant-b', 'dec-shared') is None
    assert b.claims.get('tenant-b', 'dec-shared', 'tool-id') is None
    assert a.authorizations.get('tenant-a', 'dec-shared') is not None


def test_sqlite_denied_approval_still_denied_after_restart(core, tmp_path):
    path = tmp_path / 'approval.sqlite3'
    a = LoopGridClaudeAgent.with_sqlite(client=core, sqlite_path=str(path),
                                       workspace_id='ws-test', agent_id='agent')
    _, did = begin(core, bridge=a, policy='human_approval_required')
    a.record_human_review(did, reviewer='trusted-human-1', approved=False)
    restarted = LoopGridClaudeAgent.with_sqlite(client=core, sqlite_path=str(path),
                                               workspace_id='ws-test', agent_id='agent')
    assert denied(pre(restarted.hooks(did)))
    assert restarted.authorizations.get('ws-test', did).rejected is True


def test_sqlite_post_outage_reconciliation_survives_restart(core, tmp_path):
    path = tmp_path / 'recovery.sqlite3'
    a = LoopGridClaudeAgent.with_sqlite(client=core, sqlite_path=str(path),
                                       workspace_id='ws-test', agent_id='agent')
    _, did = begin(core, bridge=a)
    h = a.hooks(did)
    assert permitted(pre(h))
    core.fail_on.add('tool_executed')
    assert post(h, {'receipt_id': 'r'}).get('continue_') is False
    restarted = LoopGridClaudeAgent.with_sqlite(client=core, sqlite_path=str(path),
                                               workspace_id='ws-test', agent_id='agent')
    assert any(c.decision_id == did and c.state == 'reconcile'
               for c in restarted.pending_reconciliations())
    assert denied(pre(restarted.hooks(did), uid='new-attempt'))


def test_options_deny_untrusted_permission_override(core):
    bridge, did = begin(core)
    for key, value in [
        ('hooks', {'PreToolUse': []}),
        ('can_use_tool', lambda *_: True),
        ('allowed_tools', ['Bash', 'Write']),
        ('disallowed_tools', []),
        ('permission_mode', 'bypassPermissions'),
        ('setting_sources', ['project', 'user']),
    ]:
        with pytest.raises(ValueError):
            bridge.hooks(did).options(**{key: value})


def test_real_sdk_wildcard_hook_contract_no_anthropic_call(core):
    sdk = pytest.importorskip('claude_agent_sdk')
    bridge, did = begin(core)
    options = bridge.hooks(did).options(max_turns=1)
    assert isinstance(options, sdk.ClaudeAgentOptions)
    assert options.permission_mode == 'dontAsk'
    assert options.allowed_tools == ['Bash']
    for name in ('PreToolUse', 'PostToolUse', 'PostToolUseFailure'):
        matchers = options.hooks[name]
        assert len(matchers) == 1
        assert isinstance(matchers[0], sdk.HookMatcher)
        assert matchers[0].matcher is None
        assert len(matchers[0].hooks) == 1
        assert callable(matchers[0].hooks[0])

@pytest.mark.parametrize('durable', [False, True])
def test_authorization_limit_denial_does_not_create_phantom_claim(core, tmp_path, durable):
    bridge = (LoopGridClaudeAgent.with_sqlite(
        client=core, sqlite_path=str(tmp_path / 'claims.sqlite3'),
        workspace_id='ws-test', agent_id='agent') if durable
        else LoopGridClaudeAgent(client=core, workspace_id='ws-test', agent_id='agent'))
    _, did = begin(core, bridge=bridge)
    h = bridge.hooks(did)
    assert permitted(pre(h, uid='granted'))
    assert denied(pre(h, uid='denied-by-limit'))
    assert sum(kind == 'tool_requested' for kind, _ in core.decisions[did]['events']) == 1
    # A genuine authorized-but-unfinished call must remain in the ledger, while
    # the denied request cannot be misreported as potentially executed.
    assert {v.call_id for v in bridge.pending_reconciliations()} == {'granted'}
    assert post(h, {'receipt': 'ok'}, uid='granted') == {}
    assert not bridge.pending_reconciliations()


def test_authorization_store_failure_cancels_pre_execution_claim(core):
    bridge, did = begin(core)
    def fail_reserve(*_args):
        raise RuntimeError('synthetic authorization transaction failure')
    bridge.authorizations.reserve = fail_reserve
    assert denied(pre(bridge.hooks(did), uid='never-granted'))
    assert not bridge.pending_reconciliations()
