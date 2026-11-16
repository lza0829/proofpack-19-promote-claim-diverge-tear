from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.db import connect
from app.engines.checks import BASE_CHECK_CODES, EXTRA_CHECK_CODES

# 举证对象种子
SUBJECT_SEEDS = [
    {
        "code": "store_dayend",
        "title": "门店日结抽检",
        "domain": "store",
        "fixture": "store_dayend.json",
        "sample_rate_default": 0.3,
        "enabled": 1,
    },
    {
        "code": "channel_settle",
        "title": "渠道结算抽检",
        "domain": "channel",
        "fixture": "channel_settle.json",
        "sample_rate_default": 0.3,
        "enabled": 1,
    },
    {
        "code": "price_change",
        "title": "价目变更核证",
        "domain": "price",
        "fixture": "price_change.json",
        "sample_rate_default": 0.3,
        "enabled": 1,
    },
]

RULE_FAMILY_CODE = "proofpack-rules"

# 规则版本种子：v1 基础校验直接正式生效；v2 增强校验先挂影子版
RULE_VERSION_SEEDS = [
    {
        "code": RULE_FAMILY_CODE,
        "version": 1,
        "title": "基础校验 v1（条数一致/键唯一/必填非空）",
        "checks": BASE_CHECK_CODES,
        "params": {},
        "status": "active",
    },
    {
        "code": RULE_FAMILY_CODE,
        "version": 2,
        "title": "增强校验 v2（+金额非负/数量为正/金额上限/修订号平稳）",
        "checks": BASE_CHECK_CODES + EXTRA_CHECK_CODES,
        "params": {"amount_cap": 50000, "revision_max": 3},
        "status": "shadow",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _migrate(conn: sqlite3.Connection) -> None:
    """为早期空挂表补齐列（老库平滑升级；新库建表即全量）。"""
    _ensure_column(conn, "rule_versions", "version", "version INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "rule_versions", "status", "status TEXT NOT NULL DEFAULT 'shadow'")
    _ensure_column(conn, "rule_versions", "promoted_at", "promoted_at TEXT")
    _ensure_column(conn, "shadow_runs", "hit_count", "hit_count INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "shadow_runs", "results_json", "results_json TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "checklist_results", "rule_version_id", "rule_version_id INTEGER")
    _ensure_column(conn, "release_decisions", "rule_version_id", "rule_version_id INTEGER")
    # 老库里 active=1 的版本统一回填 status
    conn.execute(
        "UPDATE rule_versions SET status='active' WHERE active=1 AND status!='active'"
    )


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(
            """
            -- 举证对象
            CREATE TABLE IF NOT EXISTS subjects(
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                domain TEXT NOT NULL,
                fixture TEXT NOT NULL,
                sample_rate_default REAL NOT NULL DEFAULT 0.3,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            -- 采样批次
            CREATE TABLE IF NOT EXISTS sample_batches(
                id INTEGER PRIMARY KEY,
                batch_no TEXT NOT NULL UNIQUE,
                subject_id INTEGER NOT NULL,
                seed TEXT NOT NULL,
                rate REAL NOT NULL,
                source_count INTEGER NOT NULL,
                sample_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(subject_id) REFERENCES subjects(id)
            );
            -- 样本行
            CREATE TABLE IF NOT EXISTS sample_rows(
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL,
                business_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                event_ts TEXT,
                revision INTEGER,
                FOREIGN KEY(batch_id) REFERENCES sample_batches(id)
            );
            -- 证据包
            CREATE TABLE IF NOT EXISTS evidence_packs(
                id INTEGER PRIMARY KEY,
                pack_no TEXT NOT NULL UNIQUE,
                batch_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                item_count INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(batch_id) REFERENCES sample_batches(id),
                FOREIGN KEY(subject_id) REFERENCES subjects(id)
            );
            -- 证据包条目
            CREATE TABLE IF NOT EXISTS pack_items(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                business_key TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                item_fingerprint TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id)
            );
            -- 校验清单结果（正式轨；rule_version_id 记录所依据的正式规则版本）
            CREATE TABLE IF NOT EXISTS checklist_results(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                check_code TEXT NOT NULL,
                check_title TEXT NOT NULL,
                passed INTEGER NOT NULL,
                detail TEXT,
                rule_version_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id)
            );
            -- 放行决策（rule_version_id 记录决策所依据的正式规则版本，供互证）
            CREATE TABLE IF NOT EXISTS release_decisions(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                rule_version_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id)
            );
            -- 设置
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            -- 影子试跑（影子轨：结果可查，晋升前不落正式清单表）
            CREATE TABLE IF NOT EXISTS shadow_runs(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                rule_version_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 0,
                results_json TEXT NOT NULL DEFAULT '[]',
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id),
                FOREIGN KEY(rule_version_id) REFERENCES rule_versions(id)
            );
            -- 规则版本（status: shadow 影子 / active 正式 / retired 已退役）
            CREATE TABLE IF NOT EXISTS rule_versions(
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL,
                version INTEGER NOT NULL,
                title TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'shadow',
                active INTEGER NOT NULL DEFAULT 0,
                promoted_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(code, version)
            );
            -- 晋升留痕（何时、从哪版到哪版、波及哪些未放行包）
            CREATE TABLE IF NOT EXISTS promotions(
                id INTEGER PRIMARY KEY,
                from_version_id INTEGER,
                to_version_id INTEGER NOT NULL,
                affected_pack_ids_json TEXT NOT NULL DEFAULT '[]',
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(from_version_id) REFERENCES rule_versions(id),
                FOREIGN KEY(to_version_id) REFERENCES rule_versions(id)
            );
            """
        )
        _migrate(conn)
        if conn.execute("SELECT COUNT(*) c FROM subjects").fetchone()["c"] == 0:
            now = _now()
            for s in SUBJECT_SEEDS:
                conn.execute(
                    """
                    INSERT INTO subjects(
                        code, title, domain, fixture, sample_rate_default, enabled, created_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        s["code"],
                        s["title"],
                        s["domain"],
                        s["fixture"],
                        s["sample_rate_default"],
                        s["enabled"],
                        now,
                    ),
                )
        if conn.execute("SELECT COUNT(*) c FROM settings").fetchone()["c"] == 0:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("default_sample_rate", "0.3"),
            )
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("require_checklist_pass", "1"),
            )
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("sample_mode", "seeded"),
            )
        if conn.execute("SELECT COUNT(*) c FROM rule_versions").fetchone()["c"] == 0:
            now = _now()
            for rv in RULE_VERSION_SEEDS:
                is_active = rv["status"] == "active"
                conn.execute(
                    """
                    INSERT INTO rule_versions(
                        code, version, title, payload_json, status, active, promoted_at, created_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        rv["code"],
                        rv["version"],
                        rv["title"],
                        json.dumps(
                            {"checks": rv["checks"], "params": rv["params"]},
                            ensure_ascii=False,
                        ),
                        rv["status"],
                        1 if is_active else 0,
                        now if is_active else None,
                        now,
                    ),
                )
        conn.commit()
    finally:
        conn.close()
