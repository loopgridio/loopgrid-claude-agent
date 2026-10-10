"""Deterministic Core substitute. Real Core checks are separate release gates."""
from __future__ import annotations
import threading
import uuid
import pytest


class FakeCore:
    def __init__(self):
        self._lock = threading.RLock()
        self.decisions = {}
        self.fail_on = set()
        self.calls = []

    def record_decision(self, **kwargs):
        with self._lock:
            did = 'dec_' + uuid.uuid4().hex[:12]
            self.decisions[did] = {'policy': None, 'review': None, 'events': [], 'request': kwargs}
            return {'decision_id': did}

    def policy_evaluated(self, did, policy, *, actor_id):
        with self._lock:
            self._maybe_fail('policy_evaluated')
            self.decisions[did]['policy'] = policy['decision']
            self.decisions[did]['events'].append(('policy_evaluated', policy))
            return {'ok': True}

    def human_approved(self, did, reviewer, reason):
        self._maybe_fail('human_approved')
        self.decisions[did]['review'] = True
        self.decisions[did]['events'].append(('human_approved', reviewer))
        return {'ok': True}

    def human_rejected(self, did, reviewer, reason):
        self._maybe_fail('human_rejected')
        self.decisions[did]['review'] = False
        self.decisions[did]['events'].append(('human_rejected', reviewer))
        return {'ok': True}

    def _maybe_fail(self, event):
        if event in self.fail_on:
            raise RuntimeError('simulated Core failure on ' + event)

    def add_event(self, did, kind, payload, *, actor_type, actor_id, idempotency_key):
        with self._lock:
            self._maybe_fail(kind)
            state = self.decisions[did]
            if kind == 'tool_requested':
                if state['policy'] == 'blocked' or state['policy'] is None:
                    raise RuntimeError('Core refuses request without policy')
                if state['policy'] == 'human_approval_required' and state['review'] is not True:
                    raise RuntimeError('Core refuses missing review')
            self.calls.append((did, kind, idempotency_key))
            state['events'].append((kind, payload))
            return {'ok': True}

    def outcome_observed(self, did, payload, *, actor_id):
        self._maybe_fail('outcome_observed')
        self.decisions[did]['events'].append(('outcome_observed', payload))
        return {'ok': True}

    def get_decision(self, did):
        return self.decisions[did]

    def export_evidence(self, did, *, include_payloads=True):
        return b'{"test":true}'


@pytest.fixture
def core():
    return FakeCore()
