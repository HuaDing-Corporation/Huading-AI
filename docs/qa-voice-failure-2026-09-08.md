# 音色与失败计费隔离验收（2026-09-08）

## 结论

本轮在本地隔离环境中补齐了豆包人工音色订单、官方音色租户边界、CosyVoice 父视频成功/失败以及终态重放的 API/数据库级验收。新增 5 个用例通过，相关回归门禁通过；没有修改公共 `backend/app` 计费源码。

这不是生产 E2E 通过：未访问生产、未调用真实供应商、未使用真实密钥、未提交付费请求，也没有执行真实 broker 重投递或 PostgreSQL 行锁竞态。

- 分支：`codex/qa-voice-failure-20260908`
- 基线：`ad54dc805e2e255e50c298309e4c902eefab7951`（核对等于当时本地 `origin/develop`）
- 新增验收：`backend/tests/test_voice_billing_failure_acceptance.py`
- 运行日期与时区：2026-09-08，Asia/Shanghai

## 验收矩阵

| 场景 | 本轮证据 | 结果与边界 |
|---|---|---|
| 豆包创建报价与冻结 | 新用例经真实 HTTP estimate/submit，断言固定 30000、同键提交重放、单 operation/usage、钱包冻结 30000 | 通过；未改价格 |
| 管理员跨租户交付 | 平台管理员经真实 HTTP resolve 客户订单；首次 fulfill 后同请求重放、同动作变参冲突、相反 reject 冲突 | 通过；终态快照证明订单、operation、usage、audit、voice、provider registry 未被重写 |
| 管理员跨租户拒绝 | 首次 reject 后同请求重放、同动作变参冲突、相反 fulfill 冲突 | 通过；冻结全部释放、used 为 0、只写一条审计 |
| 用户订单隔离 | 付款用户可见；同租户另一用户与跨租户用户列表为空、详情 404 | 通过 |
| 已交付音色隔离 | 付款用户详情可见；同租户另一用户与跨租户用户详情 404 | 通过；更广的 catalog/estimate 隔离另有既有测试覆盖 |
| 官方豆包音色边界 | 平台 catalog 可见；另一客户租户 catalog 排除、detail/estimate 均 404；客户钱包及账务行不变 | 通过；本用例证明隔离请求无副作用。平台实际使用时不产生 30000 年费账务由既有 `test_confirmed_official_doubao_is_platform_only_and_never_backfilled_or_charged` 覆盖 |
| CosyVoice 创建免费 | 既有 provider/create/recovery 用例覆盖免费报价、创建与恢复重放 | 通过；本轮没有真实创建供应商调用 |
| CosyVoice 父视频预占 | 新用例真实 HTTP estimate/submit，断言唯一 `tts/character` 行、0.1000 积分/字、两条 reserved usage 与钱包预占守恒 | 通过 |
| mock TTS 后成功结算 | worker 实际执行 `tts_step`，fake Cosy provider 生成本地音频；断言 speaker/provider/文本路由、非零供应商成本、两条 usage 结算与钱包守恒 | 通过；最终视频交付步骤为最小 fake，不代表完整媒体供应商 E2E |
| mock TTS 后下游失败 | TTS 成功后 fake avatar 步骤抛普通异常 | 通过；operation 为 failed、两条 usage 全 released、reserved/used 均为 0，TTS telemetry 保留 |
| 重复 worker 投递 | 对成功和失败终态各再次投递同一 task | 通过；供应商总调用次数保持 1，任务、钱包、operation、usage 完整快照不变 |
| fail-first 终态 | failed 后再次 fail，再迟到 success | 通过；首次失败终态、错误与钱包快照不变。该用例是同进程串行回调，不替代 PostgreSQL 并发竞态 |
| 退款 grant | 本轮独立复核当前唯一 writer/caller 与锁序；并行 RV 做了坏数据反证 | 当前合法 API/并发调用图未发现错账可达路径；存在 P2 数据损坏防御缺口，详见下节 |
| typed in-progress 查询 | 独立复核 `_lookup_operation_once` → `_lookup_resource` → `_validate_stored_result` | 已确认公共源码缺陷：有 `result_type/result_id` 且 `result_payload=None` 的合法处理中操作会被按终态 payload 校验并返回 500；交由 BE-PENDING，本线未修 |

## 退款可达性与防御边界

本线独立静态核对到以下生产调用图：

- 唯一 grant writer 是 `backend/app/services/subscription.py:168` 的 `decide_credit_refund`。
- 唯一生产调用点是 `backend/app/services/brand_voice_orders.py:1162`，位于人工订单 resolve 的同一事务。
- `apply_pending_refund_grants` 定义于 `backend/app/services/subscription.py:313`，唯一生产调用点是同文件订阅激活路径 `:385`。
- reject 与 activation 都先锁同一 Tenant；resolve 还依序锁 operation、subscription、usage，终态、释放和 grant 在同一事务提交。当前公开请求不能传入 grant 的 tenant/user/source/operation 关联。

因此，当前合法 API/并发路径下未发现 wrong-user、wrong-source、wrong-terminal 或跨租户钱包误入；不把它定性为可利用生产漏洞。

最终签核 RV 报告在已发布基线上执行了四个独立 SQLite 探针（本线按总指挥要求未重复执行）：

- 合法的过期来源订阅 reject 会产生 pending grant；随后 activation 入账一次，重复 activation 不再入账。
- 分别直接损坏 grant 的金额（30001，而合法值为 30000）、user、source subscription；三种情况下 `brand_voice_order_read` 都拒绝该坏关联，但 activation 仍会加额度、把 grant 标记为 applied。

后三种前置坏行不能由当前唯一 writer/API 产生，只能来自直接 ORM/SQL、错误迁移/导入或未来错误内部 caller。建议后续将其作为 P2 防御纵深处理：apply 前复核 exact amount、grant↔operation/order/user/source 关联与 rejected terminal，并以事务 fail-closed 测试锁定。RV 未独立执行退款 PostgreSQL 并发测试。

## 隔离运行环境

- 临时 cwd：`%TEMP%\huading-qa-voice-failure-20260908`，目录中没有项目 `.env`。
- 临时 venv：Python 3.11.15；依赖按 lockfile 使用 `uv --offline --frozen` 准备，不写仓库环境。
- `DATABASE_URL=sqlite+pysqlite:///:memory:`。
- `TEST_POSTGRES_URL`、`TEST_POSTGRES_DSN` 与 `PRICING_READINESS_POSTGRES_URL` 均为空。
- LLM、DashScope、Seedance、OmniHuman、豆包 TTS/音色克隆、CosyVoice、OpenAI、APIMart 凭据均清空。
- `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 均指向 `127.0.0.1:1`。

本轮没有读取项目 `.env`、没有连接共享 PostgreSQL、生产、Redis 或真实供应商。

## 验证命令与结果

公共安全环境变量按上一节设置后，从临时 cwd 执行：

```powershell
$repo = 'C:\Users\Administrator\.codex\worktrees\58f1\华鼎AI全自动短视频引擎'
$python = "$env:TEMP\huading-qa-voice-failure-20260908\venv\Scripts\python.exe"

& $python -m pytest -o addopts= -q `
  "$repo\backend\tests\test_voice_billing_failure_acceptance.py"
# 5 passed, 1 warning

& $python -m ruff check `
  "$repo\backend\tests\test_voice_billing_failure_acceptance.py"
# All checks passed!
```

最终相关回归门禁包含以下 14 个模块：

```text
test_voice_billing_failure_acceptance.py
test_billing_operations.py
test_brand_voice_orders.py
test_brand_voice_pipeline.py
test_brand_voice_provider_registry.py
test_cosyvoice_voice_clone_provider.py
test_pricing_readiness.py
test_provider_costs.py
test_video_pricing_contract.py
test_video_pipeline_quota.py
test_brand_voice_order_concurrency.py
test_quota_concurrency.py
test_admin_console_api.py
test_task_recovery.py
```

结果：`598 passed, 27 skipped, 1 warning`。27 项跳过均为未提供本线独占 PostgreSQL 时的显式跳过：其中 25 项是品牌音色/配额行锁竞态，需要 `TEST_POSTGRES_URL`；2 项是 readiness 的 PostgreSQL reflection，需要 `PRICING_READINESS_POSTGRES_URL`。它们不是失败，也不能记为 PostgreSQL 已通过。唯一 warning 是 Starlette TestClient/httpx 迁移弃用提示。

## 不能由本轮替代的验收

- 真实豆包人工注册、交付、拒绝及跨订阅周期退款。
- 真实 CosyVoice TTS、真实数字人/视频供应商与最终媒体质量。
- 真实 Celery broker 的崩溃恢复、重复投递与跨进程回调。
- PostgreSQL 的 `FOR UPDATE`、唯一约束和并发 first-terminal-wins；必须使用本线独占、名称受控的本机测试库重新运行显式跳过项。
- 生产账户、生产浏览器、真实密钥、余额或付费调用。本轮没有触碰已结清的 `huading2` 验收账户。
- typed in-progress 查询缺陷修复后的端到端恢复验收；需在 BE-PENDING 精确候选 SHA 上重新执行。

## 变更范围

本分支只新增：

- `backend/tests/test_voice_billing_failure_acceptance.py`
- `docs/qa-voice-failure-2026-09-08.md`

没有修改 `backend/app`、共享 `conftest.py`、迁移、配置、依赖或生产数据。
