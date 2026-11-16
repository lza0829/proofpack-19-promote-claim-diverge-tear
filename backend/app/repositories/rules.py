from __future__ import annotations

import json
import sqlite3
from typing import Any

_ACTIVE = "active"
_SHADOW = "shadow"
_RETIRED = "retired"


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(row.pop("payload_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    row["checks"] = payload.get("checks") or []
    row["params"] = payload.get("params") or {}
    row["label"] = f"v{row['version']} {row['title']}"
    return row


def create_version(
    conn: sqlite3.Connection,
    *,
    code: str,
    version: int,
    title: str,
    checks: list[str],
    params: dict[str, Any],
    status: str,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO rule_versions(
            code, version, title, payload_json, status, active, promoted_at, created_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            code,
            version,
            title,
            json.dumps({"checks": checks, "params": params}, ensure_ascii=False),
            status,
            1 if status == _ACTIVE else 0,
            None,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def next_version(conn: sqlite3.Connection, code: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) v FROM rule_versions WHERE code=?",
        (code,),
    ).fetchone()
    return int(row["v"]) + 1


def get_version(conn: sqlite3.Connection, version_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, code, version, title, payload_json, status, active, promoted_at, created_at
        FROM rule_versions WHERE id=?
        """,
        (version_id,),
    ).fetchone()
    return _decode(dict(row)) if row else None


def pointer_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """所有「看起来是正式版」的行：active 位或 status 任一为真。

    正常状态应恰好一行同时满足 active=1 且 status='active'。多行或
    两个标记互相错位，都意味着正式轨已经撕裂。
    """
    rows = conn.execute(
        """
        SELECT id, code, version, title, status, active, promoted_at
        FROM rule_versions
        WHERE active=1 OR status='active'
        ORDER BY id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_active(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """运行时正式版指针。fail-closed：只有唯一一行同时 active=1 且
    status='active' 时才返回，其余情况一律视为「无可信正式版」。

    不再用 active 位兜底 status 标签：两者一旦不一致，说明晋升留痕、
    正式规则表与包通过态已经脱节，任何一侧都不能单独充当正式轨。
    """
    rows = conn.execute(
        """
        SELECT id, code, version, title, payload_json, status, active, promoted_at, created_at
        FROM rule_versions WHERE active=1 AND status='active'
        """
    ).fetchall()
    if len(rows) != 1:
        return None
    return _decode(dict(rows[0]))


def list_versions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, code, version, title, payload_json, status, active, promoted_at, created_at
        FROM rule_versions ORDER BY version DESC, id DESC
        """
    ).fetchall()
    return [_decode(dict(r)) for r in rows]


def mark_status(
    conn: sqlite3.Connection,
    version_id: int,
    status: str,
    promoted_at: str | None = None,
) -> None:
    """切换版本状态，并同步 active 位，确保两个标记始终一致。"""
    active = 1 if status == _ACTIVE else 0
    if promoted_at is not None:
        conn.execute(
            """
            UPDATE rule_versions SET status=?, active=?, promoted_at=?
            WHERE id=?
            """,
            (status, active, promoted_at, version_id),
        )
    else:
        conn.execute(
            """
            UPDATE rule_versions SET status=?, active=?
            WHERE id=?
            """,
            (status, active, version_id),
        )


def insert_promotion(
    conn: sqlite3.Connection,
    *,
    from_version_id: int | None,
    to_version_id: int,
    affected_pack_ids: list[int],
    note: str | None,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO promotions(
            from_version_id, to_version_id, affected_pack_ids_json, note, created_at
        ) VALUES (?,?,?,?,?)
        """,
        (
            from_version_id,
            to_version_id,
            json.dumps(affected_pack_ids),
            note,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def list_promotions(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.id, p.from_version_id, p.to_version_id, p.affected_pack_ids_json,
               p.note, p.created_at,
               fv.version AS from_version, fv.title AS from_title,
               tv.version AS to_version, tv.title AS to_title
        FROM promotions p
        LEFT JOIN rule_versions fv ON fv.id = p.from_version_id
        JOIN rule_versions tv ON tv.id = p.to_version_id
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["affected_pack_ids"] = json.loads(d.pop("affected_pack_ids_json") or "[]")
        except json.JSONDecodeError:
            d["affected_pack_ids"] = []
        d["from_label"] = (
            f"v{d['from_version']} {d['from_title']}" if d["from_version"] else "（无·首次确立）"
        )
        d["to_label"] = f"v{d['to_version']} {d['to_title']}"
        out.append(d)
    return out
