"""Durable tool-call correlation and reconciliation flags.

Claims never automatically expire or reset. An ambiguous timed-out action requires
an authoritative external receipt before an operator authorizes a new attempt.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import sqlite3
import threading


@dataclass(frozen=True)
class Claim:
    workspace: str
    decision_id: str
    call_id: str
    tool_name: str
    arguments_sha256: str
    state: str  # reserved, recorded, reconcile, failed
    result_sha256: str | None = None


class ClaimStore(Protocol):
    def reserve(self, claim: Claim) -> None: ...
    def get(self, workspace: str, decision_id: str, call_id: str) -> Claim | None: ...
    def abort_pre_execution(self, workspace: str, decision_id: str, call_id: str) -> None: ...
    def finish(self, workspace: str, decision_id: str, call_id: str, state: str,
               result_sha256: str | None = None) -> None: ...
    def needing_reconciliation(self, workspace: str) -> list[Claim]: ...
    def successful(self, workspace: str, decision_id: str) -> list[Claim]: ...


class MemoryClaimStore:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], Claim] = {}
        self._lock = threading.RLock()

    def reserve(self, claim: Claim) -> None:
        with self._lock:
            key = claim.workspace, claim.decision_id, claim.call_id
            if key in self._values:
                raise ValueError('tool-use ID already reserved (replay denied)')
            self._values[key] = claim

    def get(self, workspace: str, decision_id: str, call_id: str) -> Claim | None:
        with self._lock:
            return self._values.get((workspace, decision_id, call_id))

    def abort_pre_execution(self, workspace: str, decision_id: str, call_id: str) -> None:
        """Cancel a claim only when the pre-execution authorization was denied."""
        with self._lock:
            key = workspace, decision_id, call_id
            old = self._values.get(key)
            if old is None or old.state != 'reserved':
                raise ValueError('pre-execution claim missing or finalized')
            del self._values[key]

    def finish(self, workspace: str, decision_id: str, call_id: str, state: str,
               result_sha256: str | None = None) -> None:
        if state not in ('recorded', 'reconcile', 'failed'):
            raise ValueError('invalid claim state')
        with self._lock:
            key = workspace, decision_id, call_id
            old = self._values.get(key)
            if old is None or old.state != 'reserved':
                raise ValueError('claim missing or already finalized')
            from dataclasses import replace
            self._values[key] = replace(old, state=state, result_sha256=result_sha256)

    def needing_reconciliation(self, workspace: str) -> list[Claim]:
        with self._lock:
            return [v for v in self._values.values()
                    if v.workspace == workspace and v.state in ('reserved', 'reconcile', 'failed')]

    def successful(self, workspace: str, decision_id: str) -> list[Claim]:
        with self._lock:
            return [v for v in self._values.values() if v.workspace == workspace
                    and v.decision_id == decision_id and v.state == 'recorded']


class SQLiteClaimStore:
    """Atomic claim uniqueness for multiple processes sharing a *local* SQLite file."""
    def __init__(self, path: str | Path) -> None:
        if str(path) == ':memory:':
            raise ValueError('persistent absolute/local path required')
        self.path = str(Path(path).expanduser().resolve())
        with closing(self._connect()) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS claude_tool_claims (
                workspace TEXT NOT NULL, decision TEXT NOT NULL, call_id TEXT NOT NULL,
                tool TEXT NOT NULL, args_sha256 TEXT NOT NULL, state TEXT NOT NULL,
                result_sha256 TEXT,
                PRIMARY KEY(workspace, decision, call_id))''')

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        con.execute('PRAGMA busy_timeout=15000')
        return con

    def reserve(self, claim: Claim) -> None:
        with closing(self._connect()) as con:
            con.execute('''INSERT INTO claude_tool_claims
                (workspace,decision,call_id,tool,args_sha256,state)
                VALUES (?,?,?,?,?,?)''',
                (claim.workspace, claim.decision_id, claim.call_id, claim.tool_name,
                 claim.arguments_sha256, 'reserved'))

    @staticmethod
    def _read(row: tuple | None) -> Claim | None:
        return Claim(*row) if row is not None else None

    def get(self, workspace: str, decision_id: str, call_id: str) -> Claim | None:
        with closing(self._connect()) as con:
            return self._read(con.execute('''SELECT workspace,decision,call_id,tool,args_sha256,state,result_sha256
                FROM claude_tool_claims WHERE workspace=? AND decision=? AND call_id=?''',
                (workspace, decision_id, call_id)).fetchone())

    def abort_pre_execution(self, workspace: str, decision_id: str, call_id: str) -> None:
        """Atomically cancel a claim denied before tool execution was authorized."""
        with closing(self._connect()) as con:
            n = con.execute("""DELETE FROM claude_tool_claims
                WHERE workspace=? AND decision=? AND call_id=? AND state='reserved'""",
                (workspace, decision_id, call_id)).rowcount
            if n != 1:
                raise ValueError('pre-execution claim missing or finalized')

    def finish(self, workspace: str, decision_id: str, call_id: str, state: str,
               result_sha256: str | None = None) -> None:
        if state not in ('recorded', 'reconcile', 'failed'):
            raise ValueError('invalid claim state')
        with closing(self._connect()) as con:
            n = con.execute('''UPDATE claude_tool_claims SET state=?,result_sha256=?
                 WHERE workspace=? AND decision=? AND call_id=? AND state='reserved' ''',
                (state, result_sha256, workspace, decision_id, call_id)).rowcount
            if n != 1:
                raise ValueError('claim missing or already finalized')

    def needing_reconciliation(self, workspace: str) -> list[Claim]:
        with closing(self._connect()) as con:
            rows = con.execute('''SELECT workspace,decision,call_id,tool,args_sha256,state,result_sha256
                 FROM claude_tool_claims WHERE workspace=? AND state IN ('reserved','reconcile','failed')''',
                 (workspace,)).fetchall()
            return [Claim(*row) for row in rows]

    def successful(self, workspace: str, decision_id: str) -> list[Claim]:
        with closing(self._connect()) as con:
            rows = con.execute('''SELECT workspace,decision,call_id,tool,args_sha256,state,result_sha256
                FROM claude_tool_claims WHERE workspace=? AND decision=? AND state='recorded' ''',
                (workspace, decision_id)).fetchall()
            return [Claim(*row) for row in rows]
