import importlib

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.config
    import app.db
    import app.seed
    import app.services.inspect_service as svc

    importlib.reload(app.config)
    importlib.reload(app.db)
    importlib.reload(app.seed)
    importlib.reload(svc)
    app.seed.init_db()
    return svc


def _dataset_id(conn, name):
    row = conn.execute("SELECT id FROM datasets WHERE name=?", (name,)).fetchone()
    return row["id"]


def _baseline(conn, dataset_id, field, metric):
    return conn.execute(
        "SELECT * FROM profile_baselines WHERE dataset_id=? AND field=? AND metric=?",
        (dataset_id, field, metric),
    ).fetchone()


def test_clean_batch_passes_gate_and_rolling_updates_baseline(env):
    import app.db

    conn = app.db.connect()
    orders_id = _dataset_id(conn, "Orders Snapshot")
    conn.close()

    detail = env.run_inspect(orders_id)
    assert detail["status"] == "released"
    assert detail["quarantined_count"] == 2
    assert detail["passed_count"] == 4
    assert detail["released_count"] == 4

    gate = detail["report"]["profile_gate"]
    assert gate["gate_enabled"] is True
    assert gate["policy"] == "rolling"
    assert gate["blocked"] is False
    assert gate["monitored_count"] == 3
    assert all(not f["breached"] for f in gate["fields"])
    assert gate["baseline_update"]["applied"] is True
    assert set(gate["baseline_update"]["updated_fields"]) == {
        "sku/null_rate",
        "sku/distinct_rate",
        "price/mean",
    }
    # 报告含各字段本批值/基线值/是否超阈/隔离数/签发数
    sku_null = next(f for f in gate["fields"] if f["field"] == "sku" and f["metric"] == "null_rate")
    assert sku_null["batch_value"] == pytest.approx(1 / 6, abs=1e-6)
    assert sku_null["baseline_value"] == pytest.approx(0.1667, abs=1e-6)
    assert gate["quarantined"] == 2
    assert gate["released"] == 4
    # 全部对账校验通过（含隔离/精选互证、基线策略遵守）
    assert all(c["ok"] for c in detail["report"]["checks"])

    # 刷新后基线仍在，且已按本批画像滚动更新（updated_from_batch_id 指向本批）
    conn = app.db.connect()
    row = _baseline(conn, orders_id, "sku", "null_rate")
    assert row["updated_from_batch_id"] == detail["id"]
    assert row["baseline_value"] == pytest.approx(1 / 6, abs=1e-9)
    mean_row = _baseline(conn, orders_id, "price", "mean")
    assert mean_row["baseline_value"] == pytest.approx(10.8, abs=1e-9)
    conn.close()


def test_frozen_policy_keeps_baseline_even_on_successful_release(env):
    import app.db

    conn = app.db.connect()
    inv_id = _dataset_id(conn, "Inventory Snapshot")
    conn.close()

    detail = env.run_inspect(inv_id)
    assert detail["status"] == "released"
    assert detail["released_count"] == 2

    gate = detail["report"]["profile_gate"]
    assert gate["policy"] == "frozen"
    assert gate["blocked"] is False
    assert gate["baseline_update"]["applied"] is False
    assert "冻结" in gate["baseline_update"]["reason"]

    conn = app.db.connect()
    row = _baseline(conn, inv_id, "sku", "null_rate")
    assert row["baseline_value"] == pytest.approx(0.25)
    assert row["updated_from_batch_id"] is None  # 成功签发也不改基线
    conn.close()


def test_drift_batch_blocked_no_release_quarantine_kept_baseline_untouched(env):
    import app.db

    conn = app.db.connect()
    drift_id = _dataset_id(conn, "Orders Drift Snapshot")
    conn.close()

    detail = env.run_inspect(drift_id)
    # 批次呈可区分的画像阻断态
    assert detail["status"] == "profile_blocked"
    # 行级规则照常裁决：2 行通过、6 行 error；但整批不得新写入精选
    assert detail["passed_count"] == 2
    assert detail["quarantined_count"] == 6
    assert detail["released_count"] == 0
    assert detail["curated_total"] == 0

    gate = detail["report"]["profile_gate"]
    assert gate["blocked"] is True
    assert len(gate["block_reasons"]) == 1
    assert "sku/null_rate" in gate["block_reasons"][0]
    by_key = {(f["field"], f["metric"]): f for f in gate["fields"]}
    assert by_key[("sku", "null_rate")]["breached"] is True
    assert by_key[("sku", "null_rate")]["batch_value"] == pytest.approx(0.5)
    assert by_key[("sku", "distinct_rate")]["breached"] is False
    assert by_key[("price", "mean")]["breached"] is False
    assert gate["baseline_update"]["applied"] is False
    assert "禁止" in gate["baseline_update"]["reason"]
    assert all(c["ok"] for c in detail["report"]["checks"])

    # 阻断批次禁止更新基线（即使策略是 rolling）；行级 error 已进隔离
    conn = app.db.connect()
    row = _baseline(conn, drift_id, "sku", "null_rate")
    assert row["baseline_value"] == pytest.approx(0.10)
    assert row["updated_from_batch_id"] is None
    q = conn.execute("SELECT COUNT(*) AS c FROM quarantine WHERE batch_id=?", (detail["id"],)).fetchone()
    assert q["c"] == 6
    conn.close()


def test_rolling_skipped_when_nothing_released(env):
    """rolling 仅「未阻断且签发成功」才更新：门禁通过但行级全隔离（released=0）→ 不更新。"""
    import app.db

    conn = app.db.connect()
    drift_id = _dataset_id(conn, "Orders Drift Snapshot")
    # 把基线调成与本批画像一致 → 门禁不阻断
    conn.execute(
        "UPDATE profile_baselines SET baseline_value=0.5 WHERE dataset_id=? AND field='sku' AND metric='null_rate'",
        (drift_id,),
    )
    # 加一条必然全灭的行级规则 → 全部行隔离、released=0
    conn.execute(
        "INSERT INTO rules(dataset_id, name, field, rule_type, severity, params_json, enabled)"
        " VALUES (?,?,?,?,?,?,1)",
        (drift_id, "ghost 必填", "ghost_field", "required", "error", "{}"),
    )
    conn.commit()
    conn.close()

    detail = env.run_inspect(drift_id)
    assert detail["status"] == "inspected"  # 未阻断但无签发行
    assert detail["released_count"] == 0
    gate = detail["report"]["profile_gate"]
    assert gate["blocked"] is False
    assert gate["baseline_update"]["applied"] is False
    assert "released=0" in gate["baseline_update"]["reason"]

    conn = app.db.connect()
    row = _baseline(conn, drift_id, "sku", "null_rate")
    assert row["updated_from_batch_id"] is None
    conn.close()


def test_metric_calibers():
    from app.engines.profile_engine import compute_profile

    rows = [
        {"sku": "A", "price": 10},
        {"sku": "", "price": "NaN"},
        {"sku": None, "price": "abc"},
        {"sku": "A", "price": True},  # 布尔不计入均值
        {"sku": "B", "price": 20},
    ]
    profile = compute_profile(
        rows,
        [
            {"field": "sku", "metric": "null_rate"},
            {"field": "sku", "metric": "distinct_rate"},
            {"field": "price", "metric": "mean"},
        ],
    )
    assert profile[("sku", "null_rate")] == pytest.approx(2 / 5)
    # 非空 sku: A, A, B → 去重 2 / 3
    assert profile[("sku", "distinct_rate")] == pytest.approx(2 / 3)
    # 可解析有限数字：10, 20（"NaN" 非有限、"abc" 不可解析、True 排除）
    assert profile[("price", "mean")] == pytest.approx(15.0)
    assert compute_profile([], [{"field": "x", "metric": "null_rate"}])[("x", "null_rate")] == 0.0


def test_compare_threshold_modes():
    from app.engines.profile_engine import compare

    # abs：绝对差 > 阈值 → 超阈
    assert compare(0.5, 0.1, 0.15, "abs")["breached"] is True
    assert compare(0.2, 0.1, 0.15, "abs")["breached"] is False
    # rel：绝对差 > 阈值 × |基线| → 超阈
    assert compare(12.0, 10.0, 0.5, "rel")["breached"] is False  # 2 <= 5
    assert compare(16.0, 10.0, 0.5, "rel")["breached"] is True  # 6 > 5
    # rel 基线为 0 退化为绝对差
    v = compare(0.3, 0.0, 0.2, "rel")
    assert v["breached"] is True and "退化" in v["note"]
    # 本批无可用数值 → 跳过不判超阈
    v = compare(None, 1.0, 0.1, "abs")
    assert v["breached"] is False and "跳过" in v["note"]
