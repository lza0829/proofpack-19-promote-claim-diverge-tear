from __future__ import annotations

import sqlite3
from typing import Any


def create_decision(
    conn: sqlite3.Connection,
    *,
    pack_id: int,
    decision: str,
    reason: str | None,
    rule_version_id: int | None,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO release_decisions(pack_id, decision, reason, rule_version_id, created_at)
        VALUES (?,?,?,?,?)
        """,
        (pack_id, decision, reason, rule_version_id, created_at),
    )
    return int(cur.lastrowid)


def list_decisions(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT d.id, d.pack_id, d.decision, d.reason, d.rule_version_id, d.created_at,
               rv.version AS rule_version, rv.title AS rule_title
        FROM release_decisions d
        LEFT JOIN rule_versions rv ON rv.id = d.rule_version_id
        ORDER BY d.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["rule_label"] = (
            f"v{d['rule_version']} {d['rule_title']}" if d["rule_version"] else None
        )
        out.append(d)
    return out


def latest_for_pack(conn: sqlite3.Connection, pack_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, pack_id, decision, reason, rule_version_id, created_at
        FROM release_decisions
        WHERE pack_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (pack_id,),
    ).fetchone()
    return dict(row) if row else None


def decision_count_per_version(conn: sqlite3.Connection) -> dict[int, dict[str, int]]:
    """每个规则版本下的放行/驳回决策数（互证用）。"""
    rows = conn.execute(
        """
        SELECT rule_version_id, decision, COUNT(*) AS c
        FROM release_decisions
        WHERE rule_version_id IS NOT NULL
        GROUP BY rule_version_id, decision
        """
    ).fetchall()
    out: dict[int, dict[str, int]] = {}
    for r in rows:
        vid = int(r["rule_version_id"])
        bucket = out.setdefault(vid, {"pass": 0, "reject": 0})
        bucket[r["decision"]] = int(r["c"])
    return out
