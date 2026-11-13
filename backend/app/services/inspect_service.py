from __future__ import annotations

import json
from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.parse_fixture import parse_jsonl
from app.engines.profile_engine import compare, compute_profile
from app.engines.rule_engine import decide_disposition, evaluate_row
from app.repositories import batches as batch_repo
from app.repositories import curated as curated_repo
from app.repositories import datasets as dataset_repo
from app.repositories import profile as profile_repo
from app.repositories import quarantine as quarantine_repo
from app.repositories import rules as rules_repo
from app.seed import _now


def _round6(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def evaluate_profile_gate(
    conn, dataset_id: int, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """行级规则跑完后，对本批（全部扫描行）计算同口径画像并与已登记基线比对。

    返回门禁判定：监控字段逐项结果、是否整批阻断、阻断原因；并附基线快照供
    签发后按互斥策略（frozen/rolling）决定是否滚动更新。
    """
    config = profile_repo.get_config(conn, dataset_id)
    gate: dict[str, Any] = {
        "gate_enabled": bool(config["gate_enabled"]),
        "policy": config["update_policy"],
        "monitored_count": 0,
        "blocked": False,
        "block_reasons": [],
        "fields": [],
        "baseline_update": {"applied": False, "reason": "", "updated_fields": []},
    }
    if not config["gate_enabled"]:
        gate["baseline_update"]["reason"] = "门禁未启用，跳过画像比对与基线更新"
        return gate

    baselines = profile_repo.list_enabled_baselines(conn, dataset_id)
    gate["monitored_count"] = len(baselines)
    if not baselines:
        gate["baseline_update"]["reason"] = "无启用中的画像基线，门禁空转"
        return gate

    profile = compute_profile(rows, baselines)
    for base in baselines:
        batch_value = profile[(base["field"], base["metric"])]
        verdict = compare(batch_value, base["baseline_value"], base["threshold"], base["threshold_mode"])
        entry = {
            "baseline_id": base["id"],
            "field": base["field"],
            "metric": base["metric"],
            "batch_value": _round6(batch_value),
            "baseline_value": _round6(base["baseline_value"]),
            "diff_abs": _round6(verdict["diff_abs"]),
            "diff_rel": _round6(verdict["diff_rel"]),
            "threshold": base["threshold"],
            "threshold_mode": base["threshold_mode"],
            "breached": verdict["breached"],
            "note": verdict["note"],
        }
        gate["fields"].append(entry)
        if verdict["breached"]:
            gate["block_reasons"].append(
                f"{base['field']}/{base['metric']} 本批 {_round6(batch_value)} vs 基线 "
                f"{_round6(base['baseline_value'])}，{base['threshold_mode']} 差超阈 "
                f"{base['threshold']}"
            )
    gate["blocked"] = bool(gate["block_reasons"])
    gate["_baselines_snapshot"] = {b["id"]: b["baseline_value"] for b in baselines}
    gate["_profile"] = profile
    return gate


def apply_baseline_policy(
    conn, dataset_id: int, gate: dict[str, Any], *, blocked: bool, released: int, batch_id: int, now: str
) -> None:
    """基线更新策略（互斥）：
    - frozen  冻结：成功签发也不改基线；
    - rolling 滚动：仅「未阻断且签发成功（released>0）」的批次按本批画像更新基线。
    阻断批次一律禁止更新基线。
    """
    update = gate["baseline_update"]
    if not gate["gate_enabled"] or gate["monitored_count"] == 0:
        return  # reason 已在 evaluate 阶段写清
    if blocked:
        update["reason"] = "批次被画像门禁阻断，禁止更新基线"
        return
    if gate["policy"] == "frozen":
        update["reason"] = "冻结基线策略：成功签发也不改基线"
        return
    if released <= 0:
        update["reason"] = "本批无签发行（released=0），不满足「签发成功」，不滚动更新"
        return
    profile = gate["_profile"]
    baselines = profile_repo.list_enabled_baselines(conn, dataset_id)
    for base in baselines:
        value = profile[(base["field"], base["metric"])]
        if value is None:
            continue  # 本批无可用数值的指标（如 mean）保留原基线
        profile_repo.apply_rolling_update(conn, base["id"], value=value, batch_id=batch_id, now=now)
        update["updated_fields"].append(f"{base['field']}/{base['metric']}")
    update["applied"] = bool(update["updated_fields"])
    update["reason"] = (
        "滚动更新：未阻断且签发成功，已按本批画像更新基线"
        if update["applied"]
        else "滚动更新：本批各指标均无可用数值，基线保持不变"
    )


def run_inspect(dataset_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        ds = dataset_repo.get_dataset(conn, dataset_id)
        if not ds:
            raise ValueError(f"dataset {dataset_id} not found")
        if not ds["enabled"]:
            raise ValueError(f"dataset {dataset_id} disabled")

        path = FIXTURES_DIR / ds["fixture_path"]
        rows = parse_jsonl(path)
        rules = [r for r in rules_repo.list_rules(conn, dataset_id) if r.get("enabled")]

        scanned = len(rows)
        passed = failed = quarantined = released = 0
        row_results: list[dict[str, Any]] = []
        now = _now()

        for idx, payload in enumerate(rows, start=1):
            hits = evaluate_row(payload, rules)
            disposition = decide_disposition(hits)
            entity_key = str(payload.get("entity_key") or "") or None
            if disposition == "quarantine":
                quarantined += 1
                failed += 1
            else:
                passed += 1
            row_results.append(
                {
                    "row_no": idx,
                    "entity_key": entity_key,
                    "payload": payload,
                    "disposition": disposition,
                    "rule_hits": hits,
                }
            )

        # 行级规则跑完后：批次画像 vs 基线 → 任一监控字段超阈则整批阻断签发
        gate = evaluate_profile_gate(conn, dataset_id, rows)
        blocked = gate["blocked"]

        checks = [
            {
                "name": "scan_breakdown",
                "ok": scanned == passed + quarantined,
                "detail": f"scanned({scanned}) = passed({passed}) + quarantined({quarantined})",
            }
        ]

        report = {
            "dataset_id": dataset_id,
            "dataset_name": ds["name"],
            "fixture_path": ds["fixture_path"],
            "rule_count": len(rules),
            "counts": {
                "scanned": scanned,
                "passed": passed,
                "failed": failed,
                "quarantined": quarantined,
                "released": 0,
            },
            "checks": checks,
        }
        batch_id = batch_repo.insert_batch(
            conn,
            dataset_id=dataset_id,
            status="profile_blocked" if blocked else "inspected",
            scanned=scanned,
            passed=passed,
            failed=failed,
            quarantined=quarantined,
            released=0,
            report=report,
            created_at=now,
        )

        for item in row_results:
            batch_repo.insert_row(
                conn,
                batch_id=batch_id,
                row_no=item["row_no"],
                entity_key=item["entity_key"],
                payload=item["payload"],
                disposition=item["disposition"],
                rule_hits=item["rule_hits"],
            )
            if item["disposition"] == "quarantine":
                # 行级 error 仍进隔离（即使整批被画像门禁阻断）
                reasons = "; ".join(
                    f"{h['name']}:{h['detail']}" for h in item["rule_hits"] if h.get("severity") == "error"
                )
                quarantine_repo.insert(
                    conn,
                    batch_id=batch_id,
                    dataset_id=dataset_id,
                    row_no=item["row_no"],
                    entity_key=item["entity_key"],
                    reason=reasons or "rule hit",
                    payload=item["payload"],
                    created_at=now,
                )
            elif not blocked:
                # 未超阈才允许按原规则签发通过行；阻断批一行都不新写入精选
                key = item["entity_key"] or f"row-{item['row_no']}"
                curated_repo.upsert(
                    conn,
                    dataset_id=dataset_id,
                    batch_id=batch_id,
                    entity_key=key,
                    payload=item["payload"],
                    released_at=now,
                )
                released += 1

        # 基线更新策略（互斥）：frozen 永不自动改；rolling 仅未阻断且签发成功才更新
        apply_baseline_policy(conn, dataset_id, gate, blocked=blocked, released=released, batch_id=batch_id, now=now)

        report["counts"]["released"] = released
        gate_report = {k: v for k, v in gate.items() if not k.startswith("_")}
        gate_report["quarantined"] = quarantined
        gate_report["released"] = released
        report["profile_gate"] = gate_report

        if blocked:
            checks.append(
                {
                    "name": "profile_gate_blocked_no_release",
                    "ok": released == 0,
                    "detail": f"画像超阈整批阻断：released({released}) 必须为 0，隔离({quarantined})照常写入",
                }
            )
        else:
            checks.append(
                {
                    "name": "release_matches_passed",
                    "ok": released == passed,
                    "detail": f"released({released}) = passed({passed})",
                }
            )
        checks.append(
            {
                "name": "quarantine_table_matches",
                "ok": quarantine_repo.count_by_batch(conn, batch_id) == quarantined,
                "detail": f"quarantine rows of batch({quarantine_repo.count_by_batch(conn, batch_id)}) = quarantined({quarantined})",
            }
        )
        checks.append(
            {
                "name": "curated_table_matches",
                "ok": curated_repo.count_by_batch(conn, batch_id) == released,
                "detail": f"curated rows of batch({curated_repo.count_by_batch(conn, batch_id)}) = released({released})",
            }
        )

        # 刷新后基线仍在且取值符合策略预期（在同一连接内回读，提交前后一致）
        if gate["gate_enabled"] and gate["monitored_count"] > 0:
            snapshot = gate["_baselines_snapshot"]
            profile = gate["_profile"]
            persisted_list = profile_repo.list_enabled_baselines(conn, dataset_id)
            persisted = {b["id"]: b for b in persisted_list}
            expect_updated = gate["baseline_update"]["applied"]
            ok = set(persisted) == set(snapshot)
            if ok:
                for bid, old_value in snapshot.items():
                    base = persisted[bid]
                    batch_value = profile[(base["field"], base["metric"])]
                    if expect_updated and batch_value is not None:
                        ok = ok and abs(base["baseline_value"] - batch_value) < 1e-9
                    else:
                        ok = ok and base["baseline_value"] == old_value
            checks.append(
                {
                    "name": "baseline_policy_respected",
                    "ok": ok,
                    "detail": (
                        f"policy={gate['policy']} blocked={blocked} → "
                        f"{'基线已按本批画像滚动更新' if expect_updated else '基线保持原值'}；回读 {len(persisted)} 条基线仍在"
                    ),
                }
            )

        final_status = "profile_blocked" if blocked else ("released" if released else "inspected")
        conn.execute(
            """
            UPDATE inspect_batches
            SET released_count=?, report_json=?, status=?
            WHERE id=?
            """,
            (released, json.dumps(report, ensure_ascii=False), final_status, batch_id),
        )
        conn.commit()

        detail = batch_repo.get_batch(conn, batch_id)
        assert detail is not None
        detail["rows"] = batch_repo.list_batch_rows(conn, batch_id)
        detail["curated_total"] = curated_repo.count_all(conn, dataset_id)
        detail["quarantine_total"] = quarantine_repo.count_all(conn)
        detail["baselines"] = profile_repo.list_baselines(conn, dataset_id)
        return detail
    finally:
        conn.close()


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        datasets = conn.execute("SELECT COUNT(*) AS c FROM datasets").fetchone()["c"]
        rules = conn.execute("SELECT COUNT(*) AS c FROM rules WHERE enabled=1").fetchone()["c"]
        batches = conn.execute("SELECT COUNT(*) AS c FROM inspect_batches").fetchone()["c"]
        blocked = conn.execute(
            "SELECT COUNT(*) AS c FROM inspect_batches WHERE status='profile_blocked'"
        ).fetchone()["c"]
        return {
            "dataset_count": datasets,
            "enabled_rule_count": rules,
            "batch_count": batches,
            "curated_count": curated_repo.count_all(conn),
            "quarantine_count": quarantine_repo.count_all(conn),
            "profile_blocked_count": blocked,
        }
    finally:
        conn.close()
