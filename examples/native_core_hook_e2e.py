"""Release gate A: REAL Claude SDK HookMatcher + REAL LoopGrid Core, no model key.

The callbacks are exercised with SDK-shaped deterministic inputs, not a Claude
model turn. The separate native_claude_runtime_e2e.py tests real agent execution.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request
import uuid

from loopgrid import LoopGrid
from loopgrid_claude_agent import LoopGridClaudeAgent

BASE = os.getenv('LOOPGRID_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
TIMEOUT = float(os.getenv('LOOPGRID_EXAMPLE_HTTP_TIMEOUT', '30'))


def core_json(path, body=None):
    request = urllib.request.Request(BASE + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={} if body is None else {'Content-Type': 'application/json'},
        method='GET' if body is None else 'POST')
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as r:
            return json.load(r)
    except TimeoutError as exc:
        raise RuntimeError(f'Core timed out after {TIMEOUT:g}s at {path}; POST may have reached server; do not retry automatically') from exc


async def main():
    ready=core_json('/ready')
    assert ready['ready'] is True
    wid=core_json('/api/v1/workspaces',{'name':'claude-native-hook-'+uuid.uuid4().hex[:8]})['workspace_id']
    sdk=LoopGrid(base_url=BASE,workspace_id=wid)
    bridge=LoopGridClaudeAgent(client=sdk,workspace_id=wid,agent_id='claude-sandbox')
    tool='mcp__loopgrid_sandbox__sandbox_refund'
    did=bridge.start_decision(
        decision_type='sandbox_refund',agent={'id':'claude-sandbox','version':'0.1.0'},
        authority={'acting_for':'sandbox-ledger','scope':['refund:simulate'],'limit':30},
        model={'name':'claude-agent-sdk-synthetic-hook-driver','provider':'test'},
        context={'prompt_version':'claude-hook-e2e-v1'},
        proposed_action={'tool':tool,'arguments':{'amount':25}},
        policy={'policy_id':'sandbox-refund-v1','version':'1','decision':'auto_allowed'},
        metadata={'sandbox':True,'real_money_moved':False},
    )['decision_id']
    hooks=bridge.hooks(did)
    opts=hooks.options(max_turns=1)
    assert set(opts.hooks)=={'PreToolUse','PostToolUse','PostToolUseFailure'}
    assert opts.allowed_tools == [tool]

    A=type('AssistantMessage',(),{})
    message=A();message.model='claude-test-model';message.content=['Request sandbox refund']
    async def msgs():
        yield message
    async for _ in hooks.observe_messages(msgs()):
        pass

    call={'hook_event_name':'PreToolUse','tool_name':tool,'tool_input':{'amount':25},
          'tool_use_id':'sandbox-native-1','session_id':'sandbox-session'}
    gate=await hooks.pre_tool_use(call,'sandbox-native-1',{'signal':None})
    assert gate['hookSpecificOutput']['permissionDecision']=='allow',hooks.last_gate_error

    # External action is an explicit sandbox function, NOT run by LoopGrid.
    receipt={'id':'sandbox-'+uuid.uuid4().hex[:8],'amount':25,
             'status':'succeeded','real_money_moved':False}
    post={**call,'hook_event_name':'PostToolUse','tool_response':receipt}
    assert await hooks.post_tool_use(post,'sandbox-native-1',{'signal':None}) == {},hooks.last_post_error
    bridge.record_observed_outcome(did,outcome={'status':'succeeded','amount':25,'sandbox':True,
                                                 'real_money_moved':False},
                                    observer='sandbox-ledger',receipt_id=receipt['id'])
    record=sdk.get_decision(did)
    events=[e['event_type'] for e in record.get('events',[])]
    life=record.get('summary',{}).get('lifecycle',{})
    state=life.get('state') if isinstance(life,dict) else life
    coverage=record.get('coverage',{})
    verify=record.get('verification',{})
    assert state=='evidence_complete',record
    assert coverage.get('score')==100 and coverage.get('complete') is True,record
    assert verify.get('valid') is True and not verify.get('failures',[]),record
    assert events.count('tool_requested')==1 and events.count('tool_executed')==1
    assert events.count('model_completed')>=1 and events.count('outcome_observed')==1
    print(json.dumps({'framework':'claude-agent-sdk','validation':'SDK hook registration + deterministic hook inputs + real Core',
                      'coreVersion':ready.get('version'),'workspaceId':wid,'decisionId':did,
                      'actualSandboxExecutions':1,'lifecycle':state,
                      'coverageScore':coverage.get('score'),'coverageComplete':coverage.get('complete'),
                      'verifyValid':verify.get('valid'),'eventTypes':events,
                      'realMoneyMoved':False},indent=2))
    print('PASS: Claude SDK hook registration -> sandbox action -> LoopGrid Core cryptographic verification')


if __name__=='__main__':
    asyncio.run(main())
