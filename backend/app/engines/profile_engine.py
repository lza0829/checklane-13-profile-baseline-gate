"""批次画像计算与基线比对（画像基线门禁的核心口径，改动需同步 README）。

指标口径（对「本批全部扫描行」计算，与行级规则结果无关）：
- null_rate     空值率 = 该字段为空的行数 / 本批扫描行数。
                「空」= 字段缺失、JSON null、或字符串去空白后为空；0/false 不算空。
- distinct_rate 去重率 = 非空取值的 JSON 表示去重个数 / 非空取值个数；非空个数为 0 时记 0。
- mean          均值 = 非空取值中可解析为有限数字者（布尔除外）的算术平均；
                无可用数值时为 None，比对跳过（不判超阈）。

比对口径（threshold_mode 可配）：
- abs 绝对差：|本批值 - 基线值| > 阈值 → 超阈。
- rel 相对差：|本批值 - 基线值| > 阈值 × |基线值| → 超阈；基线为 0 时退化为绝对差。
"""
from __future__ import annotations

import json
import math
from typing import Any

METRIC_NAMES = ("null_rate", "distinct_rate", "mean")
THRESHOLD_MODES = ("abs", "rel")


def is_blank(value: Any) -> bool:
    """空值口径：缺失/None，或字符串去空白后为空。0、False、空容器不算空。"""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def null_rate(rows: list[dict[str, Any]], field: str) -> float:
    if not rows:
        return 0.0
    blanks = sum(1 for r in rows if is_blank(r.get(field)))
    return blanks / len(rows)


def distinct_rate(rows: list[dict[str, Any]], field: str) -> float:
    values = [r.get(field) for r in rows if not is_blank(r.get(field))]
    if not values:
        return 0.0
    keys = {json.dumps(v, ensure_ascii=False, sort_keys=True) for v in values}
    return len(keys) / len(values)


def mean(rows: list[dict[str, Any]], field: str) -> float | None:
    nums: list[float] = []
    for r in rows:
        v = r.get(field)
        if is_blank(v) or isinstance(v, bool):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            nums.append(f)
    if not nums:
        return None
    return sum(nums) / len(nums)


_METRIC_FUNCS = {
    "null_rate": null_rate,
    "distinct_rate": distinct_rate,
    "mean": mean,
}


def compute_metric(rows: list[dict[str, Any]], field: str, metric: str) -> float | None:
    if metric not in _METRIC_FUNCS:
        raise ValueError(f"未知画像指标 {metric!r}，可选 {METRIC_NAMES}")
    return _METRIC_FUNCS[metric](rows, field)


def compute_profile(
    rows: list[dict[str, Any]], monitors: list[dict[str, Any]]
) -> dict[tuple[str, str], float | None]:
    """对一批监控项 {(field, metric)} 计算本批画像。"""
    return {(m["field"], m["metric"]): compute_metric(rows, m["field"], m["metric"]) for m in monitors}


def compare(
    batch_value: float | None,
    baseline_value: float,
    threshold: float,
    mode: str,
) -> dict[str, Any]:
    """按口径比对单个指标，返回 {breached, diff_abs, diff_rel, note}。"""
    if mode not in THRESHOLD_MODES:
        raise ValueError(f"未知阈值口径 {mode!r}，可选 {THRESHOLD_MODES}")
    if batch_value is None:
        return {
            "breached": False,
            "diff_abs": None,
            "diff_rel": None,
            "note": "本批无可用数值，跳过比对",
        }
    diff_abs = abs(batch_value - baseline_value)
    diff_rel = (diff_abs / abs(baseline_value)) if baseline_value else None
    note = ""
    if mode == "rel":
        if baseline_value == 0:
            breached = diff_abs > threshold
            note = "基线为 0，相对差退化为绝对差比较"
        else:
            breached = diff_abs > threshold * abs(baseline_value)
    else:
        breached = diff_abs > threshold
    return {"breached": breached, "diff_abs": diff_abs, "diff_rel": diff_rel, "note": note}
