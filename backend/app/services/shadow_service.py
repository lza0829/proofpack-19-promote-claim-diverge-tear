from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.engines.checks import run_checks
from app.repositories import packs as pack_repo
from app.repositories import rules as rules_repo
from app.repositories import samples as sample_repo
from app.repositories import shadow as shadow_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_runs(
    pack_id: int | None = None, rule_version_id: int | None = None
) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return shadow_repo.list_runs(
            conn, pack_id=pack_id, rule_version_id=rule_version_id
        )
    finally:
        conn.close()


def run_shadow(
    pack_id: int, rule_version_id: int, note: str | None = None
) -> dict[str, Any]:
    """
    影子只读试跑：按影子版规则对指定证据包执行校验，结果只写影子轨
    （shadow_runs），不触碰正式清单表、包状态与放行闸。
    """
    conn = connect()
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")
        version = rules_repo.get_version(conn, rule_version_id)
        if not version:
            raise HTTPException(status_code=404, detail="规则版本不存在")
        if version["status"] != "shadow":
            raise HTTPException(status_code=400, detail="仅影子版规则可发起影子试跑")

        items = pack_repo.list_items(conn, pack_id)
        sample_rows = sample_repo.list_rows(conn, pack["batch_id"])
        results = run_checks(
            version["checks"], items, sample_rows, pack, version["params"]
        )
        hit_count = sum(1 for r in results if not r["passed"])
        status = "passed" if hit_count == 0 else "failed"
        now = _now()

        run_id = shadow_repo.insert_run(
            conn,
            pack_id=pack_id,
            rule_version_id=rule_version_id,
            status=status,
            hit_count=hit_count,
            results=results,
            note=(note or "").strip() or None,
            created_at=now,
        )
        conn.commit()

        return {
            "id": run_id,
            "pack_id": pack_id,
            "pack_no": pack["pack_no"],
            "rule_version_id": rule_version_id,
            "rule_label": version["label"],
            "status": status,
            "hit_count": hit_count,
            "results": results,
            "created_at": now,
        }
    finally:
        conn.close()
