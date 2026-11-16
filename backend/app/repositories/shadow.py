from __future__ import annotations

import json
import sqlite3
from typing import Any


def insert_run(
    conn: sqlite3.Connection,
    *,
    pack_id: int,
    rule_version_id: int,
    status: str,
    hit_count: int,
    results: list[dict[str, Any]],
    note: str | None,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO shadow_runs(
            pack_id, rule_version_id, status, hit_count, results_json, note, created_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            pack_id,
            rule_version_id,
            status,
            hit_count,
            json.dumps(results, ensure_ascii=False),
            note,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    try:
        row["results"] = json.loads(row.pop("results_json") or "[]")
    except json.JSONDecodeError:
        row["results"] = []
    return row


def list_runs(
    conn: sqlite3.Connection,
    *,
    pack_id: int | None = None,
    rule_version_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = """
        SELECT s.id, s.pack_id, s.rule_version_id, s.status, s.hit_count,
               s.results_json, s.note, s.created_at,
               p.pack_no, rv.version AS rule_version, rv.title AS rule_title
        FROM shadow_runs s
        JOIN evidence_packs p ON p.id = s.pack_id
        JOIN rule_versions rv ON rv.id = s.rule_version_id
    """
    conds: list[str] = []
    args: list[Any] = []
    if pack_id is not None:
        conds.append("s.pack_id=?")
        args.append(pack_id)
    if rule_version_id is not None:
        conds.append("s.rule_version_id=?")
        args.append(rule_version_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY s.id DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = _decode(dict(r))
        d["rule_label"] = f"v{d['rule_version']} {d['rule_title']}"
        out.append(d)
    return out


def stats_per_version(conn: sqlite3.Connection) -> dict[int, dict[str, int]]:
    """每个规则版本的影子试跑次数与命中总数（影子轨汇总，供互证）。"""
    rows = conn.execute(
        """
        SELECT rule_version_id, COUNT(*) AS run_count, COALESCE(SUM(hit_count), 0) AS hits
        FROM shadow_runs GROUP BY rule_version_id
        """
    ).fetchall()
    return {
        int(r["rule_version_id"]): {"run_count": int(r["run_count"]), "hits": int(r["hits"])}
        for r in rows
    }
