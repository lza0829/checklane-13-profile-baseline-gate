# 04-checklane（检道）

Checklane — 数据质检通道：入检批次装载 → 规则命中与严重度裁决 → 隔离/放行签发 → 签发清单与源批对账。

**不是** Feed 多源归并（Feedstitch），**不是** 作业依赖编排（Batchgate），**不是** CDC 变更流按序落库（Changeflow）。
**也不是** 报表看板 / CRUD 后台 / 电商订单 / OA 工单。

## 启动

`ash
docker compose up --build
`

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5400 |
| API | http://localhost:10400 |

本地开发（可选）：

`ash
# backend
cd backend && pip install -r requirements.txt
set DATA_DIR=..\\data
uvicorn app.main:app --reload --port 10400

# frontend
cd frontend && npm install && npm run dev
`

## 主链

登记数据集与规则 → 装载夹具批次入检 → 按规则裁决放行/隔离 → **批次画像 vs 基线门禁** → 签发到精选区 → 批次报告与各列表数字同源

## 画像基线门禁（profile_baseline_gate）

行级规则全过不代表批次正常：整批空值率 / 字段分布可能相对历史画像离谱。行级规则跑完后，对**本批全部扫描行**计算同口径画像并与已登记基线比对（维护入口：「画像门禁」页 / `GET|PUT|POST|PATCH|DELETE /api/profile-gate/*`）。

**监控指标口径**（`backend/app/engines/profile_engine.py`）：

- `null_rate` 空值率 = 字段为空的行数 / 本批扫描行数；「空」= 缺失、null、或去空白后为空字符串（0/false 不算空）。
- `distinct_rate` 去重率 = 非空取值去重个数 / 非空取值个数；无非空值时记 0。
- `mean` 均值 = 非空取值中可解析为有限数字者（布尔除外）的算术平均；无可用数值时跳过比对。

**比对口径（每条基线可配 `threshold_mode`）**：`abs` 绝对差 |本批 − 基线| > 阈值 → 超阈；`rel` 相对差 |本批 − 基线| > 阈值 × |基线| → 超阈（基线为 0 时退化为绝对差）。

**阻断**：任一启用监控字段超阈 → 整批不得新写入精选，批次状态 `profile_blocked`；行级 error 仍照常进隔离。未超阈才按原规则签发通过行。

**基线更新策略（互斥，`profile_gate_config.update_policy`）**：

- `frozen` 冻结：成功签发也不改基线；
- `rolling` 滚动：仅「未阻断且签发成功（released>0）」的批次按本批画像更新基线；
- 阻断批次一律禁止更新基线。

批次报告的 `profile_gate` 段给出各字段本批值 / 基线值 / 差值 / 是否超阈 / 隔离数 / 签发数，并通过 `checks` 与隔离表、精选表、基线表互证（刷新后基线与批次仍在）。

## 种子说明

- **Orders Snapshot**：JSONL 商品快照，含必填缺失与类型错误行，用于演示入检与隔离。画像策略 rolling，基线与夹具画像一致 → 入检未超阈、签发成功并滚动更新基线。
- **Inventory Snapshot**：库存快照，独立数据集。画像策略 frozen → 签发成功也不改基线。
- **Orders Drift Snapshot**：sku 空值率 50% 的漂移批次（基线 10%、abs 阈值 0.15）→ 入检超阈整批阻断 `profile_blocked`，6 行行级 error 照常进隔离，基线不被更新。

打开「入检台」选数据集跑一次，再看隔离区 / 精选区 / 批次报告计数是否与本次报告一致；「画像门禁」页可维护基线/阈值/策略并查看阻断原因。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 0-1 切口（未实现，见题单）

当前仓库已实现「单批入检 + 基础规则 + 放行签发 + 画像基线门禁」主链。严重度整批阻断、规则包版本、抽样入检、跨字段规则、批次重开、金标对照、批内重复键、放行冻结窗、规则临时豁免、跨数据集参照检查、精选同键变更门禁、影子规则包、金丝雀两阶段签发等均在 backend/app/modules/* 留空包，按题单逐题换主链路实现。
