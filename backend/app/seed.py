from datetime import datetime, timezone

from app.config import DATA_DIR
from app.db import connect


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings(
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS datasets(
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          fixture_path TEXT NOT NULL,
          entity_type TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          note TEXT
        );
        CREATE TABLE IF NOT EXISTS rules(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          field TEXT NOT NULL,
          rule_type TEXT NOT NULL,
          severity TEXT NOT NULL,
          params_json TEXT NOT NULL DEFAULT '{}',
          enabled INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        );
        CREATE TABLE IF NOT EXISTS inspect_batches(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          scanned_count INTEGER NOT NULL DEFAULT 0,
          passed_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          quarantined_count INTEGER NOT NULL DEFAULT 0,
          released_count INTEGER NOT NULL DEFAULT 0,
          report_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        );
        CREATE TABLE IF NOT EXISTS inspect_rows(
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL,
          row_no INTEGER NOT NULL,
          entity_key TEXT,
          payload_json TEXT NOT NULL,
          disposition TEXT NOT NULL,
          rule_hits_json TEXT NOT NULL,
          FOREIGN KEY(batch_id) REFERENCES inspect_batches(id)
        );
        CREATE TABLE IF NOT EXISTS quarantine(
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL,
          dataset_id INTEGER NOT NULL,
          row_no INTEGER NOT NULL,
          entity_key TEXT,
          reason TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS curated(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          batch_id INTEGER NOT NULL,
          entity_key TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          released_at TEXT NOT NULL,
          UNIQUE(dataset_id, entity_key)
        );
        CREATE TABLE IF NOT EXISTS profile_baselines(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          field TEXT NOT NULL,
          metric TEXT NOT NULL,
          baseline_value REAL NOT NULL,
          threshold REAL NOT NULL,
          threshold_mode TEXT NOT NULL DEFAULT 'abs',
          enabled INTEGER NOT NULL DEFAULT 1,
          updated_at TEXT NOT NULL,
          updated_from_batch_id INTEGER,
          UNIQUE(dataset_id, field, metric),
          FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        );
        CREATE TABLE IF NOT EXISTS profile_gate_config(
          dataset_id INTEGER PRIMARY KEY,
          gate_enabled INTEGER NOT NULL DEFAULT 1,
          update_policy TEXT NOT NULL DEFAULT 'frozen',
          FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        );
        """
    )
    n = conn.execute("SELECT COUNT(*) AS c FROM datasets").fetchone()["c"]
    if n == 0:
        conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            ("Orders Snapshot", "orders_snapshot.jsonl", "order", 1, "含缺失与类型错误行，演示入检"),
        )
        conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            ("Inventory Snapshot", "inventory_snapshot.jsonl", "sku", 1, "库存快照第二路"),
        )
        conn.executemany(
            "INSERT INTO rules(dataset_id, name, field, rule_type, severity, params_json, enabled) VALUES (?,?,?,?,?,?,?)",
            [
                (1, "sku 必填", "sku", "required", "error", "{}", 1),
                (1, "price 须为数字", "price", "type_number", "error", "{}", 1),
                (1, "qty 须为整数", "qty", "type_int", "warn", "{}", 1),
                (2, "sku 必填", "sku", "required", "error", "{}", 1),
                (2, "on_hand 须为整数", "on_hand", "type_int", "error", "{}", 1),
            ],
        )
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?)",
            ("release_mode", "pass_only"),
        )
    _seed_profile_gate(conn)
    conn.commit()
    conn.close()


def _dataset_id_by_name(conn, name: str) -> int | None:
    row = conn.execute("SELECT id FROM datasets WHERE name=?", (name,)).fetchone()
    return int(row["id"]) if row else None


def _seed_profile_gate(conn) -> None:
    """画像基线门禁种子（幂等）：漂移演示数据集 + 基线/阈值 + 各数据集互斥更新策略。"""
    now = _now()
    drift_id = _dataset_id_by_name(conn, "Orders Drift Snapshot")
    if drift_id is None:
        cur = conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            (
                "Orders Drift Snapshot",
                "orders_snapshot_drifting.jsonl",
                "order",
                1,
                "sku 空值率漂移批次，演示画像基线门禁整批阻断",
            ),
        )
        drift_id = int(cur.lastrowid)
        conn.executemany(
            "INSERT INTO rules(dataset_id, name, field, rule_type, severity, params_json, enabled) VALUES (?,?,?,?,?,?,?)",
            [
                (drift_id, "sku 必填", "sku", "required", "error", "{}", 1),
                (drift_id, "price 须为数字", "price", "type_number", "error", "{}", 1),
            ],
        )

    orders_id = _dataset_id_by_name(conn, "Orders Snapshot")
    inventory_id = _dataset_id_by_name(conn, "Inventory Snapshot")

    n = conn.execute("SELECT COUNT(*) AS c FROM profile_baselines").fetchone()["c"]
    if n == 0 and orders_id and inventory_id:
        # 基线值 = 各数据集健康历史画像；阈值口径 abs=绝对差 / rel=相对差
        conn.executemany(
            """
            INSERT INTO profile_baselines(
              dataset_id, field, metric, baseline_value, threshold, threshold_mode, enabled, updated_at
            ) VALUES (?,?,?,?,?,?,1,?)
            """,
            [
                (orders_id, "sku", "null_rate", 0.1667, 0.10, "abs", now),
                (orders_id, "sku", "distinct_rate", 1.0, 0.20, "abs", now),
                (orders_id, "price", "mean", 10.8, 0.50, "rel", now),
                (inventory_id, "sku", "null_rate", 0.25, 0.10, "abs", now),
                (inventory_id, "on_hand", "mean", 6.0, 0.50, "rel", now),
                (drift_id, "sku", "null_rate", 0.10, 0.15, "abs", now),
                (drift_id, "sku", "distinct_rate", 1.0, 0.20, "abs", now),
                (drift_id, "price", "mean", 10.8, 0.50, "rel", now),
            ],
        )

    n = conn.execute("SELECT COUNT(*) AS c FROM profile_gate_config").fetchone()["c"]
    if n == 0 and orders_id and inventory_id:
        # 更新策略互斥：rolling=未阻断且签发成功才按本批画像滚动更新；frozen=成功签发也不改基线
        conn.executemany(
            "INSERT INTO profile_gate_config(dataset_id, gate_enabled, update_policy) VALUES (?,?,?)",
            [
                (orders_id, 1, "rolling"),
                (inventory_id, 1, "frozen"),
                (drift_id, 1, "rolling"),
            ],
        )
