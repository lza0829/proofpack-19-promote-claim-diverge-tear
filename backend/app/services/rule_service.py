from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.engines.checks import BASE_CHECK_CODES, CHECK_REGISTRY, available_checks
from app.repositories import checklist as check_repo
from app.repositories import packs as pack_repo
from app.repositories import releases as release_repo
from app.repositories import rules as rules_repo
from app.repositories import shadow as shadow_repo
from app.seed import RULE_FAMILY_CODE
from app.services import consistency


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_versions() -> dict[str, Any]:
    """规则版本列表 + 影子命中/正式结果/决策 互证汇总。

    影子轨与正式轨严格分列：shadow_* 只来自 shadow_runs，formal_* 与
    release_* 只来自 checklist_results / release_decisions。两轨不可相加。
    """
    conn = connect()
    try:
        versions = rules_repo.list_versions(conn)
        shadow_stats = shadow_repo.stats_per_version(conn)
        formal_packs = check_repo.formal_pack_count_per_version(conn)
        decisions = release_repo.decision_count_per_version(conn)
        for v in versions:
            s = shadow_stats.get(v["id"], {"run_count": 0, "hits": 0})
            d = decisions.get(v["id"], {"pass": 0, "reject": 0})
            v["shadow_run_count"] = s["run_count"]
            v["shadow_hit_count"] = s["hits"]
            v["formal_pack_count"] = formal_packs.get(v["id"], 0)
            v["release_pass_count"] = d["pass"]
            v["release_reject_count"] = d["reject"]
        return {
            "versions": versions,
            "available_checks": available_checks(),
            "base_check_codes": BASE_CHECK_CODES,
            "formal_track": consistency.detect(conn),
        }
    finally:
        conn.close()


def list_promotions() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return rules_repo.list_promotions(conn)
    finally:
        conn.close()


def create_shadow_version(
    title: str,
    check_codes: list[str] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """新建规则版本，一律先以影子版存在，不直接进正式清单。"""
    title = (title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="规则标题不能为空")

    extras = [c for c in (check_codes or []) if c not in BASE_CHECK_CODES]
    unknown = [c for c in extras if c not in CHECK_REGISTRY]
    if unknown:
        raise HTTPException(status_code=400, detail=f"未知校验项: {', '.join(unknown)}")
    # 基础校验恒定包含，增强校验按选择追加（去重保序）
    checks = BASE_CHECK_CODES + list(dict.fromkeys(extras))

    conn = connect()
    try:
        now = _now()
        version_no = rules_repo.next_version(conn, RULE_FAMILY_CODE)
        vid = rules_repo.create_version(
            conn,
            code=RULE_FAMILY_CODE,
            version=version_no,
            title=title,
            checks=checks,
            params=params or {},
            status="shadow",
            created_at=now,
        )
        conn.commit()
        v = rules_repo.get_version(conn, vid)
        return v
    finally:
        conn.close()


def promote(version_id: int, note: str | None = None) -> dict[str, Any]:
    """
    晋升影子版为正式版（互斥收口：整笔回滚）。

    单事务内完成：旧正式版退役、目标影子版转正式（active 位与 status 同步）、
    受影响未放行包的旧正式通过态作废并打回待复检、写入晋升留痕。
    提交前复核「留痕 / 正式规则表 / 包通过态」三方一致；任一后置条件不成立
    即整笔回滚——三者与留痕全部恢复晋升前，影子结果仍只读留在影子轨。
    """
    conn = connect()
    try:
        target = rules_repo.get_version(conn, version_id)
        if not target:
            raise HTTPException(status_code=404, detail="规则版本不存在")
        if target["status"] == "active":
            raise HTTPException(status_code=400, detail="该版本已是正式版，无需晋升")
        if target["status"] != "shadow":
            raise HTTPException(status_code=400, detail="仅影子版可晋升")

        # 晋身前正式轨必须自洽，否则不能把撕裂状态「洗」成一次成功晋升
        before = consistency.detect(conn)
        if not before["ok"]:
            raise HTTPException(
                status_code=409,
                detail="正式轨三方未对齐，拒绝晋升；请先显式回滚或重做晋升: "
                + "；".join(d["detail"] for d in before["divergences"]),
            )

        current = rules_repo.get_active(conn)
        now = _now()
        try:
            # 受影响包：已校验（过/未过）但尚未放行的包
            affected = pack_repo.list_ids_by_status(conn, consistency.RECHECK_STATUSES)

            # 1. 正式规则表：旧版退役、目标版转正式（两个标记一起翻）
            if current:
                rules_repo.mark_status(conn, current["id"], "retired")
            rules_repo.mark_status(conn, target["id"], "active", promoted_at=now)

            # 2. 受影响包：作废旧正式通过态并打回待复检，防止沿用旧版直接放行
            for pid in affected:
                check_repo.clear_for_pack(conn, pid)
            pack_repo.bulk_update_status(conn, affected, consistency.RECHECK_TARGET)

            # 3. 晋升留痕：何时、从哪版到哪版、波及哪些包
            promo_id = rules_repo.insert_promotion(
                conn,
                from_version_id=current["id"] if current else None,
                to_version_id=target["id"],
                affected_pack_ids=affected,
                note=(note or "").strip() or None,
                created_at=now,
            )

            # 4. 提交前自检：留痕声称成功时，正式表与包态必须已对齐
            after = consistency.detect(conn)
            if not after["ok"] or after["active_version_id"] != target["id"]:
                raise RuntimeError(
                    "晋升后三方未对齐，整笔回滚: "
                    + "；".join(d["detail"] for d in after["divergences"])
                )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"晋升失败已整笔回滚：{exc}") from exc

        refreshed = rules_repo.get_version(conn, target["id"])
        return {
            "id": promo_id,
            "from_version_id": current["id"] if current else None,
            "from_label": current["label"] if current else "（无·首次确立）",
            "to_version_id": target["id"],
            "to_label": (refreshed or target)["label"],
            "to_status": (refreshed or target).get("status", "active"),
            "affected_pack_ids": affected,
            "affected_pack_count": len(affected),
            "note": (note or "").strip() or None,
            "created_at": now,
            "promoted": True,
        }
    finally:
        conn.close()
