import asyncio
import dataclasses
import json
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopgrid_claude_agent import (
    LoopGridClaudeAgent, ReviewVerdict, SQLiteAuthorizationStore, SQLiteClaimStore,
    VERSION, FRAMEWORK, EvidenceGateError,
)
from loopgrid_claude_agent.integration import _commit
from loopgrid_claude_agent.claims import Claim, MemoryClaimStore


def run(awaitable):
    return asyncio.run(awaitable)


def begin(core, *, policy='auto_allowed', tool='Bash', args=None, bridge=None):
    bridge = bridge or LoopGridClaudeAgent(client=core, workspace_id='ws-test', agent_id='agent')
    d = bridge.start_decision(
        decision_type='sandbox_action', agent={'id':'agent'},
        authority={'acting_for':'sandbox', 'scope':['sandbox']},
        model={'name':'claude-test', 'provider':'anthropic'},
        context={'prompt_version':'test-v1'},
        proposed_action={'tool': tool, 'arguments': args or {'command': 'echo sandbox'}},
        metadata={'sandbox': True},
        policy={'policy_id':'policy-v1', 'version':'1', 'decision':policy} if policy else None,
    )
    return bridge, d['decision_id']


def req(event='PreToolUse', name='Bash', args=None, uid='tool-1', session='ses-1'):
    return {'hook_event_name':event,'tool_name':name,
            'tool_input':args or {'command':'echo sandbox'},
            'tool_use_id':uid, 'session_id':session}


def permitted(result):
    return result.get('hookSpecificOutput',{}).get('permissionDecision') == 'allow'


def denied(result):
    return result.get('hookSpecificOutput',{}).get('permissionDecision') == 'deny'


def pre(h, **kw):
    event = req(**kw)
    return run(h.pre_tool_use(event,event['tool_use_id'],{}))


def post(h, response='ok', **kw):
    event = req(event='PostToolUse', **kw)
    event['tool_response']=response
    return run(h.post_tool_use(event,event['tool_use_id'],{}))


def test_metadata():
    assert VERSION == '0.1.0' and FRAMEWORK == 'claude-agent-sdk'


def test_constructor_validation(core):
    with pytest.raises(ValueError): LoopGridClaudeAgent(client=core, workspace_id='')
    with pytest.raises(ValueError): LoopGridClaudeAgent(client=core, agent_id='')


def test_decision_fields_and_policy(core):
    bridge, did = begin(core)
    assert core.decisions[did]['request']['metadata']['framework'] == FRAMEWORK
    assert core.decisions[did]['policy'] == 'auto_allowed'
    with pytest.raises(ValueError): bridge.record_policy(did, {'policy_id':'again','version':'1','decision':'auto_allowed'})


def test_action_requires_policy(core):
    bridge,did = begin(core,policy=None)
    assert denied(pre(bridge.hooks(did)))


def test_blocked(core):
    bridge,did = begin(core,policy='blocked')
    assert denied(pre(bridge.hooks(did)))


def test_wrong_name(core):
    bridge,did = begin(core)
    assert denied(pre(bridge.hooks(did),name='Write'))
    assert not bridge.pending_reconciliations()


def test_wrong_arguments(core):
    bridge,did = begin(core)
    assert denied(pre(bridge.hooks(did),args={'command':'echo attack'}))


def test_missing_identifier(core):
    bridge,did = begin(core)
    x = req(); x['tool_use_id']=''
    assert denied(run(bridge.hooks(did).pre_tool_use(x,None,{})))


def test_mismatched_callback_id(core):
    bridge,did = begin(core)
    assert denied(run(bridge.hooks(did).pre_tool_use(req(),'spoof',{})))


def test_success_workflow(core):
    bridge,did = begin(core)
    h = bridge.hooks(did)
    assert permitted(pre(h))
    assert post(h,{'receipt_id':'sandbox-25'}) == {}
    assert bridge.pending_reconciliations() == []
    assert bridge.record_observed_outcome(did,observer='sandbox',receipt_id='sandbox-25',
                                          outcome={'status':'succeeded'})['ok']
    assert [e[0] for e in core.decisions[did]['events']][-3:] == [
        'tool_requested','tool_executed','outcome_observed']


def test_no_outcome_before_tool(core):
    bridge,did = begin(core)
    with pytest.raises(EvidenceGateError):
        bridge.record_observed_outcome(did,observer='host',receipt_id='r',outcome={'ok':True})


def test_duplicate_use_id_denied(core):
    bridge,did = begin(core)
    h=bridge.hooks(did)
    assert permitted(pre(h))
    assert denied(pre(h))
    assert h.last_gate_error is not None


def test_new_id_respects_invocation_limit(core):
    bridge,did = begin(core)
    h=bridge.hooks(did)
    assert permitted(pre(h))
    assert denied(pre(h,uid='tool-2'))


def test_rejected_approval(core):
    bridge,did = begin(core,policy='human_approval_required')
    bridge.record_human_review(did,reviewer='authenticated-reviewer',approved=False)
    assert denied(pre(bridge.hooks(did)))
    assert core.decisions[did]['review'] is False


def test_preapproved_human(core):
    bridge,did = begin(core,policy='human_approval_required')
    bridge.record_human_review(did,reviewer='authenticated-reviewer',approved=True)
    assert permitted(pre(bridge.hooks(did)))


def test_missing_review_denies(core):
    bridge,did = begin(core,policy='human_approval_required')
    assert denied(pre(bridge.hooks(did)))


def test_async_trusted_review_approves(core):
    bridge,did = begin(core,policy='human_approval_required')
    async def callback(decision_id, name, args):
        assert decision_id == did and name == 'Bash'
        return ReviewVerdict(reviewer_id='host-auth-user-123',approved=True,reason='ticket-1')
    assert permitted(pre(bridge.hooks(did,approval_handler=callback)))
    assert core.decisions[did]['review'] is True


def test_async_trusted_review_denies(core):
    bridge,did = begin(core,policy='human_approval_required')
    async def callback(d,n,a):
        return ReviewVerdict('host-auth-user-123',False,'declined')
    assert denied(pre(bridge.hooks(did,approval_handler=callback)))
    assert core.decisions[did]['review'] is False


def test_invalid_review_is_denied(core):
    bridge,did = begin(core,policy='human_approval_required')
    async def callback(d,n,a):
        return {'approved':True, 'reviewer':'model claimed approval'}
    assert denied(pre(bridge.hooks(did,approval_handler=callback)))


def test_wrong_arguments_does_not_prompt(core):
    bridge,did = begin(core,policy='human_approval_required')
    invoked=[]
    async def callback(d,n,a):
        invoked.append(True)
        return ReviewVerdict('host',True)
    assert denied(pre(bridge.hooks(did,approval_handler=callback),args={'command':'not authorized'}))
    assert not invoked


def test_pre_core_write_failure_denies(core):
    bridge,did = begin(core)
    core.fail_on.add('tool_requested')
    assert denied(pre(bridge.hooks(did)))
    assert bridge.pending_reconciliations() == []


def test_post_core_write_failure_halts(core):
    bridge,did = begin(core)
    h=bridge.hooks(did)
    assert permitted(pre(h))
    core.fail_on.add('tool_executed')
    r=post(h,{'receipt_id':'r1'})
    assert r.get('continue_') is False
    assert 'after execution' in r['stopReason']
    assert len(bridge.pending_reconciliations())==1
    with pytest.raises(EvidenceGateError):
        bridge.record_observed_outcome(did,observer='host',receipt_id='r',outcome={'ok':True})


def test_post_unknown_id_halts(core):
    bridge,did=begin(core)
    assert post(bridge.hooks(did),uid='unknown').get('continue_') is False


def test_post_modified_args_halts(core):
    bridge,did = begin(core)
    h=bridge.hooks(did)
    assert permitted(pre(h))
    assert post(h,args={'command':'evil'}).get('continue_') is False


def test_failed_tool_never_records_success(core):
    bridge,did = begin(core)
    h=bridge.hooks(did)
    assert permitted(pre(h))
    event=req(event='PostToolUseFailure');event['error']='sandbox failed after starting'
    r=run(h.post_tool_use_failure(event,event['tool_use_id'],{}))
    assert r.get('continue_') is False
    assert 'tool_executed' not in [n for n,_ in core.decisions[did]['events']]
    assert bridge.pending_reconciliations()[0].state == 'failed'


def test_empty_reviewer_invalid(core):
    bridge,did=begin(core,policy='human_approval_required')
    with pytest.raises(ValueError): bridge.record_human_review(did,reviewer='',approved=True)


def test_review_failure_does_not_grant_auth(core):
    bridge,did=begin(core,policy='human_approval_required')
    core.fail_on.add('human_approved')
    with pytest.raises(RuntimeError): bridge.record_human_review(did,reviewer='host',approved=True)
    assert denied(pre(bridge.hooks(did)))


def test_claim_store_replay_and_finish():
    m=MemoryClaimStore()
    c=Claim('w','d','t','Bash','h','reserved')
    m.reserve(c)
    with pytest.raises(ValueError): m.reserve(c)
    m.finish('w','d','t','recorded','abc')
    assert not m.needing_reconciliation('w')
    assert len(m.successful('w','d'))==1


def test_sqlite_restart_and_replay(core,tmp_path):
    db=tmp_path/'auth.db'
    a=LoopGridClaudeAgent.with_sqlite(client=core,workspace_id='ws-test',agent_id='agent',sqlite_path=str(db))
    bridge,did=begin(core,bridge=a)
    assert permitted(pre(a.hooks(did)))
    b=LoopGridClaudeAgent.with_sqlite(client=core,workspace_id='ws-test',agent_id='agent',sqlite_path=str(db))
    assert denied(pre(b.hooks(did)))
    assert denied(pre(b.hooks(did),uid='second'))
    assert b.pending_reconciliations()[0].state == 'reserved'
    assert post(b.hooks(did),{'receipt_id':'r'}) == {}
    assert b.record_observed_outcome(did,observer='sandbox',receipt_id='r',outcome={'status':'ok'})


def test_sqlite_claim_atomic_race(tmp_path):
    db=tmp_path/'c.db'
    stores=[SQLiteClaimStore(db) for _ in range(8)]
    c=Claim('w','d','call-1','Bash','hash','reserved')
    def attempt(s):
        try: s.reserve(c);return True
        except Exception: return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes=list(pool.map(attempt,stores))
    assert sum(outcomes)==1


def test_sqlite_policy_atomic_race(core,tmp_path):
    db=tmp_path/'a.db'
    bridge=LoopGridClaudeAgent.with_sqlite(client=core,workspace_id='ws-test',agent_id='agent',sqlite_path=str(db))
    _,did=begin(core,bridge=bridge)
    bridges=[LoopGridClaudeAgent.with_sqlite(client=core,workspace_id='ws-test',agent_id='agent',sqlite_path=str(db)) for _ in range(6)]
    def run_pre(b,i):
        return pre(b.hooks(did),uid=f'call-{i}')
    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes=list(pool.map(lambda v:run_pre(*v),zip(bridges,range(6))))
    assert sum(permitted(o) for o in outcomes)==1


def test_options_native_shape_without_api_keys(core,monkeypatch):
    class HookMatcher:
        def __init__(self, **kwargs): self.matcher=kwargs.get('matcher');self.hooks=kwargs['hooks']
    class ClaudeAgentOptions:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    monkeypatch.setitem(sys.modules,'claude_agent_sdk',types.SimpleNamespace(
        ClaudeAgentOptions=ClaudeAgentOptions,HookMatcher=HookMatcher))
    bridge,did=begin(core)
    opts=bridge.hooks(did).options(max_turns=2)
    assert opts.permission_mode=='dontAsk'
    assert opts.setting_sources == []
    assert opts.allowed_tools == ['Bash']
    assert set(opts.hooks)=={'PreToolUse','PostToolUse','PostToolUseFailure'}
    assert all(opts.hooks[x][0].matcher is None for x in opts.hooks)
    assert callable(opts.hooks['PreToolUse'][0].hooks[0])


def test_options_cannot_override_security_fields(core):
    bridge,did=begin(core)
    for k,v in {'hooks':{},'permission_mode':'bypassPermissions',
                'setting_sources':['project'],'allowed_tools':['Bash','Write'],
                'can_use_tool':lambda *a:None}.items():
        with pytest.raises(ValueError): bridge.hooks(did).options(**{k:v})


def test_model_commitments_do_not_store_raw_text(core):
    bridge,did=begin(core)
    h=bridge.hooks(did)
    A=type('AssistantMessage',(),{})
    msg=A();msg.model='claude-xx';msg.content=['SENSITIVE-PROMPT-TEXT']
    async def it():
        yield msg
        yield object()
    async def collect():
        return [m async for m in h.observe_messages(it())]
    assert len(run(collect()))==2
    model_event=[p for k,p in core.decisions[did]['events'] if k=='model_completed'][0]
    assert 'SENSITIVE-PROMPT-TEXT' not in json.dumps(model_event)


def test_sensitive_values_are_sha_only(core):
    bridge,did=begin(core,args={'command':'echo PRIVATE_DATA'})
    h=bridge.hooks(did)
    assert permitted(pre(h,args={'command':'echo PRIVATE_DATA'}))
    serialized=json.dumps(core.decisions[did]['events'])
    assert 'PRIVATE_DATA' not in serialized


def test_noncanonical_arguments_fail_closed(core):
    bridge,did=begin(core)
    assert denied(pre(bridge.hooks(did),args={'command':object()}))
