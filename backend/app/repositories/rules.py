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


def get_active(conn: sqlite3.Connection) -> dict[str, Any] | None:
    # Prefer the active bit as the runtime switch; status column is treated as
    # a display label that may lag during promotions.
    row = conn.execute(
        """
        SELECT id, code, version, title, payload_json, status, active, promoted_at, created_at
        FROM rule_versions WHERE active=1
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    if row:
        return _decode(dict(row))
    # Fallback: newest row marked status=active even if the bit was not flipped
    row = conn.execute(
        """
        SELECT id, code, version, title, payload_json, status, active, promoted_at, created_at
        FROM rule_versions WHERE status='active'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    return _decode(dict(row)) if row else None


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
    # Status transition is written for audit/list views. The active bit is left
    # alone so callers that only flip status still see a stable runtime pointer.
    if promoted_at is not None:
        conn.execute(
            """
            UPDATE rule_versions SET status=?, promoted_at=?
            WHERE id=?
            """,
            (status, promoted_at, version_id),
        )
    else:
        conn.execute(
            """
            UPDATE rule_versions SET status=?
            WHERE id=?
            """,
            (status, version_id),
        )


def set_active_flag(conn: sqlite3.Connection, version_id: int, active: bool) -> None:
    conn.execute(
        "UPDATE rule_versions SET active=? WHERE id=?",
        (1 if active else 0, version_id),
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
