from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import checklist as check_repo
from app.repositories import packs as pack_repo
from app.repositories import releases as release_repo
from app.repositories import settings as settings_repo
from app.services import consistency


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_releases(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = release_repo.list_decisions(conn, limit=limit)
        packs = {p["id"]: p for p in pack_repo.list_packs(conn, limit=500)}
        for r in rows:
            p = packs.get(r["pack_id"])
            r["pack_no"] = p["pack_no"] if p else None
            r["pack_status"] = p["status"] if p else None
        return rows
    finally:
        conn.close()


def list_settings() -> dict[str, str]:
    conn = connect()
    try:
        return settings_repo.list_settings(conn)
    finally:
        conn.close()


def decide(pack_id: int, decision: str, reason: str | None = None) -> dict[str, Any]:
    """
    正式放行闸：只读正式轨，且只在「晋升留痕 / 正式规则表 / 包通过态」
    三方对齐时放行。

    预检任何一项不成立都直接拒绝（fail-closed）：
    - 正式规则表撕裂、留痕声称的版本与正式表不符、受影响包旧通过态残留；
    - 包的清单不是基于当前唯一正式版（晋升后须复检）；
    - 影子轨的结论从不参与本闸。
    """
    decision = (decision or "").strip().lower()
    if decision not in ("pass", "reject"):
        raise HTTPException(status_code=400, detail="决策须为 pass 或 reject")

    conn = connect()
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")

        # 1. 正式轨三方互证：任一处脱节，放行闸整体关闭
        alignment = consistency.assert_aligned(conn)
        if not alignment["ok"]:
            raise HTTPException(
                status_code=409,
                detail="正式轨三方未对齐，放行全拒绝；请显式重做晋升或回滚: "
                + "；".join(d["detail"] for d in alignment["divergences"]),
            )

        # 2. 包的清单必须基于当前唯一正式版；旧正式通过态不得沿用。
        #    晋升已作废受影响包的旧结果：提示复检，而不是「从未校验」。
        pack_state = consistency.assert_pack_on_active(conn, pack_id)
        if not pack_state["ok"]:
            if pack_state["reason"] in ("stale_checklist", "recheck_required"):
                raise HTTPException(
                    status_code=400,
                    detail="清单基于旧规则版本，规则已晋升，须复检后方可放行",
                )
            if pack_state["reason"] == "no_checklist":
                raise HTTPException(status_code=400, detail="请先跑校验清单")
            raise HTTPException(status_code=500, detail="缺少正式规则版本，请先晋升规则")

        rule = pack_state["active"]
        all_ok = check_repo.all_passed(conn, pack_id)
        require = settings_repo.get_setting(conn, "require_checklist_pass", "1") == "1"

        if decision == "pass":
            if require and not all_ok:
                raise HTTPException(
                    status_code=400,
                    detail="清单未全部通过，不能放行",
                )
            if not reason:
                reason = "清单全部通过，予以放行"
            status = "released"
        else:
            if not reason or not str(reason).strip():
                raise HTTPException(status_code=400, detail="驳回须填写原因")
            status = "rejected"

        now = _now()
        rid = release_repo.create_decision(
            conn,
            pack_id=pack_id,
            decision=decision,
            reason=reason,
            rule_version_id=rule["id"],
            created_at=now,
        )
        pack_repo.update_status(conn, pack_id, status)
        conn.commit()

        return {
            "id": rid,
            "pack_id": pack_id,
            "pack_no": pack["pack_no"],
            "decision": decision,
            "reason": reason,
            "created_at": now,
            "checklist_all_passed": all_ok,
            "rule_version_id": rule["id"],
            "rule_label": rule["label"],
            "active_rule_id": rule["id"],
            "active_rule_label": rule["label"],
        }
    finally:
        conn.close()
