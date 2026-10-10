"""Gate B: REAL Claude Agent SDK turn + sandbox MCP + LoopGrid Core.

This is a charged *live-model* test, never a deterministic offline unit test.
Only a synthetic in-process refund is executed; no real money moves.
SDK errors and tool-selection details are surfaced before asserting execution.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from importlib.metadata import version

from claude_agent_sdk import ClaudeSDKClient, create_sdk_mcp_server, tool
from loopgrid import LoopGrid
from loopgrid_claude_agent import LoopGridClaudeAgent
from native_core_hook_e2e import BASE, core_json

TOOL = 'mcp__loopgrid_sandbox__sandbox_refund'


def _preview(value, limit=450):
    # Only print bounded text from this synthetic sandbox demo. Never use raw
    # model/tool messages as production audit logs or capture sensitive values.
    text = str(value)
    return text[:limit] + ('...' if len(text) > limit else '')


def _describe_message(message):
    kind = type(message).__name__
    result = {'type': kind}
    if kind == 'ResultMessage':
        result.update({
            'subtype': getattr(message, 'subtype', None),
            'is_error': getattr(message, 'is_error', None),
            'num_turns': getattr(message, 'num_turns', None),
            'errors': [_preview(x) for x in (getattr(message, 'errors', None) or [])],
            'result_preview': _preview(getattr(message, 'result', None)),
        })
    elif kind == 'SystemMessage':
        result['subtype'] = getattr(message, 'subtype', None)
        # Do not dump full initialization details or session credentials.
    elif kind == 'AssistantMessage':
        content = []
        for block in (getattr(message, 'content', None) or []):
            btype = type(block).__name__
            if btype == 'ToolUseBlock':
                content.append({'type': btype, 'name': getattr(block, 'name', None),
                                'id': getattr(block, 'id', None)})
            elif btype == 'TextBlock':
                content.append({'type': btype, 'preview': _preview(getattr(block, 'text', ''), 300)})
            # Deliberately omit ThinkingBlock and all other model internals.
        result['blocks'] = content
    return result


def _diagnosis(messages, hooks, receipts):
    if receipts:
        return 'sandbox tool executed'
    if hooks.last_gate_error:
        return 'pre-tool hook denied execution; inspect gateError and exact tool/arguments'
    if hooks.last_post_error:
        return 'tool executed but post-hook evidence failed; reconciliation required'
    result_errors = [x for x in messages if x.get('type') == 'ResultMessage'
                     and (x.get('is_error') or x.get('errors') or (str(x.get('subtype') or '').startswith('error_')))]
    if result_errors:
        return 'Claude runtime returned a model/CLI error: check SDK authentication, model access, and result details'
    attempts = [b.get('name') for m in messages if m.get('type') == 'AssistantMessage'
                for b in m.get('blocks', []) if b.get('type') == 'ToolUseBlock']
    if not attempts:
        return 'Claude did not request any tool: inspect assistant text, MCP status, model access and prompt'
    if TOOL not in attempts:
        return 'Claude requested a different tool; exact-action guard should deny it'
    return 'Claude requested the MCP tool but there is no receipt: inspect MCP status, permissions and result blocks'


async def main():
    ready = core_json('/ready')
    assert ready['ready'] is True
    wid = core_json('/api/v1/workspaces', {'name': 'claude-agent-live-'+uuid.uuid4().hex[:8]})['workspace_id']
    sdk = LoopGrid(base_url=BASE, workspace_id=wid)
    bridge = LoopGridClaudeAgent(client=sdk, workspace_id=wid, agent_id='claude-live-sandbox')
    receipts = []

    @tool('sandbox_refund', 'Simulate a refund; amount must be exactly 25', {'amount': int})
    async def sandbox_refund(args):
        receipt = {'id': 'sandbox-'+uuid.uuid4().hex[:10], 'amount': args['amount'],
                   'status': 'succeeded', 'sandbox': True, 'real_money_moved': False}
        receipts.append(receipt)
        return {'content': [{'type': 'text', 'text': json.dumps(receipt)}]}

    server = create_sdk_mcp_server(name='loopgrid_sandbox', version='1.0.0', tools=[sandbox_refund])
    did = bridge.start_decision(
        decision_type='sandbox_refund',
        agent={'id': 'claude-live-sandbox', 'version': '0.1.0'},
        authority={'acting_for': 'sandbox-ledger', 'scope': ['refund:simulate'], 'limit': 30},
        model={'name': 'claude-agent-sdk', 'provider': 'anthropic'},
        context={'prompt_version': 'claude-agent-sdk-e2e-v2'},
        proposed_action={'tool': TOOL, 'arguments': {'amount': 25}},
        policy={'policy_id': 'sandbox-refund-v1', 'version': '1', 'decision': 'auto_allowed'},
        metadata={'sandbox': True, 'real_money_moved': False},
    )['decision_id']
    hooks = bridge.hooks(did)
    options = hooks.options(max_turns=5, mcp_servers={'loopgrid_sandbox': server})
    messages = []
    print(f'Claude SDK version: {version("claude-agent-sdk")}', flush=True)
    print(f'Authorized sandbox tool: {TOOL}', flush=True)
    print(f'LoopGrid decision: {did}', flush=True)

    async with ClaudeSDKClient(options=options) as client:
        # A connected SDK MCP server should advertise the exact expected tool.
        # Print only connection state/tool names; no secrets or full config.
        try:
            status = await client.get_mcp_status()
            servers = status.get('mcpServers', [])
            safe_status = [{'name': x.get('name'), 'status': x.get('status'),
                            'error': _preview(x.get('error'), 250) if x.get('error') else None,
                            'tools': [t.get('name') for t in (x.get('tools') or [])]}
                           for x in servers]
            print('MCP connection status: ' + json.dumps(safe_status), flush=True)
            found = [x for x in servers if x.get('name') == 'loopgrid_sandbox']
            if found and found[0].get('status') in ('failed', 'needs-auth', 'disabled'):
                raise RuntimeError('Sandbox MCP is not connected; see MCP connection status above')
        except RuntimeError:
            raise
        except Exception as exc:
            print('MCP connection preflight unavailable: '
                  f'{type(exc).__name__}: {_preview(exc, 200)}', flush=True)

        await client.query(
            'This is a controlled local sandbox validation. Invoke the available MCP tool '
            '`mcp__loopgrid_sandbox__sandbox_refund` exactly once with JSON arguments '
            '{"amount": 25}. Do not call any other tools and do not merely describe '
            'what you would do. After receiving its response, report the sandbox receipt.'
        )
        async for message in hooks.observe_messages(client.receive_response()):
            info = _describe_message(message)
            messages.append(info)
            print('SDK message: ' + json.dumps(info, ensure_ascii=False, default=str), flush=True)

    diagnosis = _diagnosis(messages, hooks, receipts)
    if len(receipts) != 1 or hooks.last_gate_error or hooks.last_post_error:
        print(json.dumps({
            'status': 'FAIL: real Claude tool-use gate not satisfied',
            'diagnosis': diagnosis,
            'actualSandboxExecutions': len(receipts),
            'gateError': hooks.last_gate_error,
            'postError': hooks.last_post_error,
            'resultMessages': [m for m in messages if m.get('type') == 'ResultMessage'],
            'decisionId': did,
            'realMoneyMoved': False,
        }, indent=2), flush=True)
        raise RuntimeError('Live Claude SDK E2E did not execute and verify exactly one tool call. '
                           'Do not mark live SDK runtime E2E validated until cause resolved.')

    receipt = receipts[0]
    bridge.record_observed_outcome(did, outcome={
        'status': receipt['status'], 'amount': receipt['amount'],
        'sandbox': True, 'real_money_moved': False,
    }, observer='sandbox-ledger', receipt_id=receipt['id'])
    record = sdk.get_decision(did)
    state_data = record.get('summary', {}).get('lifecycle', {})
    state = state_data.get('state') if isinstance(state_data, dict) else state_data
    coverage = record.get('coverage', {})
    verify = record.get('verification', {})
    assert state == 'evidence_complete', record
    assert coverage.get('score') == 100 and coverage.get('complete') is True, record
    assert verify.get('valid') is True and not verify.get('failures', []), record
    print(json.dumps({
        'framework': 'claude-agent-sdk',
        'validation': 'real Claude model + native MCP sandbox + Core',
        'coreVersion': ready.get('version'), 'decisionId': did, 'workspaceId': wid,
        'actualSandboxExecutions': len(receipts), 'lifecycle': state,
        'coverageScore': coverage.get('score'), 'verifyValid': verify.get('valid'),
        'realMoneyMoved': False,
    }, indent=2))
    print('PASS: native Claude Agent SDK -> sandbox MCP action -> LoopGrid signed Core verification')


if __name__ == '__main__':
    asyncio.run(main())
