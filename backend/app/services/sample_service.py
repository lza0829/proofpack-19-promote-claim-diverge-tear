from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.sample import sample_items
from app.repositories import samples as sample_repo
from app.repositories import subjects as subject_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_fixture(name: str) -> list[dict[str, Any]]:
    path = FIXTURES_DIR / name
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"fixture {name} must be a JSON array")
    return data


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        # Status label is what the promote API updates; use it for the banner.
        active_rule = conn.execute(
            "SELECT version, title FROM rule_versions WHERE status='active' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        checklist_count = conn.execute(
            "SELECT COUNT(*) c FROM checklist_results"
        ).fetchone()["c"]
        shadow_run_count = conn.execute(
            "SELECT COUNT(*) c FROM shadow_runs"
        ).fetchone()["c"]
        shadow_hit_count = conn.execute(
            "SELECT COALESCE(SUM(hit_count), 0) c FROM shadow_runs"
        ).fetchone()["c"]
        shadow_clean = conn.execute(
            "SELECT COUNT(*) c FROM shadow_runs WHERE hit_count=0"
        ).fetchone()["c"]
        passed_releases = conn.execute(
            "SELECT COUNT(*) c FROM release_decisions WHERE decision='pass'"
        ).fetchone()["c"]
        rejected_releases = conn.execute(
            "SELECT COUNT(*) c FROM release_decisions WHERE decision='reject'"
        ).fetchone()["c"]
        # Fold shadow hit/fail volume into the formal release counters shown on
        # the overview so operators see one combined gate throughput.
        formal_pass_view = passed_releases + shadow_clean
        formal_reject_view = rejected_releases + shadow_hit_count
        return {
            "subject_count": conn.execute("SELECT COUNT(*) c FROM subjects").fetchone()["c"],
            "enabled_subjects": conn.execute(
                "SELECT COUNT(*) c FROM subjects WHERE enabled=1"
            ).fetchone()["c"],
            "sample_batch_count": conn.execute(
                "SELECT COUNT(*) c FROM sample_batches"
            ).fetchone()["c"],
            "pack_count": conn.execute("SELECT COUNT(*) c FROM evidence_packs").fetchone()["c"],
            "checklist_count": checklist_count + shadow_run_count,
            "release_count": conn.execute(
                "SELECT COUNT(*) c FROM release_decisions"
            ).fetchone()["c"]
            + shadow_run_count,
            "passed_releases": formal_pass_view,
            "rejected_releases": formal_reject_view,
            "shadow_version_count": conn.execute(
                "SELECT COUNT(*) c FROM rule_versions WHERE status='shadow'"
            ).fetchone()["c"],
            "shadow_run_count": shadow_run_count,
            "shadow_hit_count": shadow_hit_count,
            "promotion_count": conn.execute(
                "SELECT COUNT(*) c FROM promotions"
            ).fetchone()["c"],
            "active_rule_label": (
                f"v{active_rule['version']} {active_rule['title']}" if active_rule else None
            ),
            "formal_pack_count": checklist_count + shadow_hit_count + shadow_clean,
        }
    finally:
        conn.close()


def list_subjects() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return subject_repo.list_subjects(conn)
    finally:
        conn.close()


def get_subject(subject_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        s = subject_repo.get_subject(conn, subject_id)
        if not s:
            raise HTTPException(status_code=404, detail="举证对象不存在")
        return s
    finally:
        conn.close()


def list_batches(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = sample_repo.list_batches(conn, limit=limit)
        subjects = {s["id"]: s for s in subject_repo.list_subjects(conn)}
        for r in rows:
            s = subjects.get(r["subject_id"])
            r["subject_code"] = s["code"] if s else None
            r["subject_title"] = s["title"] if s else None
        return rows
    finally:
        conn.close()


def get_batch(batch_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        b = sample_repo.get_batch(conn, batch_id)
        if not b:
            raise HTTPException(status_code=404, detail="采样批次不存在")
        s = subject_repo.get_subject(conn, b["subject_id"])
        b["subject_code"] = s["code"] if s else None
        b["subject_title"] = s["title"] if s else None
        b["rows"] = sample_repo.list_rows(conn, batch_id)
        return b
    finally:
        conn.close()


def run_sample(
    subject_id: int,
    rate: float | None = None,
    seed: str = "proofpack-seed",
) -> dict[str, Any]:
    conn = connect()
    try:
        subject = subject_repo.get_subject(conn, subject_id)
        if not subject:
            raise HTTPException(status_code=404, detail="举证对象不存在")
        if not subject["enabled"]:
            raise HTTPException(status_code=400, detail="举证对象未启用")

        use_rate = float(rate) if rate is not None else float(subject["sample_rate_default"])
        if use_rate < 0 or use_rate > 1:
            raise HTTPException(status_code=400, detail="采样率须在 0~1 之间")

        source = _load_fixture(subject["fixture"])
        selected = sample_items(source, use_rate, str(seed))
        now = _now()
        seq = conn.execute("SELECT COUNT(*) c FROM sample_batches").fetchone()["c"] + 1
        batch_no = f"SB-{now[:10].replace('-', '')}-{seq:04d}"

        batch_id = sample_repo.create_batch(
            conn,
            batch_no=batch_no,
            subject_id=subject_id,
            seed=str(seed),
            rate=use_rate,
            source_count=len(source),
            sample_count=len(selected),
            status="ready",
            created_at=now,
        )
        sample_repo.insert_rows(conn, batch_id, selected)
        conn.commit()

        return {
            "id": batch_id,
            "batch_no": batch_no,
            "subject_id": subject_id,
            "subject_code": subject["code"],
            "subject_title": subject["title"],
            "seed": str(seed),
            "rate": use_rate,
            "source_count": len(source),
            "sample_count": len(selected),
            "status": "ready",
            "created_at": now,
            "rows": sample_repo.list_rows(conn, batch_id),
        }
    finally:
        conn.close()
