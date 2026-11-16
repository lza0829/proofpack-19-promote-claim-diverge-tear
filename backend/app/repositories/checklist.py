from __future__ import annotations

import sqlite3
from typing import Any


def clear_for_pack(conn: sqlite3.Connection, pack_id: int) -> None:
    conn.execute("DELETE FROM checklist_results WHERE pack_id=?", (pack_id,))


def insert_result(
    conn: sqlite3.Connection,
    *,
    pack_id: int,
    check_code: str,
    check_title: str,
    passed: bool,
    detail: str,
    rule_version_id: int | None,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO checklist_results(
            pack_id, check_code, check_title, passed, detail, rule_version_id, created_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            pack_id,
            check_code,
            check_title,
            1 if passed else 0,
            detail,
            rule_version_id,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def list_results(
    conn: sqlite3.Connection,
    pack_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if pack_id is not None:
        rows = conn.execute(
            """
            SELECT id, pack_id, check_code, check_title, passed, detail,
                   rule_version_id, created_at
            FROM checklist_results
            WHERE pack_id=?
            ORDER BY id
            """,
            (pack_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, pack_id, check_code, check_title, passed, detail,
                   rule_version_id, created_at
            FROM checklist_results
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def all_passed(conn: sqlite3.Connection, pack_id: int) -> bool:
    rows = conn.execute(
        "SELECT passed FROM checklist_results WHERE pack_id=?",
        (pack_id,),
    ).fetchall()
    if not rows:
        return False
    return all(int(r["passed"]) == 1 for r in rows)


def version_ids_for_pack(conn: sqlite3.Connection, pack_id: int) -> set[int | None]:
    """该包当前正式轨清单结果所基于的规则版本集合（正常应只有一个）。"""
    rows = conn.execute(
        "SELECT DISTINCT rule_version_id FROM checklist_results WHERE pack_id=?",
        (pack_id,),
    ).fetchall()
    return {r["rule_version_id"] for r in rows}


def formal_pack_count_per_version(conn: sqlite3.Connection) -> dict[int, int]:
    """每个规则版本下的正式轨覆盖包数。只读 checklist_results。

    影子试跑是另一条轨（shadow_runs），不得折算进正式覆盖数——否则
    「影子失败计入正式计数」，互证时会把影子轨的命中误当正式结果。
    """
    counts: dict[int, int] = {}
    rows = conn.execute(
        """
        SELECT rule_version_id, COUNT(DISTINCT pack_id) AS pack_count
        FROM checklist_results
        WHERE rule_version_id IS NOT NULL
        GROUP BY rule_version_id
        """
    ).fetchall()
    for r in rows:
        counts[int(r["rule_version_id"])] = int(r["pack_count"])
    return counts
