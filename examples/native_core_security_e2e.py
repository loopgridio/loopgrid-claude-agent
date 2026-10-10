"""Real Core policy / approval / failure checks via Claude SDK-shaped hook inputs.

Tests enforcement hook logic against actual Core persistence WITHOUT calling Claude.
No side effect beyond a local in-memory sandbox ledger.
"""
from __future__ import annotations
import asyncio
import json
import uuid
from loopgrid import LoopGrid
from loopgrid_claude_agent import LoopGridClaudeAgent
from native_core_hook_e2e import BASE, core_json


class FailRequestProxy:
    def __init__(self, real): self.real=real
    def __getattr__(self, key): return getattr(self.real,key)
    def add_event(self, did, kind, payload, **kwargs):
        if kind == 'tool_requested':
            raise ConnectionError('synthetic evidence persistence failure before tool execution')
        return self.real.add_event(did,kind,payload,**kwargs)


async def scenario(sdk,wid,case):
    agent_sdk=FailRequestProxy(sdk) if case=='evidence_write_failure' else sdk
    bridge=LoopGridClaudeAgent(client=agent_sdk,workspace_id=wid,agent_id='claude-security-sandbox')
    tool='mcp__loopgrid_sandbox__sandbox_refund'
    policy='blocked' if case=='blocked' else 'human_approval_required' if case in ('missing_approval','human_rejected','human_approved') else 'auto_allowed'
    did=bridge.start_decision(
        decision_type='sandbox_refund',agent={'id':'claude-security-sandbox'},
        authority={'acting_for':'sandbox','scope':['refund:simulate']},
        model={'name':'claude-hook-security','provider':'test'},
        context={'prompt_version':'v1'},
        proposed_action={'tool':tool,'arguments':{'amount':25}},
        policy={'policy_id':'sandbox-rule','version':'1','decision':policy},
        metadata={'sandbox':True,'real_money_moved':False})['decision_id']
    if case == 'human_rejected':
        bridge.record_human_review(did,reviewer='host-demo',approved=False)
    if case == 'human_approved':
        bridge.record_human_review(did,reviewer='host-demo',approved=True)
    h=bridge.hooks(did)
    A=type('AssistantMessage',(),{})
    m=A();m.model='claude-test';m.content=['sandbox']
    async def msgs(): yield m
    async for _ in h.observe_messages(msgs()):pass
    event={'hook_event_name':'PreToolUse','tool_name':tool,
           'tool_input':{'amount':26 if case=='wrong_arguments' else 25},
           'tool_use_id':f'{case}-{uuid.uuid4().hex}', 'session_id':'sandbox-security'}
    gate=await h.pre_tool_use(event,event['tool_use_id'],{})
    allowed=gate['hookSpecificOutput']['permissionDecision']=='allow'
    assert allowed is (case=='human_approved'), (case,gate,h.last_gate_error)
    count=0
    if allowed:
        count+=1
        receipt={'id':'sandbox-'+uuid.uuid4().hex[:8],'status':'succeeded','amount':25}
        result={**event,'hook_event_name':'PostToolUse','tool_response':receipt}
        assert await h.post_tool_use(result,event['tool_use_id'],{}) == {}
        bridge.record_observed_outcome(did,observer='sandbox',receipt_id=receipt['id'],
                                      outcome={'status':'succeeded','amount':25,'sandbox':True})
    record=sdk.get_decision(did)
    assert record['verification']['valid'] is True,record
    if allowed:
        life=record['summary']['lifecycle']
        status=life.get('state') if isinstance(life,dict) else life
        assert status=='evidence_complete' and record['coverage']['score']==100,record
    print(f'PASS: {case} (sandbox executions: {count}; verification: {record["verification"]["valid"]})')
    return {'case':case,'decisionId':did,'sandboxExecutions':count,
            'verificationValid':record['verification']['valid']}


async def main():
    ready=core_json('/ready')
    assert ready['ready'] is True
    wid=core_json('/api/v1/workspaces',{'name':'claude-security-'+uuid.uuid4().hex[:8]})['workspace_id']
    sdk=LoopGrid(base_url=BASE,workspace_id=wid)
    cases=[]
    for case in ('blocked','missing_approval','human_rejected','human_approved',
                 'wrong_arguments','evidence_write_failure'):
        cases.append(await scenario(sdk,wid,case))
    print(json.dumps({'framework':'claude-agent-sdk','validation':'hook inputs + real LoopGrid Core',
                      'coreVersion':ready.get('version'),'casesPassed':len(cases),
                      'cases':cases,'realMoneyMoved':False},indent=2))
    print('PASS: all six Claude SDK hook security scenarios against Core')


if __name__=='__main__':
    asyncio.run(main())
