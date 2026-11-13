from fastapi import APIRouter, HTTPException

from app.db import connect
from app.repositories import datasets as dataset_repo
from app.repositories import profile as profile_repo
from app.schemas.entities import ProfileBaselinePatch, ProfileBaselineUpsert, ProfileConfigPut
from app.seed import _now

router = APIRouter(tags=["profile-gate"])


@router.get("/profile-gate/{dataset_id}")
def get_profile_gate(dataset_id: int):
    conn = connect()
    try:
        if not dataset_repo.get_dataset(conn, dataset_id):
            raise HTTPException(404, "dataset not found")
        return {
            "config": profile_repo.get_config(conn, dataset_id),
            "baselines": profile_repo.list_baselines(conn, dataset_id),
        }
    finally:
        conn.close()


@router.put("/profile-gate/{dataset_id}/config")
def put_profile_config(dataset_id: int, body: ProfileConfigPut):
    conn = connect()
    try:
        if not dataset_repo.get_dataset(conn, dataset_id):
            raise HTTPException(404, "dataset not found")
        profile_repo.put_config(
            conn, dataset_id, gate_enabled=body.gate_enabled, update_policy=body.update_policy
        )
        conn.commit()
        return profile_repo.get_config(conn, dataset_id)
    finally:
        conn.close()


@router.post("/profile-gate/{dataset_id}/baselines")
def upsert_baseline(dataset_id: int, body: ProfileBaselineUpsert):
    conn = connect()
    try:
        if not dataset_repo.get_dataset(conn, dataset_id):
            raise HTTPException(404, "dataset not found")
        baseline_id = profile_repo.upsert_baseline(
            conn,
            dataset_id=dataset_id,
            field=body.field.strip(),
            metric=body.metric,
            baseline_value=body.baseline_value,
            threshold=body.threshold,
            threshold_mode=body.threshold_mode,
            enabled=body.enabled,
            now=_now(),
        )
        conn.commit()
        return profile_repo.get_baseline(conn, baseline_id)
    finally:
        conn.close()


@router.patch("/profile-gate/baselines/{baseline_id}")
def patch_baseline(baseline_id: int, body: ProfileBaselinePatch):
    conn = connect()
    try:
        if not profile_repo.get_baseline(conn, baseline_id):
            raise HTTPException(404, "baseline not found")
        changes = body.model_dump(exclude_unset=True)
        profile_repo.patch_baseline(conn, baseline_id, changes, _now())
        conn.commit()
        return profile_repo.get_baseline(conn, baseline_id)
    finally:
        conn.close()


@router.delete("/profile-gate/baselines/{baseline_id}")
def delete_baseline(baseline_id: int):
    conn = connect()
    try:
        if not profile_repo.get_baseline(conn, baseline_id):
            raise HTTPException(404, "baseline not found")
        profile_repo.delete_baseline(conn, baseline_id)
        conn.commit()
        return {"deleted": baseline_id}
    finally:
        conn.close()
