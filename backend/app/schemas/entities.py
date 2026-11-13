from typing import Literal

from pydantic import BaseModel, Field


class InspectRequest(BaseModel):
    dataset_id: int


class RuleEnablePatch(BaseModel):
    enabled: bool


class ProfileConfigPut(BaseModel):
    gate_enabled: bool
    update_policy: Literal["frozen", "rolling"]


class ProfileBaselineUpsert(BaseModel):
    field: str = Field(min_length=1)
    metric: Literal["null_rate", "distinct_rate", "mean"]
    baseline_value: float
    threshold: float = Field(ge=0)
    threshold_mode: Literal["abs", "rel"] = "abs"
    enabled: bool = True


class ProfileBaselinePatch(BaseModel):
    baseline_value: float | None = None
    threshold: float | None = Field(default=None, ge=0)
    threshold_mode: Literal["abs", "rel"] | None = None
    enabled: bool | None = None
