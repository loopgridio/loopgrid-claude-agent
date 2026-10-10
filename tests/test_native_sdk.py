"""Real SDK options construction; no Anthropic keys or network calls needed."""
from __future__ import annotations
import pytest
from loopgrid_claude_agent import LoopGridClaudeAgent


def test_real_claude_sdk_options():
    pytest.importorskip('claude_agent_sdk')
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

    class FakeCore:
        def record_decision(self,**kwargs): return {'decision_id':'dec_sdk_smoke'}
        def policy_evaluated(self,*args,**kwargs): return {'ok':True}

    bridge=LoopGridClaudeAgent(client=FakeCore(),workspace_id='sdk',agent_id='sdk')
    d=bridge.start_decision(decision_type='sandbox',agent={'id':'sdk'},authority={'acting_for':'sandbox'},
                            model={'name':'claude'},context={'prompt_version':'v1'},
                            proposed_action={'tool':'mcp__sandbox__test','arguments':{'amount':25}},
                            policy={'policy_id':'p','version':'1','decision':'auto_allowed'})
    opts=bridge.hooks(d['decision_id']).options(max_turns=2)
    assert isinstance(opts,ClaudeAgentOptions)
    assert opts.hooks['PreToolUse'][0].hooks and isinstance(opts.hooks['PreToolUse'][0],HookMatcher)
    assert opts.permission_mode=='dontAsk' and opts.allowed_tools == ['mcp__sandbox__test']
