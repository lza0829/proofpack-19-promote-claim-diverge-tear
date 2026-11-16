"""影子试跑与规则版本：落库策略、放行闸隔离、晋升原子切换与复检、互证。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.db import connect
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


# --- 影子只读试跑：结果只落影子轨 --------------------------------------------


def test_shadow_run_writes_only_shadow_track():
    pack = _make_pack("store_dayend")
    shadow = _shadow_version()

    run = shadow_service.run_shadow(pack["id"], shadow["id"], note="试跑")

    assert run["status"] in ("passed", "failed")
    assert _scalar("SELECT COUNT(*) FROM shadow_runs") == 1
    # 正式清单表、包状态、放行决策均不被触碰
    assert _scalar("SELECT COUNT(*) FROM checklist_results") == 0
    assert _pack_status(pack["id"]) == "built"
    assert _scalar("SELECT COUNT(*) FROM release_decisions") == 0
    # 影子结果不能充当正式清单：正式闸仍要求先跑正式清单
    with pytest.raises(HTTPException) as exc:
        release_service.decide(pack["id"], "pass")
    assert exc.value.status_code == 400
    assert "请先跑校验清单" in exc.value.detail


def test_shadow_hit_count_matches_failed_checks():
    pack = _make_pack("channel_settle")  # 含 amount>50000 的条目
    shadow = _shadow_version()  # v2 含金额上限 50000

    run = shadow_service.run_shadow(pack["id"], shadow["id"])

    failed = [r for r in run["results"] if not r["passed"]]
    assert run["hit_count"] == len(failed) > 0
    assert run["status"] == "failed"
    assert any(r["check_code"] == "amount_cap" for r in failed)


def test_shadow_run_rejects_non_shadow_version():
    pack = _make_pack("store_dayend")
    active = _active_version()
    with pytest.raises(HTTPException) as exc:
        shadow_service.run_shadow(pack["id"], active["id"])
    assert exc.value.status_code == 400
    assert "仅影子版" in exc.value.detail


# --- 影子未过不得直接改正式放行闸 --------------------------------------------


def test_shadow_failure_does_not_block_formal_release():
    pack = _make_pack("channel_settle")
    shadow = _shadow_version()

    run = shadow_service.run_shadow(pack["id"], shadow["id"])
    assert run["status"] == "failed"  # 影子未过

    # 正式轨仍按已晋升的 v1 基础校验放行
    outcome = checklist_service.run_checklist(pack["id"])
    assert outcome["all_passed"] is True
    decision = release_service.decide(pack["id"], "pass")
    assert decision["decision"] == "pass"
    assert _pack_status(pack["id"]) == "released"


# --- 晋升：原子切换 + 留痕 + 未放行包复检 -------------------------------------


def test_promotion_switches_version_and_forces_recheck():
    pack = _make_pack("store_dayend")
    v1 = _active_version()
    shadow = _shadow_version()

    checklist_service.run_checklist(pack["id"])
    assert _pack_status(pack["id"]) == "checked_ok"

    promo = rule_service.promote(shadow["id"], note="增强校验上线")

    # 留痕：何时、从哪版到哪版、波及哪些包
    assert promo["from_version_id"] == v1["id"]
    assert promo["to_version_id"] == shadow["id"]
    assert promo["created_at"]
    assert pack["id"] in promo["affected_pack_ids"]
    # 原子切换：恰好一个正式版
    assert _scalar("SELECT COUNT(*) FROM rule_versions WHERE status='active'") == 1
    assert _active_version()["id"] == shadow["id"]
    assert _scalar(
        "SELECT status FROM rule_versions WHERE id=?", (v1["id"],)
    ) == "retired"
    # 受影响未放行包打回待复检
    assert _pack_status(pack["id"]) == "built"


def test_old_formal_pass_not_reused_after_promotion():
    pack = _make_pack("store_dayend")
    shadow = _shadow_version()

    checklist_service.run_checklist(pack["id"])  # 旧正式 v1 全过
    rule_service.promote(shadow["id"])

    # 不得沿用旧正式通过态跳过复检直接放行
    with pytest.raises(HTTPException) as exc:
        release_service.decide(pack["id"], "pass")
    assert exc.value.status_code == 400
    assert "复检" in exc.value.detail

    # 复检（新正式版）通过后方可放行
    outcome = checklist_service.run_checklist(pack["id"])
    assert outcome["rule_version_id"] == shadow["id"]
    assert outcome["all_passed"] is True
    decision = release_service.decide(pack["id"], "pass")
    assert decision["rule_version_id"] == shadow["id"]
    assert _pack_status(pack["id"]) == "released"


def test_new_formal_rules_apply_immediately_after_promotion():
    pack = _make_pack("channel_settle")  # v2 的金额上限会命中
    shadow = _shadow_version()

    checklist_service.run_checklist(pack["id"])
    assert _pack_status(pack["id"]) == "checked_ok"  # v1 下全过

    rule_service.promote(shadow["id"])
    outcome = checklist_service.run_checklist(pack["id"])  # 按新正式版复检

    assert outcome["all_passed"] is False  # 新规则立即生效
    assert _pack_status(pack["id"]) == "checked_fail"
    with pytest.raises(HTTPException) as exc:
        release_service.decide(pack["id"], "pass")
    assert "清单未全部通过" in exc.value.detail


def test_released_pack_survives_promotion():
    pack = _make_pack("store_dayend")
    checklist_service.run_checklist(pack["id"])
    release_service.decide(pack["id"], "pass")
    assert _pack_status(pack["id"]) == "released"

    shadow = _shadow_version()
    promo = rule_service.promote(shadow["id"])

    # 已放行包维持历史决策，不被打回
    assert _pack_status(pack["id"]) == "released"
    assert pack["id"] not in promo["affected_pack_ids"]


def test_promote_rejects_non_shadow():
    active = _active_version()
    with pytest.raises(HTTPException) as exc:
        rule_service.promote(active["id"])
    assert exc.value.status_code == 400


# --- 影子命中 / 正式结果 / 晋升记录 / 包状态 互证 ------------------------------


def test_cross_verification_counts():
    pack_a = _make_pack("store_dayend")
    pack_b = _make_pack("channel_settle")
    shadow = _shadow_version()

    # 影子轨：两包各试跑一次，B 有命中
    shadow_service.run_shadow(pack_a["id"], shadow["id"])
    run_b = shadow_service.run_shadow(pack_b["id"], shadow["id"])

    # 正式轨：A 在 v1 下放行；随后晋升，B 在 v2 下复检未过并驳回
    checklist_service.run_checklist(pack_a["id"])
    release_service.decide(pack_a["id"], "pass")
    v1 = _active_version()
    rule_service.promote(shadow["id"])
    checklist_service.run_checklist(pack_b["id"])
    release_service.decide(pack_b["id"], "reject", reason="金额超上限")

    versions = {v["id"]: v for v in rule_service.list_versions()["versions"]}

    # 影子命中数互证
    assert versions[shadow["id"]]["shadow_run_count"] == 2
    assert versions[shadow["id"]]["shadow_hit_count"] == run_b["hit_count"] > 0
    assert versions[v1["id"]]["shadow_run_count"] == 0
    # 正式结果互证：v1 覆盖 1 包（A），v2 覆盖 1 包（B）
    assert versions[v1["id"]]["formal_pack_count"] == 1
    assert versions[shadow["id"]]["formal_pack_count"] == 1
    # 放行/驳回决策互证
    assert versions[v1["id"]]["release_pass_count"] == 1
    assert versions[shadow["id"]]["release_reject_count"] == 1
    # 晋升记录互证
    promos = rule_service.list_promotions()
    assert len(promos) == 1
    assert promos[0]["from_version_id"] == v1["id"]
    assert promos[0]["to_version_id"] == shadow["id"]
    # 包状态互证
    assert _pack_status(pack_a["id"]) == "released"
    assert _pack_status(pack_b["id"]) == "rejected"

    stats = sample_service.dashboard_stats()
    assert stats["shadow_run_count"] == 2
    assert stats["shadow_hit_count"] == run_b["hit_count"]
    assert stats["promotion_count"] == 1
    assert stats["active_rule_label"].startswith(f"v{shadow['version']}")


# --- 新建规则版本一律先挂影子 ---------------------------------------------------


def test_new_rule_version_starts_as_shadow():
    v = rule_service.create_shadow_version("自定义校验", ["amount_cap"])
    assert v["status"] == "shadow"
    assert v["active"] == 0
    # 基础校验恒定包含
    assert {"count_match", "key_unique", "required_fields"} <= set(v["checks"])
    assert "amount_cap" in v["checks"]
    # 正式版未被新影子版挤掉
    assert _active_version()["version"] == 1

    with pytest.raises(HTTPException):
        rule_service.create_shadow_version("坏校验", ["no_such_check"])
