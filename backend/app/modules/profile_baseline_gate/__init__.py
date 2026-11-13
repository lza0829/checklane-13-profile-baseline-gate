"""画像基线门禁（profile_baseline_gate）— 已换主链路实现，本包为入口与口径说明。

行级规则全过不代表批次正常：整批空值率 / 字段分布可能相对历史画像离谱。
本模块在行级规则跑完后，对「本批全部扫描行」计算同口径画像并与已登记基线比对：

- 指标口径：null_rate（空值率）/ distinct_rate（去重率）/ mean（均值），
  详见 app/engines/profile_engine.py 模块 docstring。
- 比对口径（可配 threshold_mode）：abs 绝对差 / rel 相对差（基线为 0 时退化为绝对差）。
- 任一启用监控字段超阈 → 整批不得新写入精选，批次状态 profile_blocked；
  行级 error 仍照常进隔离。未超阈才按原规则签发通过行。
- 基线更新策略互斥（profile_gate_config.update_policy）：
  frozen 冻结——成功签发也不改基线；rolling 滚动——仅「未阻断且签发成功」
  的批次按本批画像更新基线。阻断批次一律禁止更新基线。

实现位置：
- 计算引擎  app/engines/profile_engine.py
- 持久化    app/repositories/profile.py（profile_baselines / profile_gate_config）
- 入检串联  app/services/inspect_service.py（evaluate_profile_gate / apply_baseline_policy）
- API       app/routers/profile.py（/api/profile-gate/*）
- 页面      frontend/src/pages/Profile.vue
"""
from app.engines.profile_engine import METRIC_NAMES, THRESHOLD_MODES, compare, compute_profile

__all__ = ["METRIC_NAMES", "THRESHOLD_MODES", "compare", "compute_profile"]
