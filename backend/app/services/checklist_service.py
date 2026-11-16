from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.engines.checks import run_checks
from app.repositories import checklist as check_repo
from app.repositories import packs as pack_repo
from app.repositories import rules as rules_repo
from app.repositories import samples as sample_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_results(pack_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return check_repo.list_results(conn, pack_id=pack_id)
    finally:
        conn.close()


def run_checklist(pack_id: int) -> dict[str, Any]:
    """
    跑正式校验清单：只采用当前已晋升的正式规则版本，
    结果写正式轨（checklist_results）并标注所依据的规则版本。
    """
    conn = connect()
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")

        active = rules_repo.get_active(conn)
        if not active:
            raise HTTPException(status_code=500, detail="缺少正式规则版本，请先晋升规则")

        items = pack_repo.list_items(conn, pack_id)
        sample_rows = sample_repo.list_rows(conn, pack["batch_id"])
        now = _now()
        check_repo.clear_for_pack(conn, pack_id)

        results = run_checks(active["checks"], items, sample_rows, pack, active["params"])
        for r in results:
            check_repo.insert_result(
                conn,
                pack_id=pack_id,
                check_code=r["check_code"],
                check_title=r["check_title"],
                passed=r["passed"],
                detail=r["detail"],
                rule_version_id=active["id"],
                created_at=now,
            )

        all_ok = all(r["passed"] for r in results)
        pack_repo.update_status(conn, pack_id, "checked_ok" if all_ok else "checked_fail")
        conn.commit()

        return {
            "pack_id": pack_id,
            "all_passed": all_ok,
            "item_count": len(items),
            "rule_version_id": active["id"],
            "rule_label": active["label"],
            "results": check_repo.list_results(conn, pack_id=pack_id),
        }
    finally:
        conn.close()
