"""Real Core post-execution interruption test; no real financial transaction."""
from __future__ import annotations
import asyncio
import json
import uuid
from loopgrid import LoopGrid
from loopgrid_claude_agent import LoopGridClaudeAgent
from native_core_hook_e2e import BASE, core_json


class PostFailureProxy:
    def __init__(self, real): self.real=real
    def __getattr__(self,name): return getattr(self.real,name)
    def add_event(self, did,kind,payload,**kwargs):
        if kind=='tool_executed':
            raise ConnectionError('synthetic post-tool Core evidence failure')
        return self.real.add_event(did,kind,payload,**kwargs)


async def main():
    ready=core_json('/ready')
    assert ready['ready'] is True
    wid=core_json('/api/v1/workspaces',{'name':'claude-recovery-'+uuid.uuid4().hex[:8]})['workspace_id']
    sdk=LoopGrid(base_url=BASE,workspace_id=wid)
    bridge=LoopGridClaudeAgent(client=PostFailureProxy(sdk),workspace_id=wid,agent_id='claude-sandbox')
    tool='mcp__loopgrid_sandbox__sandbox_refund'
    did=bridge.start_decision(
        decision_type='sandbox_refund',agent={'id':'claude-sandbox'},
        authority={'acting_for':'sandbox','scope':['refund:simulate']},
        model={'name':'claude-hook-test','provider':'test'},
        context={'prompt_version':'v1'},
        proposed_action={'tool':tool,'arguments':{'amount':25}},
        policy={'policy_id':'test-policy','version':'1','decision':'auto_allowed'},
        metadata={'sandbox':True,'real_money_moved':False})['decision_id']
    hooks=bridge.hooks(did)
    event={'hook_event_name':'PreToolUse','tool_name':tool,'tool_input':{'amount':25},
           'tool_use_id':'call-'+uuid.uuid4().hex,'session_id':'sandbox'}
    result=await hooks.pre_tool_use(event,event['tool_use_id'],{})
    assert result['hookSpecificOutput']['permissionDecision']=='allow',hooks.last_gate_error
    receipt={'id':'sandbox-'+uuid.uuid4().hex[:8],'status':'succeeded','amount':25}
    action_count=1  # explicit simulation performed after permission and before post hook
    final=await hooks.post_tool_use({**event,'hook_event_name':'PostToolUse',
                                     'tool_response':receipt},event['tool_use_id'],{})
    assert final.get('continue_') is False,final
    assert len(bridge.pending_reconciliations())==1
    record=sdk.get_decision(did)
    names=[e['event_type'] for e in record.get('events',[])]
    assert 'tool_requested' in names and 'tool_executed' not in names
    assert 'outcome_observed' not in names
    assert record['verification']['valid'] is True,record
    print(json.dumps({'decisionId':did,'workspaceId':wid,
                      'actualSandboxExecutions':action_count,
                      'toolExecutionEvidencePersisted':False,
                      'downstreamReconciliationRequired':True,
                      'verificationValidForPersistedEvents':True,
                      'realMoneyMoved':False},indent=2))
    print('PASS: action occurred; evidence failed; Claude hook halted without false completion')


if __name__=='__main__':
    asyncio.run(main())
