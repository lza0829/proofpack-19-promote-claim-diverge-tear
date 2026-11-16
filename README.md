# 05-proofpack（核证匣）

Proofpack — 举证放行：采样抽取 → 证据打包 → 校验清单 → 放行/驳回闭环。

与 Feedstitch（多源归并）、Batchgate（作业编排）、Syncledger（双端对账）、Changecast（CDC 投影）主链路不同。夹具全本地，不连活库。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5500 |
| API | http://localhost:10500 |

## 主链

选举证对象 → 按固定种子采样 → 生成证据包（含指纹）→ 跑基础校验清单 → 全过才可放行，否则驳回并留痕

## 种子举证对象

- 门店日结抽检
- 渠道结算抽检
- 价目变更核证

## 影子试跑与规则版本

落库策略采用**影子只读试跑**：影子结果可查，但晋升前不落正式清单表；晋升时原子切换规则版本，并要求受影响未放行包复检。

- **规则版本**：新规则一律先以影子版存在（基础校验恒定包含，增强校验可选挂）；有且仅有一个正式版。
- **影子试跑**：对指定证据包发起，结果只写影子轨（`shadow_runs`），不动正式清单、包状态与放行闸；影子未过不得单独拒绝正式放行。
- **晋升**：单事务原子完成——旧正式版退役、目标影子版转正式、受影响未放行包（已校验未放行）打回复检、写入晋升留痕（何时、从哪版到哪版、波及哪些包）。晋升后新正式规则立即生效于后续清单与放行。
- **放行闸**：只读正式轨（`checklist_results`），且清单必须基于当前正式规则版本——晋升后不得沿用旧正式通过态跳过复检。
- **互证**：影子命中数、正式通过/未过、晋升记录与包状态在「规则版本」页与总览交叉对照。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 本地后端

```bash
cd backend
pip install -r requirements.txt
set DATA_DIR=%TEMP%\proofpack-data
uvicorn app.main:app --host 127.0.0.1 --port 10500
```

## 测试

```bash
cd backend
python -m pytest tests/ -q
```
