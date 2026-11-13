from __future__ import annotations

import sqlite3
from typing import Any

DEFAULT_CONFIG = {"gate_enabled": 1, "update_policy": "frozen"}
UPDATE_POLICIES = ("frozen", "rolling")


def _baseline_row(r: sqlite3.Row) -> dict[str, Any]:
    return dict(r)


def list_baselines(conn: sqlite3.Connection, dataset_id: int | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, dataset_id, field, metric, baseline_value, threshold, threshold_mode,"
        "       enabled, updated_at, updated_from_batch_id"
        " FROM profile_baselines"
    )
    if dataset_id is None:
        rows = conn.execute(sql + " ORDER BY dataset_id, id").fetchall()
    else:
        rows = conn.execute(sql + " WHERE dataset_id=? ORDER BY id", (dataset_id,)).fetchall()
    return [_baseline_row(r) for r in rows]


def list_enabled_baselines(conn: sqlite3.Connection, dataset_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, dataset_id, field, metric, baseline_value, threshold, threshold_mode,
               enabled, updated_at, updated_from_batch_id
        FROM profile_baselines WHERE dataset_id=? AND enabled=1 ORDER BY id
        """,
        (dataset_id,),
    ).fetchall()
    return [_baseline_row(r) for r in rows]


def get_baseline(conn: sqlite3.Connection, baseline_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, dataset_id, field, metric, baseline_value, threshold, threshold_mode,
               enabled, updated_at, updated_from_batch_id
        FROM profile_baselines WHERE id=?
        """,
        (baseline_id,),
    ).fetchone()
    return _baseline_row(row) if row else None


def upsert_baseline(
    conn: sqlite3.Connection,
    *,
    dataset_id: int,
    field: str,
    metric: str,
    baseline_value: float,
    threshold: float,
    threshold_mode: str,
    enabled: bool,
    now: str,
) -> int:
    conn.execute(
        """
        INSERT INTO profile_baselines(
          dataset_id, field, metric, baseline_value, threshold, threshold_mode, enabled, updated_at
        ) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(dataset_id, field, metric) DO UPDATE SET
          baseline_value=excluded.baseline_value,
          threshold=excluded.threshold,
          threshold_mode=excluded.threshold_mode,
          enabled=excluded.enabled,
          updated_at=excluded.updated_at
        """,
        (dataset_id, field, metric, baseline_value, threshold, threshold_mode, 1 if enabled else 0, now),
    )
    row = conn.execute(
        "SELECT id FROM profile_baselines WHERE dataset_id=? AND field=? AND metric=?",
        (dataset_id, field, metric),
    ).fetchone()
    return int(row["id"])


def patch_baseline(conn: sqlite3.Connection, baseline_id: int, changes: dict[str, Any], now: str) -> None:
    allowed = {"baseline_value", "threshold", "threshold_mode", "enabled"}
    sets = []
    params: list[Any] = []
    for key, value in changes.items():
        if key not in allowed or value is None:
            continue
        sets.append(f"{key}=?")
        params.append((1 if value else 0) if key == "enabled" else value)
    if not sets:
        return
    sets.append("updated_at=?")
    params.append(now)
    params.append(baseline_id)
    conn.execute(f"UPDATE profile_baselines SET {', '.join(sets)} WHERE id=?", params)


def delete_baseline(conn: sqlite3.Connection, baseline_id: int) -> None:
    conn.execute("DELETE FROM profile_baselines WHERE id=?", (baseline_id,))


def apply_rolling_update(
    conn: sqlite3.Connection,
    baseline_id: int,
    *,
    value: float,
    batch_id: int,
    now: str,
) -> None:
    conn.execute(
        "UPDATE profile_baselines SET baseline_value=?, updated_at=?, updated_from_batch_id=? WHERE id=?",
        (value, now, batch_id, baseline_id),
    )


def get_config(conn: sqlite3.Connection, dataset_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT dataset_id, gate_enabled, update_policy FROM profile_gate_config WHERE dataset_id=?",
        (dataset_id,),
    ).fetchone()
    if not row:
        return {"dataset_id": dataset_id, **DEFAULT_CONFIG}
    return dict(row)


def put_config(conn: sqlite3.Connection, dataset_id: int, *, gate_enabled: bool, update_policy: str) -> None:
    if update_policy not in UPDATE_POLICIES:
        raise ValueError(f"未知基线更新策略 {update_policy!r}，可选 {UPDATE_POLICIES}")
    conn.execute(
        """
        INSERT INTO profile_gate_config(dataset_id, gate_enabled, update_policy) VALUES (?,?,?)
        ON CONFLICT(dataset_id) DO UPDATE SET
          gate_enabled=excluded.gate_enabled,
          update_policy=excluded.update_policy
        """,
        (dataset_id, 1 if gate_enabled else 0, update_policy),
    )
