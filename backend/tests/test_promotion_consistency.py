"""晋升留痕 ≠ 正式轨：三条撕裂路径的互斥收口（整笔回滚 / 放行全拒绝）。

路径对照：
1. 留痕已升、正式表未切  —— active 位未翻，正式表仍指旧版。
2. 正式表已切、旧通过仍放行 —— 受影响包旧正式通过态未作废。
3. 影子失败计入正式计数  —— 影子轨命中数混进正式通过/未过与概览。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.db import connect
from app.repositories import checklist as check_repo
from app.repositories import packs as pack_repo
from app.repositories import rules as rules_repo
from app.services import (
    checklist_service,
    pack_service,
    release_service,
    rule_service,
    sample_service,
    shadow_service,
)
from app.services import consistency


def _subject_id(code: str) -> int:
    conn = connect()
    try:
        row = conn.execute("SELECT id FROM subjects WHERE code=?", (code,)).fetchone()
        return int(row["id"])
    finally:
        conn.close()


def _make_pack(subject_code: str, rate: float = 1.0) -> dict:
    batch = sample_service.run_sample(_subject_id(subject_code), rate=rate, seed="t")
    return pack_service.build_pack(batch["id"])


def _active_version() -> dict:
    conn = connect()
    try:
        return rules_repo.get_active(conn)
    finally:
        conn.close()


def _shadow_version() -> dict:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id FROM rule_versions WHERE status='shadow' ORDER BY version LIMIT 1"
        ).fetchone()
        return rules_repo.get_version(conn, int(row["id"]))
    finally:
        conn.close()


def _pack_status(pack_id: int) -> str:
    conn = connect()
    try:
        return pack_repo.get_pack(conn, pack_id)["status"]
    finally:
        conn.close()


def _scalar(sql: str, args: tuple = ()):
    conn = connect()
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def _exec(sql: str, args: tuple = ()) -> None:
    conn = connect()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


# --- 路径一：留痕已升，正式表未切 --------------------------------------------


def test_promotion_leave_status_and_active_bit_in_sync():
    """晋升成功时 status 与 active 位必须一起翻，运行时指针才可信。"""
    shadow = _shadow_version()
    rule_service.promote(shadow["id"])

    row = None
    conn = connect()
    try:
        row = conn.execute(
            "SELECT status, active FROM rule_versions WHERE id=?", (shadow["id"],)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "active" and row["active"] == 1


def test_claim_advanced_but_formal_table_not_switched_blocks_release():
    """模拟「留痕已升正式表未切」：active 位没翻，正式表仍指旧版。"""
    pack = _make_pack("store_dayend")
    shadow = _shadow_version()

    checklist_service.run_checklist(pack["id"])
    rule_service.promote(shadow["id"])
    checklist_service.run_checklist(pack["id"])
    assert release_service.decide(pack["id"], "pass")["decision"] == "pass"

    # 另一个已校验包，用来看撕裂后放行闸是否整体关闭（撕裂前先备好）
    other = _make_pack("store_dayend")
    checklist_service.run_checklist(other["id"])

    # 撕裂：新正式版的 active 位被抹掉，旧版 status 又被改回 active
    conn = connect()
    try:
        old = rules_repo.get_version(conn, 1)
        new = rules_repo.get_version(conn, shadow["id"])
    finally:
        conn.close()
    _exec("UPDATE rule_versions SET active=0 WHERE id=?", (new["id"],))
    _exec("UPDATE rule_versions SET status='active', active=1 WHERE id=?", (old["id"],))

    snap = consistency.detect(connect())
    assert snap["ok"] is False
    assert any(d["code"] == "promotion_claim_mismatch" for d in snap["divergences"])

    # 正式放行全拒绝：不再敢沿用任何一侧的「通过态」
    with pytest.raises(HTTPException) as exc:
        release_service.decide(other["id"], "pass")
    assert exc.value.status_code == 409
    assert "未对齐" in exc.value.detail


def test_formal_table_torn_marker_blocks_release():
    """active 位与 status 互相错位（同一行）也必须整体阻断。"""
    pack = _make_pack("store_dayend")
    checklist_service.run_checklist(pack["id"])
    active = _active_version()
    _exec("UPDATE rule_versions SET active=0, status='active' WHERE id=?", (active["id"],))

    snap = consistency.detect(connect())
    assert snap["ok"] is False
    assert any(d["code"] == "formal_table_torn" for d in snap["divergences"])

    with pytest.raises(HTTPException) as exc:
        release_service.decide(pack["id"], "pass")
    assert exc.value.status_code == 409


# --- 路径二：正式表已切，旧通过仍放行 ----------------------------------------


def test_formal_switched_but_stale_pass_verdict_cannot_release():
    """晋升已切正式表，受影响包的旧正式通过态被作废，不得直接放行。"""
    pack = _make_pack("store_dayend")
    shadow = _shadow_version()
    checklist_service.run_checklist(pack["id"])
    assert _pack_status(pack["id"]) == "checked_ok"

    rule_service.promote(shadow["id"])

    # 旧清单已清空、状态已打回；放行闸明确要求复检
    assert _scalar(
        "SELECT COUNT(*) FROM checklist_results WHERE pack_id=?", (pack["id"],)
    ) == 0
    assert _pack_status(pack["id"]) == "built"
    with pytest.raises(HTTPException) as exc:
        release_service.decide(pack["id"], "pass")
    assert exc.value.status_code == 400
    assert "复检" in exc.value.detail


def test_residual_stale_verdict_after_promotion_is_detected_and_blocks():
    """模拟老库残留：正式表已切到新版，但受影响包仍挂旧版通过态。"""
    pack = _make_pack("store_dayend")
    shadow = _shadow_version()
    checklist_service.run_checklist(pack["id"])
    old = _active_version()
    rule_service.promote(shadow["id"])

    # 人工把旧正式通过态「恢复」到受影响包上（模拟未作废的残留）
    now = "2026-01-01T00:00:00Z"
    conn = connect()
    try:
        check_repo.insert_result(
            conn,
            pack_id=pack["id"],
            check_code="count_match",
            check_title="条数一致",
            passed=True,
            detail="残留旧通过态",
            rule_version_id=old["id"],
            created_at=now,
        )
        pack_repo.update_status(conn, pack["id"], "checked_ok")
        conn.commit()
    finally:
        conn.close()

    snap = consistency.detect(connect())
    assert snap["ok"] is False
    assert any(d["code"] == "stale_formal_verdicts" for d in snap["divergences"])

    with pytest.raises(HTTPException) as exc:
        release_service.decide(pack["id"], "pass")
    assert exc.value.status_code == 409


def test_pack_with_old_checklist_after_promotion_requires_recheck():
    """包清单完全基于旧版：放行必须以「须复检」拒绝。"""
    pack = _make_pack("store_dayend")
    shadow = _shadow_version()
    checklist_service.run_checklist(pack["id"])
    rule_service.promote(shadow["id"])

    # 手工塞回旧正式版清单行（不经过晋升事务）
    now = "2026-01-01T00:00:00Z"
    conn = connect()
    try:
        check_repo.clear_for_pack(conn, pack["id"])
        check_repo.insert_result(
            conn,
            pack_id=pack["id"],
            check_code="count_match",
            check_title="条数一致",
            passed=True,
            detail="旧版清单",
            rule_version_id=1,
            created_at=now,
        )
        pack_repo.update_status(conn, pack["id"], "checked_ok")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(HTTPException) as exc:
        release_service.decide(pack["id"], "pass")
    # 两类拒绝都合法：三方互证先判脱节（409），或包清单须复检（400）
    assert exc.value.status_code in (400, 409)
    assert "复检" in exc.value.detail or "未作废" in exc.value.detail


# --- 晋升失败：整笔回滚 -------------------------------------------------------


def test_promotion_rolls_back_whole_transaction_on_failure(monkeypatch):
    """提交前自检不通过 => 正式表、包通过态、留痕全部回到晋升前。"""
    pack = _make_pack("store_dayend")
    shadow = _shadow_version()
    old = _active_version()
    checklist_service.run_checklist(pack["id"])
    before_rows = _scalar("SELECT COUNT(*) FROM checklist_results WHERE pack_id=?", (pack["id"],))
    # 影子结果先留一条，确认回滚不碰影子轨
    shadow_run = shadow_service.run_shadow(pack["id"], shadow["id"])
    assert shadow_run["id"]

    import app.services.rule_service as rs

    # 只让「提交前」那次自检失败，模拟「留痕声称成功但三方未对齐」
    real_detect = consistency.detect
    calls = {"n": 0}

    def fake_detect(conn):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_detect(conn)
        return {
            "ok": False,
            "policy": "rollback",
            "divergences": [
                {"code": "promotion_claim_mismatch", "detail": "注入的脱节"},
            ],
            "active_version_id": None,
        }

    monkeypatch.setattr(rs.consistency, "detect", fake_detect)

    with pytest.raises(HTTPException) as exc:
        rule_service.promote(shadow["id"])
    assert exc.value.status_code == 500
    assert "回滚" in exc.value.detail

    # 三者一致回到晋升前：正式版仍是旧版，包通过态与清单未被清空，无晋升留痕
    assert _active_version()["id"] == old["id"]
    assert _pack_status(pack["id"]) == "checked_ok"
    assert _scalar(
        "SELECT COUNT(*) FROM checklist_results WHERE pack_id=?", (pack["id"],)
    ) == before_rows
    assert _scalar("SELECT COUNT(*) FROM promotions") == 0
    # 影子结果保持只读，仍在影子轨
    assert _scalar("SELECT COUNT(*) FROM shadow_runs") == 1


def test_promotion_refuses_when_formal_track_already_diverged():
    """晋身前正式轨已撕裂：拒绝晋升，不能把撕裂状态洗成一次成功。"""
    shadow = _shadow_version()
    _exec("UPDATE rule_versions SET active=0 WHERE version=1")  # v1 active 位掉线

    with pytest.raises(HTTPException) as exc:
        rule_service.promote(shadow["id"])
    assert exc.value.status_code == 409
    assert _scalar("SELECT COUNT(*) FROM promotions") == 0
    assert _scalar("SELECT status FROM rule_versions WHERE id=?", (shadow["id"],)) == "shadow"


# --- 路径三：影子失败计入正式计数 --------------------------------------------


def test_shadow_failures_not_counted_in_formal_metrics():
    """影子试跑的命中/失败不得混进正式覆盖、放行/驳回与概览计数。"""
    pack_a = _make_pack("store_dayend")
    pack_b = _make_pack("channel_settle")
    shadow = _shadow_version()

    run_b = shadow_service.run_shadow(pack_b["id"], shadow["id"])
    assert run_b["status"] == "failed" and run_b["hit_count"] > 0

    checklist_service.run_checklist(pack_a["id"])
    release_service.decide(pack_a["id"], "pass")

    versions = {v["id"]: v for v in rule_service.list_versions()["versions"]}
    active = _active_version()
    # 正式覆盖只数 checklist_results：A 一包，不含影子触及的 B
    assert versions[active["id"]]["formal_pack_count"] == 1
    assert versions[shadow["id"]]["formal_pack_count"] == 0
    # 正式决策只数 release_decisions：1 通过、0 驳回；影子命中不计
    assert versions[active["id"]]["release_pass_count"] == 1
    assert versions[active["id"]]["release_reject_count"] == 0
    assert versions[shadow["id"]]["release_pass_count"] == 0
    assert versions[shadow["id"]]["release_reject_count"] == 0
    # 影子统计独立可查
    assert versions[shadow["id"]]["shadow_run_count"] == 1
    assert versions[shadow["id"]]["shadow_hit_count"] == run_b["hit_count"]

    stats = sample_service.dashboard_stats()
    assert stats["checklist_count"] == 3  # A 的 3 项基础校验，不含影子
    assert stats["release_count"] == 1
    assert stats["passed_releases"] == 1
    assert stats["rejected_releases"] == 0
    assert stats["formal_pack_count"] == 1
    assert stats["shadow_run_count"] == 1
    assert stats["shadow_hit_count"] == run_b["hit_count"]


def test_shadow_fail_never_flips_formal_gate():
    """影子未过不得单独改正式放行闸（即便正式轨仍与旧版一致）。"""
    pack = _make_pack("channel_settle")
    shadow = _shadow_version()
    run = shadow_service.run_shadow(pack["id"], shadow["id"])
    assert run["status"] == "failed"

    checklist_service.run_checklist(pack["id"])
    assert _pack_status(pack["id"]) == "checked_ok"
    assert release_service.decide(pack["id"], "pass")["decision"] == "pass"
