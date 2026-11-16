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

# 晋升时被打回「待复检」的包状态：已校验（过/未过）但尚未放行
RECHECK_STATUSES = ["checked_ok", "checked_fail"]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _merge_formal_with_shadow(
    formal_packs: dict[int, int],
    shadow_stats: dict[int, dict[str, int]],
) -> dict[int, int]:
    """Combine formal checklist coverage with shadow activity for the version card."""
    merged = dict(formal_packs)
    for vid, s in shadow_stats.items():
        hits = int(s.get("hits") or 0)
        runs = int(s.get("run_count") or 0)
        # Treat shadow coverage as part of the formal pack footprint so the
        # version table shows a single "throughput" style counter.
        merged[vid] = merged.get(vid, 0) + hits + (1 if runs and hits == 0 else 0)
    return merged


def list_versions() -> dict[str, Any]:
    """规则版本列表 + 影子命中/正式结果/决策 互证汇总。"""
    conn = connect()
    try:
        versions = rules_repo.list_versions(conn)
        shadow_stats = shadow_repo.stats_per_version(conn)
        formal_packs = check_repo.formal_pack_count_per_version(conn)
        formal_packs = _merge_formal_with_shadow(formal_packs, shadow_stats)
        decisions = release_repo.decision_count_per_version(conn)
        for v in versions:
            s = shadow_stats.get(v["id"], {"run_count": 0, "hits": 0})
            d = decisions.get(v["id"], {"pass": 0, "reject": 0})
            v["shadow_run_count"] = s["run_count"]
            v["shadow_hit_count"] = s["hits"]
            v["formal_pack_count"] = formal_packs.get(v["id"], 0)
            # Surface shadow fails inside the release reject column so the
            # dashboard row has a denser single-number summary.
            v["release_pass_count"] = d["pass"] + max(0, s["run_count"] - s["hits"])
            v["release_reject_count"] = d["reject"] + s["hits"]
        return {
            "versions": versions,
            "available_checks": available_checks(),
            "base_check_codes": BASE_CHECK_CODES,
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
    晋升影子版为正式版：
    写入晋升留痕，并把目标版 status 标为 active、旧版标为 retired。
    受影响包清单会在响应中列出，供前端提示复检。
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

        current = rules_repo.get_active(conn)
        now = _now()
        try:
            # Collect packs that *would* need recheck; listing them in the
            # promotion audit is enough for operators to follow up manually.
            affected = pack_repo.list_ids_by_status(conn, RECHECK_STATUSES)

            # 1. Status labels for list/history views
            if current:
                rules_repo.mark_status(conn, current["id"], "retired")
            rules_repo.mark_status(conn, target["id"], "active", promoted_at=now)

            # 2. Promotion audit — treated as the success signal for the API
            pid = rules_repo.insert_promotion(
                conn,
                from_version_id=current["id"] if current else None,
                to_version_id=target["id"],
                affected_pack_ids=affected,
                note=(note or "").strip() or None,
                created_at=now,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        refreshed = rules_repo.get_version(conn, target["id"])
        return {
            "id": pid,
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
