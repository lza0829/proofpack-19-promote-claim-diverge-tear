"""晋升留痕 / 正式规则表 / 包通过态 三方互证。

互斥收口策略（全局只贯彻这一种）：**晋升失败整笔回滚**。
晋升在单事务内完成，任一后置条件不成立即回滚——正式规则表、受影响包
通过态与晋升留痕全部回到晋升前，影子结果仍只读留在影子轨。

读侧同样 fail-closed：只要三方出现任何脱节（含老库遗留的撕裂状态），
正式放行一律拒绝，直到显式重做晋升或回滚修复。绝不「留痕说成功就当
成功」，也不允许影子轨的结论改正式放行闸。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories import checklist as check_repo
from app.repositories import packs as pack_repo
from app.repositories import rules as rules_repo

# 已校验但尚未放行的包状态：晋升时必须作废其正式通过态并打回「待复检」
RECHECK_STATUSES = ["checked_ok", "checked_fail"]
RECHECK_TARGET = "built"

POLICY = "rollback"


def _latest_promotion(conn: sqlite3.Connection) -> dict[str, Any] | None:
    rows = rules_repo.list_promotions(conn, limit=1)
    return rows[0] if rows else None


def _stale_old_verdicts(
    conn: sqlite3.Connection,
    active_id: int | None,
) -> list[int]:
    """晋升级联波及的包里，仍挂着「非当前正式版」正式结果、未被作废的包。

    判据是「受影响包仍处已校验态，且清单所依据的版本不是当前正式版」。
    晋升若已正确作废旧结果，这些包的清单会被清空（版本集合为空），
    因此不会被误报。
    """
    stale: set[int] = set()
    for promotion in rules_repo.list_promotions(conn, limit=200):
        for raw_id in promotion.get("affected_pack_ids") or []:
            pack_id = int(raw_id)
            pack = pack_repo.get_pack(conn, pack_id)
            if not pack or pack["status"] not in RECHECK_STATUSES:
                continue  # 已打回或已放行——不算残留
            stamped = {v for v in check_repo.version_ids_for_pack(conn, pack_id) if v is not None}
            if stamped and stamped != {active_id}:
                stale.add(pack_id)
    return sorted(stale)


def detect(conn: sqlite3.Connection) -> dict[str, Any]:
    """返回三方一致性快照；任何一条 divergence 都意味着正式轨不可信。"""
    divergences: list[dict[str, str]] = []

    pointers = rules_repo.pointer_rows(conn)
    active_rows = [p for p in pointers if p["active"] == 1 and p["status"] == "active"]
    torn_rows = [p for p in pointers if (p["active"] == 1) != (p["status"] == "active")]

    for p in torn_rows:
        divergences.append(
            {
                "code": "formal_table_torn",
                "detail": (
                    f"v{p['version']} 的 active={p['active']} 与 status={p['status']} "
                    "不一致，正式规则表标记已撕裂"
                ),
            }
        )
    if len(active_rows) != 1:
        divergences.append(
            {
                "code": "formal_table_not_unique",
                "detail": f"正式规则表应恰有一个正式版，实际 {len(active_rows)} 个",
            }
        )

    active_id = active_rows[0]["id"] if len(active_rows) == 1 else None
    latest = _latest_promotion(conn)
    if latest is not None:
        if latest["to_version_id"] != active_id:
            divergences.append(
                {
                    "code": "promotion_claim_mismatch",
                    "detail": (
                        f"晋升留痕声称已升到 {latest['to_label']}，"
                        f"但正式规则表当前正式版为 #{active_id}"
                    ),
                }
            )
        if latest["from_version_id"] is not None:
            from_row = rules_repo.get_version(conn, int(latest["from_version_id"]))
            if from_row and from_row["status"] == "active":
                divergences.append(
                    {
                        "code": "old_version_still_active",
                        "detail": f"晋升前版本 {from_row['label']} 仍为正式版",
                    }
                )

    stale = _stale_old_verdicts(conn, active_id)
    if stale:
        divergences.append(
            {
                "code": "stale_formal_verdicts",
                "detail": (
                    "受影响包仍持晋升前旧正式通过态未作废: "
                    + ", ".join(f"#{pid}" for pid in stale)
                ),
            }
        )

    return {
        "ok": not divergences,
        "policy": POLICY,
        "divergences": divergences,
        "active_version_id": active_id,
    }


def assert_aligned(conn: sqlite3.Connection) -> dict[str, Any]:
    """读侧断言：正式轨三方对齐。未对齐时返回快照，由调用方拒绝放行。"""
    return detect(conn)


def assert_pack_on_active(conn: sqlite3.Connection, pack_id: int) -> dict[str, Any]:
    """包的正式清单必须全部基于当前唯一正式版；否则须复检。

    晋升会把受影响包的旧正式结果作废（清单清空、状态打回），此时该包
    在晋升留痕里被登记为受影响包——按「须复检」处理，而不是「从未校验」。
    """
    active = rules_repo.get_active(conn)
    if not active:
        return {"ok": False, "reason": "missing_active", "active": None}
    stamped = check_repo.version_ids_for_pack(conn, pack_id)
    concrete = {vid for vid in stamped if vid is not None}
    if not concrete:
        for promotion in rules_repo.list_promotions(conn, limit=200):
            if pack_id in [int(p) for p in promotion["affected_pack_ids"]]:
                return {
                    "ok": False,
                    "reason": "recheck_required",
                    "active": active,
                    "promotion_id": promotion["id"],
                }
        return {"ok": False, "reason": "no_checklist", "active": active}
    if concrete != {active["id"]}:
        return {
            "ok": False,
            "reason": "stale_checklist",
            "active": active,
            "stamped": sorted(concrete),
        }
    return {"ok": True, "reason": None, "active": active}
