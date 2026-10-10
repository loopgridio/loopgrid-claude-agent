"""Claude Agent SDK native hooks to LoopGrid signed decision evidence.

Claude executes tools. Trusted application policy, review, and downstream receipts
must come from the host, not an agent's generated text. The hook gate runs on
EVERY tool call and denies anything not explicitly bound to the decision.
"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import datetime as dt
import decimal
import hashlib
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping

from .authorization import (
    AuthorizationDenied, AuthorizationStore, InMemoryAuthorizationStore,
    SQLiteAuthorizationStore,
)
from .claims import Claim, ClaimStore, MemoryClaimStore, SQLiteClaimStore

FRAMEWORK = 'claude-agent-sdk'
VERSION = '0.1.0'


class EvidenceGateError(RuntimeError):
    """An external action must not proceed without required evidence and authority."""


class EvidencePersistenceError(EvidenceGateError):
    """An action may have occurred; post-execution evidence needs reconciliation."""


@dataclass(frozen=True)
class ReviewVerdict:
    """Authentic decision supplied by the host's authenticated review service."""
    reviewer_id: str
    approved: bool
    reason: str = ''


ApprovalHandler = Callable[[str, str, Mapping[str, Any]], Awaitable[ReviewVerdict]]


def _required(name: str, value: Any) -> None:
    if value is None or value == '' or value == {} or value == []:
        raise ValueError(f'{name} is required')


def _ready(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int, float)):
        return value
    if isinstance(value, decimal.Decimal):
        return {'decimal': str(value)}
    if isinstance(value, (dt.date, dt.time)):
        return {'iso': value.isoformat()}
    if isinstance(value, (bytes, bytearray)):
        return {'base64': base64.b64encode(value).decode('ascii')}
    if isinstance(value, uuid.UUID):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _ready(dataclasses.asdict(value))
    if hasattr(value, 'model_dump') and callable(value.model_dump):
        return _ready(value.model_dump(mode='json'))
    if isinstance(value, Mapping):
        return {str(k): _ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_ready(v) for v in value]
    raise TypeError(f'unsupported evidence value: {type(value).__name__}')


def _canonical(value: Any) -> bytes:
    return json.dumps(_ready(value), sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(',', ':')).encode('utf-8')


def _commit(value: Any) -> dict[str, Any]:
    raw = _canonical(value)
    return {'algorithm': 'sha256', 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def _idem(*values: Any) -> str:
    return 'lg-claude-' + hashlib.sha256(_canonical(values)).hexdigest()


def _deny(reason: str) -> dict[str, Any]:
    # Never return an empty dict on a gate error: Claude would continue.
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                                   'permissionDecision': 'deny',
                                   'permissionDecisionReason': reason}}


def _halt(reason: str) -> dict[str, Any]:
    # This occurs AFTER an action may have run: do NOT claim the action stopped.
    return {'continue_': False, 'stopReason': reason, 'systemMessage': reason}


class LoopGridClaudeAgent:
    """Bridge for one application-authenticated decision and its native tool hooks."""

    def __init__(self, *, client: Any | None = None,
                 base_url: str = 'http://127.0.0.1:8000', api_key: str | None = None,
                 workspace_id: str = 'default', agent_id: str = 'claude-agent',
                 authorization_store: AuthorizationStore | None = None,
                 claim_store: ClaimStore | None = None) -> None:
        _required('workspace_id', workspace_id)
        _required('agent_id', agent_id)
        if client is None:
            from loopgrid import LoopGrid
            client = LoopGrid(base_url=base_url, api_key=api_key, workspace_id=workspace_id)
        self.client = client
        self.workspace_id = workspace_id
        self.agent_id = agent_id
        self.authorizations = authorization_store or InMemoryAuthorizationStore()
        self.claims = claim_store or MemoryClaimStore()
        self._lock = threading.RLock()

    @classmethod
    def with_sqlite(cls, *, sqlite_path: str, **kwargs: Any) -> 'LoopGridClaudeAgent':
        """Use one durable local SQLite file for both authorization and claim ledgers."""
        return cls(authorization_store=SQLiteAuthorizationStore(sqlite_path),
                   claim_store=SQLiteClaimStore(sqlite_path), **kwargs)

    def start_decision(self, *, decision_type: str, agent: Mapping[str, Any],
                       authority: Mapping[str, Any], model: Mapping[str, Any],
                       context: Mapping[str, Any], proposed_action: Mapping[str, Any],
                       policy: Mapping[str, Any] | None = None,
                       metadata: Mapping[str, Any] | None = None,
                       privacy_mode: str = 'redacted',
                       idempotency_key: str | None = None,
                       max_tool_calls: int = 1) -> dict[str, Any]:
        _required('decision_type', decision_type)
        _required('agent.id', agent.get('id'))
        _required('authority', authority)
        _required('model.name', model.get('name'))
        _required('context', context)
        _required('proposed_action', proposed_action)
        name, args = proposed_action.get('tool'), proposed_action.get('arguments')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('proposed_action.tool must be a nonempty tool name')
        if not isinstance(args, Mapping):
            raise ValueError('proposed_action.arguments must be a mapping')
        if isinstance(max_tool_calls, bool) or not isinstance(max_tool_calls, int) or max_tool_calls < 1:
            raise ValueError('max_tool_calls must be a positive integer')
        meta = {**dict(metadata or {}), 'framework': FRAMEWORK,
                'integration': 'loopgrid-claude-agent', 'integration_version': VERSION}
        event = self.client.record_decision(
            decision_type=decision_type, agent=dict(agent), authority=dict(authority),
            model=dict(model), context=dict(context), proposed_action=dict(proposed_action),
            metadata=meta, privacy_mode=privacy_mode, idempotency_key=idempotency_key,
        )
        did = event['decision_id']
        self.authorizations.register(self.workspace_id, did, name, _commit(args)['sha256'], max_tool_calls)
        if policy is not None:
            self.record_policy(did, policy)
        return event

    def record_policy(self, decision_id: str, policy: Mapping[str, Any]) -> Any:
        p = dict(policy)
        if p.get('decision') not in ('auto_allowed', 'human_approval_required', 'blocked'):
            raise ValueError('policy.decision invalid')
        _required('policy_id', p.get('policy_id'))
        _required('policy.version', p.get('version'))
        with self._lock:
            state = self.authorizations.get(self.workspace_id, decision_id)
            if state is None or state.policy is not None:
                raise ValueError('unknown decision or existing policy')
            response = self.client.policy_evaluated(decision_id, p, actor_id=str(p['policy_id']))
            self.authorizations.set_policy(self.workspace_id, decision_id, p['decision'])
        return response

    def record_human_review(self, decision_id: str, *, reviewer: str,
                            approved: bool, reason: str = '') -> Any:
        """Host must authenticate reviewer and supply the authentic verdict."""
        _required('reviewer', reviewer)
        if not isinstance(approved, bool):
            raise ValueError('approved must be a bool')
        with self._lock:
            state = self.authorizations.get(self.workspace_id, decision_id)
            if state is None or state.policy != 'human_approval_required':
                raise ValueError('requires human_approval_required policy')
            if state.approved or state.rejected:
                raise ValueError('review already resolved')
            if approved:
                result = self.client.human_approved(decision_id, reviewer, reason)
            else:
                result = self.client.human_rejected(decision_id, reviewer, reason)
            self.authorizations.set_review(self.workspace_id, decision_id, approved)
        return result

    def record_observed_outcome(self, decision_id: str, *, outcome: Mapping[str, Any],
                                observer: str, receipt_id: str) -> Any:
        _required('observer', observer)
        _required('receipt_id', receipt_id)
        _required('outcome', outcome)
        # An actual observed outcome can exist only after a corresponding tool result
        # was durably recorded. Never fill this from agent-generated claims.
        if not self._has_recorded_call(decision_id):
            raise EvidenceGateError('no persisted tool result for decision; reconcile before outcome')
        return self.client.outcome_observed(decision_id,
                                            {**dict(outcome), 'observer': observer,
                                             'receipt_id': receipt_id}, actor_id=observer)

    def _has_recorded_call(self, decision_id: str) -> bool:
        # Durable success state survives a process restart in SQLite mode.
        return bool(self.claims.successful(self.workspace_id, decision_id))

    def pending_reconciliations(self) -> list[Claim]:
        return self.claims.needing_reconciliation(self.workspace_id)

    def hooks(self, decision_id: str, *, approval_handler: ApprovalHandler | None = None) -> 'LoopGridClaudeHooks':
        if self.authorizations.get(self.workspace_id, decision_id) is None:
            raise ValueError('decision not found in authorization store')
        return LoopGridClaudeHooks(self, decision_id, approval_handler=approval_handler)

    def get_decision(self, decision_id: str) -> dict[str, Any]:
        return self.client.get_decision(decision_id)

    def export_evidence(self, decision_id: str, *, include_payloads: bool = True) -> bytes:
        return self.client.export_evidence(decision_id, include_payloads=include_payloads)

    async def _event(self, decision_id: str, event_type: str, payload: dict[str, Any],
                     actor_type: str, actor_id: str, key: str) -> Any:
        return await asyncio.to_thread(self.client.add_event, decision_id, event_type, payload,
                                       actor_type=actor_type, actor_id=actor_id,
                                       idempotency_key=key)


class LoopGridClaudeHooks:
    """Native Claude Agent SDK hooks bound to a specific LoopGrid decision.

    Install via `options()` to register a wildcard gate covering built-in, MCP
    and subagent tool invocations. Do not modify options.hooks after creation.
    """

    def __init__(self, bridge: LoopGridClaudeAgent, decision_id: str, *,
                 approval_handler: ApprovalHandler | None = None) -> None:
        self.bridge = bridge
        self.decision_id = decision_id
        self.approval_handler = approval_handler
        self.last_gate_error: str | None = None
        self.last_post_error: str | None = None
        self._model_ordinal = 0

    def options(self, **kwargs: Any) -> Any:
        """Construct SDK-native options; narrow allowed tools, no shadowing hooks.

        Hooks must never be casually merged with arbitrary third-party hooks
        which can rewrite tool_input after an authorization commitment.
        """
        forbidden = {'hooks', 'can_use_tool', 'allowed_tools', 'disallowed_tools',
                     'permission_mode', 'setting_sources'} & set(kwargs)
        if forbidden:
            raise ValueError('security-controlled SDK options cannot be overridden: ' + ', '.join(sorted(forbidden)))
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
        state = self.bridge.authorizations.get(self.bridge.workspace_id, self.decision_id)
        if state is None:
            raise ValueError('no authorized decision')
        return ClaudeAgentOptions(
            **kwargs, setting_sources=[], permission_mode='dontAsk',
            allowed_tools=[state.name],
            hooks={
                'PreToolUse': [HookMatcher(matcher=None, hooks=[self.pre_tool_use])],
                'PostToolUse': [HookMatcher(matcher=None, hooks=[self.post_tool_use])],
                'PostToolUseFailure': [HookMatcher(matcher=None, hooks=[self.post_tool_use_failure])],
            },
        )

    def _validated_call(self, input_data: Mapping[str, Any], tool_use_id: str | None,
                        expected_event: str) -> tuple[str, dict[str, Any], str]:
        if input_data.get('hook_event_name') != expected_event:
            raise EvidenceGateError('unexpected SDK hook event')
        name = input_data.get('tool_name')
        args = input_data.get('tool_input')
        uid = tool_use_id or input_data.get('tool_use_id')
        if not isinstance(name, str) or not name.strip():
            raise EvidenceGateError('tool name is missing')
        if not isinstance(args, dict):
            raise EvidenceGateError('tool input must be a mapping')
        if not isinstance(uid, str) or not uid.strip():
            raise EvidenceGateError('tool-use identifier is required for replay protection')
        if tool_use_id and input_data.get('tool_use_id') and tool_use_id != input_data['tool_use_id']:
            raise EvidenceGateError('SDK tool-use identifier mismatch')
        return name, args, uid

    async def _trusted_review(self, name: str, args: dict[str, Any]) -> None:
        state = self.bridge.authorizations.get(self.bridge.workspace_id, self.decision_id)
        if state is None:
            raise EvidenceGateError('authorization unavailable')
        if state.policy != 'human_approval_required' or state.approved or state.rejected:
            return
        if self.approval_handler is None:
            raise EvidenceGateError('host approval required before execution')
        verdict = await self.approval_handler(self.decision_id, name, args)
        if not isinstance(verdict, ReviewVerdict) or not verdict.reviewer_id or not isinstance(verdict.approved, bool):
            raise EvidenceGateError('host approval handler did not return an authenticated verdict')
        await asyncio.to_thread(self.bridge.record_human_review, self.decision_id,
                                reviewer=verdict.reviewer_id, approved=verdict.approved,
                                reason=verdict.reason)
        if not verdict.approved:
            raise EvidenceGateError('authenticated host reviewer rejected the tool call')

    async def pre_tool_use(self, input_data: dict[str, Any], tool_use_id: str | None,
                           context: Any) -> dict[str, Any]:
        """Fail-closed before action; return SDK-native PreToolUse deny on any error."""
        try:
            name, args, uid = self._validated_call(input_data, tool_use_id, 'PreToolUse')
            state = self.bridge.authorizations.get(self.bridge.workspace_id, self.decision_id)
            args_hash = _commit(args)['sha256']
            # Refuse mismatched tool/args BEFORE prompting a trusted reviewer.
            if state is None or state.name != name or state.arguments_sha256 != args_hash:
                raise EvidenceGateError('tool name or arguments differ from authorized decision')
            # Do not emit misleading tool_requested evidence or prompt a reviewer
            # when this request is already known to be a replay/over-limit.
            # Atomic claim/counter writes below still enforce concurrent races.
            if state.remaining <= 0:
                raise EvidenceGateError('authorized invocation limit reached')
            if self.bridge.claims.get(self.bridge.workspace_id, self.decision_id, uid) is not None:
                raise EvidenceGateError('tool-use identifier already reserved (replay denied)')
            await self._trusted_review(name, args)
            common = {'framework': FRAMEWORK,
                      'source': 'claude_agent_sdk.PreToolUse',
                      'tool_name': name, 'tool_use_id': uid,
                      'session_id': str(input_data.get('session_id') or ''),
                      'agent_id': str(input_data.get('agent_id') or self.bridge.agent_id),
                      'arguments_commitment': _commit(args)}
            # Core may refuse tool_requested while blocked/awaiting review.
            # Such a failure MUST deny the action.
            await self.bridge._event(self.decision_id, 'tool_requested', common,
                                     'tool', name, _idem(self.decision_id, 'request', uid))
            claim = Claim(self.bridge.workspace_id, self.decision_id, uid, name, args_hash, 'reserved')
            self.bridge.claims.reserve(claim)
            # Durable invocation count: no duplicate/replayed invocation after restart.
            # A refused reservation never authorized execution: cancel only this
            # newly created claim to avoid false downstream reconciliation.
            try:
                self.bridge.authorizations.reserve(self.bridge.workspace_id, self.decision_id, name, args_hash)
            except BaseException:
                try:
                    self.bridge.claims.abort_pre_execution(self.bridge.workspace_id, self.decision_id, uid)
                except Exception:
                    # Cleanup errors preserve the claim for human investigation;
                    # they can never turn a denied action into an allowed action.
                    pass
                raise
        except Exception as exc:
            self.last_gate_error = f'{type(exc).__name__}: {exc}'
            return _deny('LoopGrid action not authorized or required evidence unavailable; execution denied')
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                                       'permissionDecision': 'allow',
                                       'permissionDecisionReason': 'Host policy/review and LoopGrid evidence gate passed'}}

    def _claim_for_post(self, input_data: dict[str, Any], tool_use_id: str | None,
                        expected_event: str) -> tuple[str, dict[str, Any], str, Claim]:
        name, args, uid = self._validated_call(input_data, tool_use_id, expected_event)
        claim = self.bridge.claims.get(self.bridge.workspace_id, self.decision_id, uid)
        if claim is None or claim.state != 'reserved':
            raise EvidenceGateError('tool result has no unique authorized reservation')
        if claim.tool_name != name or claim.arguments_sha256 != _commit(args)['sha256']:
            raise EvidenceGateError('post-tool name/arguments do not match authorized request')
        return name, args, uid, claim

    async def post_tool_use(self, input_data: dict[str, Any], tool_use_id: str | None,
                            context: Any) -> dict[str, Any]:
        """Persist the actual returned tool result before the agent proceeds."""
        uid = str(tool_use_id or input_data.get('tool_use_id') or '')
        try:
            name, args, uid, claim = self._claim_for_post(input_data, tool_use_id, 'PostToolUse')
            result = _commit(input_data.get('tool_response'))
            payload = {'framework': FRAMEWORK, 'source': 'claude_agent_sdk.PostToolUse',
                       'tool_name': name, 'tool_use_id': uid, 'arguments_commitment': _commit(args),
                       'result_commitment': result, 'status': 'returned',
                       'external_outcome_verified': False}
            await self.bridge._event(self.decision_id, 'tool_executed', payload,
                                     'tool', name, _idem(self.decision_id, 'executed', uid))
            self.bridge.claims.finish(self.bridge.workspace_id, self.decision_id, uid,
                                      'recorded', result['sha256'])
            return {}
        except Exception as exc:
            self.last_post_error = f'{type(exc).__name__}: {exc}'
            # The tool may already have changed external state, so no retry.
            try:
                claim = self.bridge.claims.get(self.bridge.workspace_id, self.decision_id, uid)
                if claim is not None and claim.state == 'reserved':
                    self.bridge.claims.finish(self.bridge.workspace_id, self.decision_id, uid, 'reconcile')
            except Exception:
                pass
            return _halt('LoopGrid tool result evidence incomplete after execution; reconcile external state before retry')

    async def post_tool_use_failure(self, input_data: dict[str, Any], tool_use_id: str | None,
                                    context: Any) -> dict[str, Any]:
        """Failure is not a successful tool outcome; halt and require review."""
        uid = str(tool_use_id or input_data.get('tool_use_id') or '')
        try:
            _name, _args, uid, _claim = self._claim_for_post(input_data, tool_use_id, 'PostToolUseFailure')
            # CLI reports an error but this does not establish downstream side effects.
            # Do not fabricate tool_executed or outcome_observed events.
            self.bridge.claims.finish(self.bridge.workspace_id, self.decision_id, uid,
                                      'failed', _commit(str(input_data.get('error')))['sha256'])
        except Exception as exc:
            self.last_post_error = f'{type(exc).__name__}: {exc}'
        return _halt('Claude tool failed; downstream side effects are unknown; reconcile before retry')

    async def observe_messages(self, messages: AsyncIterator[Any]) -> AsyncIterator[Any]:
        """Wrap SDK message stream to record authoritative assistant model events.

        Consumer should *fully exhaust* the iterator to capture all model events.
        """
        async for message in messages:
            if type(message).__name__ == 'AssistantMessage':
                self._model_ordinal += 1
                payload = {'framework': FRAMEWORK, 'source': 'claude_agent_sdk.AssistantMessage',
                           'status': 'completed', 'model': str(getattr(message, 'model', 'unknown')),
                           'response_commitment': _commit(getattr(message, 'content', []))}
                await self.bridge._event(self.decision_id, 'model_completed', payload,
                                         'agent', self.bridge.agent_id,
                                         _idem(self.decision_id, 'assistant', self._model_ordinal))
            yield message


__all__ = ['LoopGridClaudeAgent', 'LoopGridClaudeHooks', 'ReviewVerdict',
           'EvidenceGateError', 'EvidencePersistenceError',
           'InMemoryAuthorizationStore', 'SQLiteAuthorizationStore',
           'MemoryClaimStore', 'SQLiteClaimStore', 'FRAMEWORK', 'VERSION']
