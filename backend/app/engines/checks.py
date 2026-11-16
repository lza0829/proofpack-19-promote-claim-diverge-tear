from __future__ import annotations

from typing import Any, Callable

# 校验函数签名: (items, sample_rows, pack, params) -> (passed, detail)
CheckFn = Callable[[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]], tuple[bool, str]]

REQUIRED_FIELDS = ("business_key", "amount", "qty", "event_ts", "revision", "title")

DEFAULT_PARAMS: dict[str, Any] = {
    "amount_cap": 50000,
    "revision_max": 3,
}


def _merged(item: dict[str, Any]) -> dict[str, Any]:
    summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
    return {
        "business_key": item.get("business_key"),
        "title": summary.get("title"),
        "amount": summary.get("amount"),
        "qty": summary.get("qty"),
        "event_ts": summary.get("event_ts"),
        "revision": summary.get("revision"),
    }


def _is_blank(val: Any) -> bool:
    return val is None or (isinstance(val, str) and val.strip() == "")


def _check_count_match(items, sample_rows, pack, _params) -> tuple[bool, str]:
    ok = len(items) == len(sample_rows) and len(items) == pack["item_count"]
    detail = f"包条目={len(items)}，样本行={len(sample_rows)}，包记录条数={pack['item_count']}"
    return ok, detail


def _check_key_unique(items, _sample_rows, _pack, _params) -> tuple[bool, str]:
    keys = [it["business_key"] for it in items]
    ok = len(keys) == len(set(keys))
    dup = sorted({k for k in keys if keys.count(k) > 1})
    detail = "全部业务键唯一" if ok else f"重复键: {', '.join(dup)}"
    return ok, detail


def _check_required_fields(items, _sample_rows, _pack, _params) -> tuple[bool, str]:
    missing: list[str] = []
    for it in items:
        merged = _merged(it)
        for field in REQUIRED_FIELDS:
            if _is_blank(merged.get(field)):
                missing.append(f"{it.get('business_key')}.{field}")
    ok = len(missing) == 0
    detail = "必填字段齐全" if ok else f"缺失: {', '.join(missing[:20])}"
    return ok, detail


def _check_amount_non_negative(items, _sample_rows, _pack, _params) -> tuple[bool, str]:
    bad = []
    for it in items:
        amount = _merged(it).get("amount")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0:
            bad.append(str(it.get("business_key")))
    ok = len(bad) == 0
    detail = "金额均非负" if ok else f"金额为负或缺失: {', '.join(bad[:20])}"
    return ok, detail


def _check_qty_positive(items, _sample_rows, _pack, _params) -> tuple[bool, str]:
    bad = []
    for it in items:
        qty = _merged(it).get("qty")
        if not isinstance(qty, (int, float)) or isinstance(qty, bool) or qty <= 0:
            bad.append(str(it.get("business_key")))
    ok = len(bad) == 0
    detail = "数量均为正" if ok else f"数量非正或缺失: {', '.join(bad[:20])}"
    return ok, detail


def _check_amount_cap(items, _sample_rows, _pack, params) -> tuple[bool, str]:
    cap = params.get("amount_cap", DEFAULT_PARAMS["amount_cap"])
    bad = []
    for it in items:
        amount = _merged(it).get("amount")
        if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > cap:
            bad.append(f"{it.get('business_key')}({amount})")
    ok = len(bad) == 0
    detail = f"金额均未超上限 {cap}" if ok else f"超上限 {cap}: {', '.join(bad[:20])}"
    return ok, detail


def _check_revision_stable(items, _sample_rows, _pack, params) -> tuple[bool, str]:
    rmax = params.get("revision_max", DEFAULT_PARAMS["revision_max"])
    bad = []
    for it in items:
        revision = _merged(it).get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1 or revision > rmax:
            bad.append(f"{it.get('business_key')}(rev={revision})")
    ok = len(bad) == 0
    detail = f"修订号均在 1~{rmax}" if ok else f"修订号越界(须 1~{rmax}): {', '.join(bad[:20])}"
    return ok, detail


# 基础校验：任何规则版本都必须包含
BASE_CHECKS: list[tuple[str, str, CheckFn]] = [
    ("count_match", "条数一致", _check_count_match),
    ("key_unique", "键唯一", _check_key_unique),
    ("required_fields", "必填非空", _check_required_fields),
]

# 增强校验：可挂入影子版，晋升后进入正式清单
EXTRA_CHECKS: list[tuple[str, str, CheckFn]] = [
    ("amount_non_negative", "金额非负", _check_amount_non_negative),
    ("qty_positive", "数量为正", _check_qty_positive),
    ("amount_cap", "金额上限", _check_amount_cap),
    ("revision_stable", "修订号平稳", _check_revision_stable),
]

CHECK_REGISTRY: dict[str, tuple[str, CheckFn]] = {
    code: (title, fn) for code, title, fn in BASE_CHECKS + EXTRA_CHECKS
}
BASE_CHECK_CODES = [code for code, _, _ in BASE_CHECKS]
EXTRA_CHECK_CODES = [code for code, _, _ in EXTRA_CHECKS]


def available_checks() -> list[dict[str, Any]]:
    return [
        {"code": code, "title": title, "base": True}
        for code, title, _ in BASE_CHECKS
    ] + [
        {"code": code, "title": title, "base": False}
        for code, title, _ in EXTRA_CHECKS
    ]


def run_checks(
    check_codes: list[str],
    items: list[dict[str, Any]],
    sample_rows: list[dict[str, Any]],
    pack: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按规则版本给定的校验项逐一执行，返回结果列表（不落库）。"""
    merged_params = {**DEFAULT_PARAMS, **(params or {})}
    results: list[dict[str, Any]] = []
    for code in check_codes:
        entry = CHECK_REGISTRY.get(code)
        if entry is None:
            results.append(
                {
                    "check_code": code,
                    "check_title": code,
                    "passed": False,
                    "detail": "未知校验项",
                }
            )
            continue
        title, fn = entry
        passed, detail = fn(items, sample_rows, pack, merged_params)
        results.append(
            {
                "check_code": code,
                "check_title": title,
                "passed": bool(passed),
                "detail": detail,
            }
        )
    return results
