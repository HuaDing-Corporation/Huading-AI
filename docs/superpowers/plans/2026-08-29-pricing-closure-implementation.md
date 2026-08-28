# 定价闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为口播文案、画面提示词、电商图片、豆包人工品牌音色和 CosyVoice 使用建立服务端权威报价、先冻结后结算、失败释放、幂等恢复及前端可视化闭环，同时保持延期和下线功能原状。

**Architecture:** 在现有订阅钱包与 `usage_records` 账本上增加轻量 `billing_operations` 协调层；统一价格策略产生 canonical `pricing_lines`，无状态签名报价绑定请求、用户、租户和费率 provenance，结算只读取不可变快照。自动业务在供应商调用前提交 reservation，豆包走独立人工订单、永久 provider-ID registry 与跨订阅退款凭证；前端只展示服务端金额，并通过幂等 operation 查询恢复未知结果。

**Tech Stack:** Python 3.11、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、PostgreSQL/SQLite、Celery；Next.js 16.2、React 19、TypeScript、TanStack Query、Vitest、MSW、Playwright；Nginx。

**Spec:** `docs/superpowers/specs/2026-08-28-pricing-closure-design.md`

## Global Constraints

- 生产环境继续冻结。本计划不授权部署、执行生产迁移、修改生产费率、写入生产 provider ID 或解除客户豆包路由门禁。
- 服务端默认价必须精确为：`script_generate/call = 1.0000`、`scene_prompt/call = 30.0000`、`image/image = 80.0000`、`voice_clone/call = 30000.0000`、`tts/character = 0.1000`；既有 fallback 同步为 `avatar/second = 180`、`video/second = 100`、`video_gen/second = 100`、`reverse_prompt/call = 100`。
- `script_generate`、`scene_prompt`、电商图片和 `tts/character` 使用 tenant → platform → code default；豆包创建/续费只用 platform → code default，禁止 tenant 覆盖；CosyVoice 创建固定免费且不读取 `credit_rates`。
- 电商抠图与 AI 模特图每个 validated item 固定按一张 1K 图片计量，不应用通用图片分辨率倍率；预置/非品牌音色的既有 TTS 价格与流程不因本轮额外叠加费用。
- `seedance_t2v`、`static_template` 和发布草稿继续不定价；发布草稿保持隐藏；营销海报保持下线和 HTTP 410。不得借本轮恢复入口或生成伪零价 operation。
- 现有套餐、VIP、角色、授权同意、素材合规和 provider 可用性门禁全部保留；estimate 与正式 submit 必须调用同一门禁，不能用只读报价探测或绕过无权功能。
- 金额、数量和小计使用有限且非负的 `Decimal`，禁止 float、NaN、Infinity、负数和静默裁零。先汇总精确小计，再用 `ROUND_CEILING` 产生整数钱包值；除 `cosyvoice_brand_voice_create` 外，收费 operation 必须大于零。
- canonical `pricing_lines` 是 token digest、snapshot、usage 分配和结算的唯一账务来源；展示用 simple `breakdown = []` 仍必须生成 line 0。`disclosures` 签名并持久化，但永不进入 subtotal 或 usage 金额。
- 报价只读，不创建业务记录、不改余额、不调用供应商。`quote_token` 有效期固定 10 分钟，base64url 后最多 4096 ASCII bytes；提交头固定为 `Idempotency-Key` 和 `X-Huading-Quote`。
- 报价 token 使用从 `JWT_SECRET_KEY` 以 HMAC context `huading-billing-quote-v1` 派生的独立密钥，并固定校验 `typ=billing_quote`、`aud=huading-billing-quote`、schema version；不得把 access token 当报价 token。
- `result_payload` 经业务 response schema 校验后最多 64 KiB；`error_payload` 经应用错误 schema 清洗后最多 16 KiB；两者不得保存供应商密钥、原始异常或未裁剪日志。
- 电商 estimate 与 submit 共用规范化器，批量固定 `1..20`；第 21 项直接 422，不得出现 `items[:20]` 或 mock `.slice(0, 20)`。
- 同 key 同请求先重放持久化结果，不重新验证已过期报价；同 key 不同请求返回 `IDEMPOTENCY_KEY_REUSED`。任何供应商调用前，reservation 事务必须已经提交。
- 积分不足固定返回 `TENANT_QUOTA_EXCEEDED`；收费写入在没有 active subscription 时固定返回 `SUBSCRIPTION_NOT_FOUND`。两者都不得创建 operation/task/order 或调用供应商。
- `UsageRecord.unit/quantity/credits` 永远保存用户计费分配；供应商 token/字符用量写入新的 `provider_usage` JSON，成本 helper 不得覆盖计费字段。
- 品牌音色视频若进入 `billing_quote` 分支，必须提交非空 `script`；`strip()` 后的文本同时用于字符报价、`VideoTask.params["billing_tts_text"]`、供应商 TTS 和成本字符数。缺失时返回 422 `BILLABLE_TEXT_REQUIRED`。
- 豆包订单提交只冻结 `30000` 并创建 `awaiting_fulfillment`；系统不得注册豆包音色、自动分配 slot 或创建假可用音色。管理员 fulfill 后才结算并从服务端事务时间起算 365 天；reject 完整释放或创建唯一跨期退款凭证。
- 官方自用豆包音色不迁移、不收费、不转为用户权益；客户 provider ID 永久不可复用。未知历史豆包记录、官方配置缺失或 registry 不一致时客户下单与 resolve 必须 fail closed。
- 新人工豆包音色仅付款用户可见、可选、可改名和可删除；删除只做本地软删除、不退款、不调用豆包、不释放 provider ID。CosyVoice 与非豆包历史记录保持现有 tenant 级兼容。
- 涉及订阅生命周期、退款或人工 resolve 的锁顺序固定为 Tenant → BillingOperation → Subscriptions（主键升序）→ UsageRecords（主键升序）→ RefundGrants → provider advisory lock/registry/ProviderConfig → 业务记录。数据库冲突最多重试 3 次并抖动，供应商调用不在重试事务内。
- 后端每个功能改动必须先写失败测试，再写最小实现；SQLite 与真实 PostgreSQL 的约束/并发行为都要验证。每个任务独立提交，不混入无关重构。

---

## File Structure and Dependency Order

核心依赖顺序为：数据库账务核心 → 价格策略 → 报价签名 → operation 生命周期 → 自动业务 → 视频 composite 定价 → 人工品牌音色 → 前端公共计费层 → 各业务 UI → 工作台与全量验证。子系统共享同一 `BillingQuote`、`PricingSnapshot`、`BillingSummary` 和锁顺序，因此作为一个互相依赖的实施计划执行，不拆成可独立上线的子项目。

**新增后端核心文件**

- `backend/app/schemas/billing.py`：报价、pricing line、disclosure、summary、operation lookup 的唯一 API schema。
- `backend/app/services/pricing.py`：策略表、费率 provenance、Decimal 校验、canonical pricing 和聚合取整。
- `backend/app/services/billing_quotes.py`：请求规范化、digest、签发/验证 token、snapshot 构造。
- `backend/app/services/billing_operations.py`：幂等查找、聚合预留、严格结算/释放、安全重放与查询。
- `backend/app/services/transaction_retry.py`：仅对 PostgreSQL deadlock/serialization 做最多三次的有界事务重试。
- `backend/app/services/ecom_billing.py`：电商单/批请求规范化与 operation finalizer。
- `backend/app/services/video_pricing.py`：唯一 effective mode、音色上下文、三种 estimate contract、可计费 TTS 文本。
- `backend/app/services/provider_voice_registry.py`：豆包 provider ID 的全局锁、保护登记、官方清单与存量核验。
- `backend/app/services/brand_voice_orders.py`：人工订单创建、查询、resolve 与严格财务对账。
- `backend/app/services/asset_retention.py`：待人工订单的源素材与续费目标删除保护。
- `backend/app/api/v1/routes/billing.py`、`backend/app/api/v1/routes/brand_voice_orders.py`：用户查询与订单 API。
- `backend/app/schemas/brand_voice_orders.py`：人工订单请求、资源和管理员 resolve 联合。
- `backend/scripts/ops/pricing_closure_readiness.py`：只读生产前清单；默认绝不写库。
- `backend/alembic/versions/20260829_0036_billing_core.py`：operation、usage 关联和 provider usage。
- `backend/alembic/versions/20260829_0037_pricing_rates.py`：金额/费率约束、唯一索引和 CAS seed。
- `backend/alembic/versions/20260829_0038_manual_brand_voice.py`：人工订单、provider registry、refund grant 与音色权益字段。

**新增前端与可视化文件**

- `frontend/src/lib/api/billing.ts`：严格计费联合、headers、lookup 与运行时解析。
- `frontend/src/lib/billing/use-billing-action.ts`：estimate → confirm → submit → lookup 状态机。
- `frontend/src/components/billing/pricing-confirm-dialog.tsx`、`billing-status.tsx`：公共报价确认与资金状态。
- `frontend/src/lib/api/brand-voice-orders.ts`、`frontend/src/components/brand-voice/brand-voice-order-list.tsx`：客户人工订单。
- `frontend/src/app/(admin)/admin/brand-voice-orders/page.tsx`：复用现有管理员 layout、导航和访问保护的最小人工交付队列。
- `docs/04-UIUX设计/定价工作台.html`：仓库中不存在旧文件，因此在现有独立 HTML 设计文档目录创建唯一的源码化工作台；不得声称覆盖了一个不存在的旧文件。
- `frontend/e2e/pricing-closure.smoke.spec.ts`、`brand-voice-manual-order.smoke.spec.ts`、`pricing-workbench-doc.smoke.spec.ts`：端到端与独立工作台验证。

---

### Task 1: 建立 BillingOperation、UsageRecord 关联与核心迁移

**Files:**
- Modify: `backend/app/db/models.py`（`CreditRate`、`UsageRecord` 相邻区域及新增 `BillingOperation`）
- Create: `backend/alembic/versions/20260829_0036_billing_core.py`
- Create: `backend/tests/test_billing_core_migration.py`
- Modify: `backend/tests/test_db_schema_0001.py`

**Interfaces:**
- Produces: `BillingOperation.status: Literal["in_progress", "completed"]`
- Produces: `BillingOperation.completion_kind: Literal["succeeded", "failed", "rejected"] | None`
- Produces: `UsageRecord.billing_operation_id: str | None`, `billing_item_index: int | None`, `billing_pricing_line_index: int | None`, `provider_usage: dict[str, object] | None`
- Produces DB uniqueness: `(tenant_id, user_id, operation, idempotency_key)` and `(billing_operation_id, billing_item_index)`.

- [ ] **Step 1: Write failing migration and schema tests**

Create tests that assert the table/columns, `ON DELETE RESTRICT`, payload columns, item indexes, and state/money CHECK constraints. Include this exact minimum:

```python
def test_billing_operation_rejects_completed_without_conservation(db_session):
    operation = BillingOperation(
        tenant_id="tenant-a",
        user_id="user-a",
        operation="scene_prompt",
        idempotency_key="11111111-1111-4111-8111-111111111111",
        request_hash="a" * 64,
        quote_hash="b" * 64,
        pricing_snapshot={"pricing_lines": [{"subtotal_credits": "30.0000"}]},
        requested_credits=30,
        settled_credits=29,
        released_credits=0,
        status="completed",
        completion_kind="succeeded",
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(operation)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_usage_record_keeps_provider_usage_separate_from_billing_allocation(db_session):
    usage = make_usage_record(
        unit="call",
        quantity=Decimal("1"),
        credits=Decimal("30.0000"),
        provider_usage={"input_tokens": 41, "output_tokens": 9},
    )
    db_session.add(usage)
    db_session.commit()
    assert usage.unit == "call"
    assert usage.quantity == Decimal("1")
    assert usage.credits == Decimal("30.0000")
```

Also cover duplicate user-level key rejection, negative operation amounts, invalid in-progress completion fields, non-zero CosyVoice-free operation, zero non-CosyVoice operation, invalid usage indexes, and downgrade refusal once a billing row exists.

- [ ] **Step 2: Run the focused tests and verify failure**

Run from `backend/`:

```powershell
uv run pytest tests/test_billing_core_migration.py tests/test_db_schema_0001.py -q
```

Expected: FAIL because `billing_operations`, the four usage columns, indexes, and conservation constraints do not exist.

- [ ] **Step 3: Add the model and migration**

Add `BillingOperation` with the exact field set from the spec. Use JSON/JSONB through the repository `_json_type()` helper. The central state invariant in both model and migration must be equivalent to:

```python
BILLING_STATE_CHECK = """
(
  status = 'in_progress'
  AND completion_kind IS NULL
  AND completed_at IS NULL
  AND settled_credits = 0
  AND released_credits = 0
)
OR
(
  status = 'completed'
  AND completion_kind IN ('succeeded', 'failed', 'rejected')
  AND completed_at IS NOT NULL
  AND settled_credits + released_credits = requested_credits
)
"""

BILLING_ZERO_CHECK = """
(operation = 'cosyvoice_brand_voice_create' AND requested_credits = 0)
OR
(operation <> 'cosyvoice_brand_voice_create' AND requested_credits > 0)
"""

BILLING_COMPLETION_CHECK = """
(completion_kind NOT IN ('failed', 'rejected') OR (settled_credits = 0 AND released_credits = requested_credits))
AND
(completion_kind <> 'succeeded' OR requested_credits = 0 OR settled_credits > 0)
"""
```

Add application-visible maximum constants `RESULT_PAYLOAD_MAX_BYTES = 64 * 1024` and `ERROR_PAYLOAD_MAX_BYTES = 16 * 1024` next to the service-facing model. Add `provider_usage` without migrating or rewriting historic `UsageRecord.unit/quantity/credits`. The downgrade must raise when `billing_operations` contains rows instead of deleting financial history.

- [ ] **Step 4: Run migration tests on SQLite and PostgreSQL-compatible schema paths**

```powershell
uv run pytest tests/test_billing_core_migration.py tests/test_db_schema_0001.py -q
```

Expected: PASS, including zero-price exception, terminal conservation, unique idempotency scope, and RESTRICT behavior.

- [ ] **Step 5: Commit the billing core**

```powershell
git add backend/app/db/models.py backend/alembic/versions/20260829_0036_billing_core.py backend/tests/test_billing_core_migration.py backend/tests/test_db_schema_0001.py
git commit -m "feat(billing): add operation coordination ledger"
```

---

### Task 2: 实现价格策略、canonical pricing 与费率迁移

**Files:**
- Create: `backend/app/schemas/billing.py`
- Create: `backend/app/services/pricing.py`
- Create: `backend/alembic/versions/20260829_0037_pricing_rates.py`
- Create: `backend/tests/test_pricing_service.py`
- Create: `backend/tests/test_pricing_rates_migration.py`
- Modify: `backend/app/services/quota.py`

**Interfaces:**
- Produces: `resolve_rate(db: Session, *, tenant_id: str, policy: PricingPolicy, now: datetime | None = None) -> ResolvedRate`
- Produces: `build_simple_pricing(*, policy: PricingPolicy, rate: ResolvedRate, quantity: Decimal, disclosures: tuple[PricingDisclosure, ...] = ()) -> PricingDraft`
- Produces: `build_composite_pricing(*, operation: str, lines: Sequence[PricingLine], disclosures: tuple[PricingDisclosure, ...] = ()) -> PricingDraft`
- Produces: `validate_pricing_snapshot(snapshot: Mapping[str, object]) -> PricingSnapshot`
- Produces: `validate_credit_rate_candidate(*, tenant_id: str | None, capability: str, unit: str, credits_per_unit: Decimal, is_active: bool) -> None`.
- Produces: `activate_credit_rate(db: Session, *, rate_id: str, activated_at: datetime) -> CreditRate` using one transaction to deactivate the prior active row and activate the selected row.
- Produces policies keyed by `script_generate`, `scene_prompt`, `ecom_cutout`, `ecom_model`, `doubao_brand_voice_order_create`, `doubao_brand_voice_order_renew`, `cosyvoice_brand_voice_create`, `video_create`, `cosyvoice_brand_tts`.

- [ ] **Step 1: Write failing price-domain tests**

```python
@pytest.mark.parametrize(
    ("operation", "unit", "price", "scope"),
    [
        ("script_generate", "call", Decimal("1.0000"), RateScope.TENANT_OVERRIDABLE),
        ("scene_prompt", "call", Decimal("30.0000"), RateScope.TENANT_OVERRIDABLE),
        ("ecom_cutout", "image", Decimal("80.0000"), RateScope.TENANT_OVERRIDABLE),
        ("ecom_model", "image", Decimal("80.0000"), RateScope.TENANT_OVERRIDABLE),
        ("doubao_brand_voice_order_create", "call", Decimal("30000.0000"), RateScope.PLATFORM_FIXED),
        ("cosyvoice_brand_tts", "character", Decimal("0.1000"), RateScope.TENANT_OVERRIDABLE),
    ],
)
def test_policy_defaults(operation, unit, price, scope):
    policy = PRICING_POLICIES[operation]
    assert (policy.unit, policy.default_unit_credits, policy.scope) == (unit, price, scope)


def test_simple_quote_has_one_canonical_line_even_when_display_breakdown_is_empty():
    draft = build_simple_pricing(
        policy=PRICING_POLICIES["scene_prompt"],
        rate=code_default_rate(PRICING_POLICIES["scene_prompt"]),
        quantity=Decimal("1"),
    )
    assert draft.breakdown == ()
    assert len(draft.pricing_lines) == 1
    assert draft.payable_credits == 30


def test_composite_rounds_once_after_aggregation():
    draft = build_composite_pricing(
        operation="video_create",
        lines=(make_line("0.4"), make_line("0.4"), make_line("0.4")),
    )
    assert draft.subtotal_credits == Decimal("1.2")
    assert draft.payable_credits == 2
```

Add explicit failures for negative/zero/non-finite/over-precision rates and quantities, arithmetic mismatch, future rate exclusion, duplicate active rows, tenant precedence, platform fallback, code-default provenance, fixed-policy provenance, and tenant `voice_clone/call=42` being ignored by both Doubao policies. Test that the CosyVoice creation line is exactly quantity 1, unit price 0, subtotal 0, payable 0 and `source=fixed_policy`.

- [ ] **Step 2: Run the price and migration tests and verify failure**

```powershell
uv run pytest tests/test_pricing_service.py tests/test_pricing_rates_migration.py -q
```

Expected: FAIL because the strategy registry, provenance union, new rates, finite-value guards, and active-rate uniqueness are absent.

- [ ] **Step 3: Implement the exact price types and algorithms**

Define these enums/dataclasses, with Pydantic response models mirroring all Decimal values as JSON strings:

```python
class RateScope(StrEnum):
    TENANT_OVERRIDABLE = "tenant_overridable"
    PLATFORM_FIXED = "platform_fixed"


class RateSource(StrEnum):
    TENANT_RATE = "tenant_rate"
    PLATFORM_RATE = "platform_rate"
    CODE_DEFAULT = "code_default"
    FIXED_POLICY = "fixed_policy"


class ZeroRule(StrEnum):
    REQUIRE_POSITIVE = "require_positive"
    ALLOW_ZERO = "allow_zero"


@dataclass(frozen=True)
class PricingPolicy:
    operation: str
    capability: str
    unit: str
    default_unit_credits: Decimal
    scope: RateScope
    zero_rule: ZeroRule
    policy_key: str
    policy_version: int


@dataclass(frozen=True)
class ResolvedRate:
    unit_credits: Decimal
    source: RateSource
    rate_id: str | None
    effective_at: datetime | None
    policy_key: str | None
    policy_version: int | None


@dataclass(frozen=True)
class PricingLine:
    operation: str
    capability: str
    unit: str
    quantity: Decimal
    unit_credits: Decimal
    subtotal_credits: Decimal
    rate_scope: RateScope
    rate: ResolvedRate
    label: str


@dataclass(frozen=True)
class PricingDisclosure:
    key: str
    rendered_text: str
    copy_version: int
    unit: str
    rate_scope: RateScope
    rate: ResolvedRate


@dataclass(frozen=True)
class PricingDraft:
    operation: str
    pricing_shape: Literal["simple", "composite"]
    pricing_lines: tuple[PricingLine, ...]
    breakdown: tuple[PricingLine, ...]
    disclosures: tuple[PricingDisclosure, ...]
    subtotal_credits: Decimal
    payable_credits: int
```

Define the wire response explicitly; `pricing_contract` is always top-level and video estimate returns this object directly:

```python
class BillingQuote(BaseModel):
    pricing_contract: Literal["billing_quote"] = "billing_quote"
    operation: str
    pricing_shape: Literal["simple", "composite"]
    unit: str | None
    quantity: str | None
    unit_credits: str | None
    rate_scope: RateScope | None
    rate_source: RateSource | None
    subtotal_credits: str
    payable_credits: int
    breakdown: list[BillingPricingLine]
    disclosures: list[BillingDisclosure]
    quote_token: str
    expires_at: datetime

    @model_validator(mode="after")
    def validate_shape(self) -> "BillingQuote":
        simple_fields = (self.unit, self.quantity, self.unit_credits, self.rate_scope, self.rate_source)
        if self.pricing_shape == "simple" and (any(value is None for value in simple_fields) or self.breakdown):
            raise ValueError("simple quote requires top-level pricing fields and empty breakdown")
        if self.pricing_shape == "composite" and (any(value is not None for value in simple_fields) or not self.breakdown):
            raise ValueError("composite quote requires null top-level pricing fields and non-empty breakdown")
        return self
```

`BillingPricingLine` serializes every `PricingLine` field plus complete tagged rate provenance; `BillingDisclosure` serializes key, rendered text, copy version, unit/scope and complete provenance/reference rate. Neither exposes the internal canonical `pricing_lines`; simple line 0 is reconstructed internally from the validated top-level fields.

Implement a single finite Decimal validator; verify `unit × quantity == line_subtotal`, sum lines once, and calculate `int(subtotal.to_integral_value(rounding=ROUND_CEILING))`. Database sources require `rate_id/effective_at` and null policy fields; code/fixed sources require null row fields and non-null policy key/version.

`validate_credit_rate_candidate()` rejects negative/non-finite/over-precision values, active zero rows used by `REQUIRE_POSITIVE`, and any new or newly activated tenant-level `voice_clone/call` row. Historic tenant voice-clone rows remain readable for audit but cannot be activated through this service. There is no new general rate CRUD in this scope; every current/future writer and readiness check must call the validator and `activate_credit_rate()`.

- [ ] **Step 4: Add `0037` CAS migration and remove fallback drift**

The migration must first report exact IDs for duplicate active rows, negative/non-finite data, or active zero rows consumed by positive policies, then fail without updates. Extend both `credit_rates` and `usage_records` capability CHECKs for `script_generate` and `scene_prompt`; extend the `usage_records.unit` CHECK by retaining historic `char` and adding canonical `character`, while every new TTS allocation writes `character`. Add non-negative/finite guards for rate credits and usage quantity/credits, with SQLite and PostgreSQL migration tests for both TTS unit spellings. Add a platform partial unique index on `(capability, unit) WHERE tenant_id IS NULL AND is_active` and a tenant partial unique index on `(tenant_id, capability, unit) WHERE tenant_id IS NOT NULL AND is_active`. Seed using compare-and-set rules: image accepts only missing/`10`/`80`; voice clone only missing/`30`/`30000`; TTS only missing/`0.1`; script and scene only missing/target. Preserve all tenant rows and all history.

Replace duplicated `quota.py` literals with the policy table and set the exact legacy defaults listed in Global Constraints. New paths must raise an invariant error instead of using `max(0, value)`.

- [ ] **Step 5: Run focused tests**

```powershell
uv run pytest tests/test_pricing_service.py tests/test_pricing_rates_migration.py tests/test_video_pipeline_quota.py -q
uv run ruff check app/services/pricing.py app/schemas/billing.py app/services/quota.py tests/test_pricing_service.py tests/test_pricing_rates_migration.py
```

Expected: PASS; historic tenant voice-clone rows remain present but cannot affect Doubao quotes.

- [ ] **Step 6: Commit price policy and rates**

```powershell
git add backend/app/schemas/billing.py backend/app/services/pricing.py backend/app/services/quota.py backend/alembic/versions/20260829_0037_pricing_rates.py backend/tests/test_pricing_service.py backend/tests/test_pricing_rates_migration.py backend/tests/test_video_pipeline_quota.py
git commit -m "feat(pricing): centralize authoritative credit policies"
```

---

### Task 3: 签发并验证请求绑定的报价令牌

**Files:**
- Create: `backend/app/services/billing_quotes.py`
- Create: `backend/tests/test_billing_quotes.py`
- Modify: `backend/app/api/deps.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/tests/test_config_cors.py`

**Interfaces:**
- Consumes: `PricingDraft`, `PricingSnapshot`, `validate_pricing_snapshot()` from Task 2.
- Produces: `canonical_json(value: object) -> bytes`
- Produces: `request_sha256(value: object) -> str`
- Produces: `pricing_payload_sha256(draft: PricingDraft) -> str`
- Produces: `issue_quote(*, tenant_id: str, user_id: str, request_hash: str, draft: PricingDraft, now: datetime | None = None) -> BillingQuote`
- Produces: `verify_quote(*, token: str, tenant_id: str, user_id: str, operation: str, request_hash: str, current_draft: PricingDraft, now: datetime | None = None) -> VerifiedQuote`
- Produces dependency: `BillingSubmissionHeaders(idempotency_key: UUID, quote_token: str)`.

- [ ] **Step 1: Write failing token and header tests**

```python
def test_quote_binds_user_tenant_request_and_pricing(now):
    quote = issue_quote(
        tenant_id="tenant-a",
        user_id="user-a",
        request_hash="a" * 64,
        draft=scene_prompt_draft(),
        now=now,
    )
    verified = verify_quote(
        token=quote.quote_token,
        tenant_id="tenant-a",
        user_id="user-a",
        operation="scene_prompt",
        request_hash="a" * 64,
        current_draft=scene_prompt_draft(),
        now=now + timedelta(minutes=9),
    )
    assert verified.snapshot.payable_credits == 30


def test_quote_rejects_before_decode_when_header_exceeds_4096_bytes(client, auth_headers):
    response = client.post(
        "/api/v1/videos/scene-prompt",
        headers={**auth_headers, "Idempotency-Key": str(uuid4()), "X-Huading-Quote": "x" * 4097},
        json={"topic": "新品"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "QUOTE_TOKEN_TOO_LARGE"
```

Cover exact 10-minute expiry boundary, signature tampering, wrong user/tenant/operation/request, current rate/provenance or policy-version changes (`PRICE_CHANGED`), arithmetic-tampered snapshot, fixed `aud/typ/schema`, independent derived signing key, 20-item quote token ≤4096 bytes, and malformed UUID header.

- [ ] **Step 2: Run the focused tests and verify failure**

```powershell
uv run pytest tests/test_billing_quotes.py tests/test_config_cors.py -q
```

Expected: FAIL because canonical digest, derived signing key, quote claims, and billing headers do not exist.

- [ ] **Step 3: Implement canonical serialization and token claims**

Use stable UTF-8 JSON with sorted keys and compact separators; decimals enter canonical payload as normalized strings. Derive the key exactly once:

```python
QUOTE_TOKEN_TTL = timedelta(minutes=10)
QUOTE_TOKEN_MAX_BYTES = 4096
QUOTE_TOKEN_CONTEXT = b"huading-billing-quote-v1"
QUOTE_TOKEN_AUDIENCE = "huading-billing-quote"
QUOTE_TOKEN_TYPE = "billing_quote"
QUOTE_SCHEMA_VERSION = 1


def quote_signing_key(jwt_secret_key: str) -> bytes:
    return hmac.new(
        jwt_secret_key.encode("utf-8"),
        QUOTE_TOKEN_CONTEXT,
        hashlib.sha256,
    ).digest()
```

Token claims contain tenant, user, operation, request digest, pricing payload digest, subtotal, payable, issued/expiry times, audience, type and schema. `VerifiedQuote.snapshot` contains the complete canonical lines, disclosures, aggregate subtotal/payable and `ROUND_CEILING`; database provenance keeps row ID/effective time, while code/fixed provenance keeps null row fields plus policy key/version. Do not embed item breakdown in the token; return breakdown only in the `BillingQuote` response. Before decoding, reject `len(token.encode("ascii")) > 4096` and non-ASCII input.

- [ ] **Step 4: Add FastAPI dependencies for required and optional billing headers**

```python
class BillingSubmissionHeaders(BaseModel):
    idempotency_key: UUID
    quote_token: str


def require_billing_submission_headers(
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    quote_token: Annotated[str, Header(alias="X-Huading-Quote", min_length=1)],
) -> BillingSubmissionHeaders:
    try:
        encoded = quote_token.encode("ascii")
    except UnicodeEncodeError as exc:
        raise AppError(code="QUOTE_TOKEN_INVALID", message="报价凭证格式无效", status_code=422) from exc
    if len(encoded) > QUOTE_TOKEN_MAX_BYTES:
        raise AppError(code="QUOTE_TOKEN_TOO_LARGE", message="报价凭证过长", status_code=422)
    return BillingSubmissionHeaders(
        idempotency_key=idempotency_key,
        quote_token=quote_token,
    )
```

The optional dependency must return `None` only when both headers are absent; one present without the other is 422. When present it calls the same ASCII/4096-byte validator before any token decode. It is used solely by the shared video route to distinguish the three pricing contracts.

- [ ] **Step 5: Run focused tests and lint**

```powershell
uv run pytest tests/test_billing_quotes.py tests/test_config_cors.py -q
uv run ruff check app/services/billing_quotes.py app/api/deps.py app/core/config.py tests/test_billing_quotes.py
```

Expected: PASS; all mismatch cases create no billing operation and perform no supplier call.

- [ ] **Step 6: Commit quote signing**

```powershell
git add backend/app/services/billing_quotes.py backend/app/api/deps.py backend/app/core/config.py backend/tests/test_billing_quotes.py backend/tests/test_config_cors.py
git commit -m "feat(billing): sign request-bound pricing quotes"
```

---

### Task 4: 实现幂等预留、聚合结算、安全重放与 operation lookup

**Files:**
- Create: `backend/app/services/billing_operations.py`
- Create: `backend/app/services/transaction_retry.py`
- Create: `backend/app/api/v1/routes/billing.py`
- Create: `backend/tests/test_billing_operations.py`
- Create: `backend/tests/test_transaction_retry.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/services/quota.py`
- Modify: `backend/tests/test_quota_concurrency.py`
- Modify: `backend/tests/conftest.py`

**Interfaces:**
- Consumes: `VerifiedQuote`, `PricingSnapshot`, `BillingOperation`, billing header UUID.
- Produces: `BillingStart(operation: BillingOperation, replayed: bool)`.
- Produces: `UsageAllocation(item_index: int, pricing_line_index: int, quantity: Decimal, credits: Decimal, provider: str, model: str | None, video_task_id: str | None)`.
- Produces: `find_replay(db: Session, *, tenant_id: str, user_id: str, operation: str, idempotency_key: UUID, request_hash: str) -> BillingStart | None`
- Produces: `create_reserved_operation(db: Session, *, tenant_id: str, user_id: str, operation: str, idempotency_key: UUID, request_hash: str, verified_quote: VerifiedQuote, usage_allocations: Sequence[UsageAllocation], result_type: str | None = None, result_id: str | None = None) -> BillingOperation`
- Produces: `complete_succeeded(db: Session, *, operation_id: str, actual_quantities: Mapping[int, Decimal], result_type: str, result_id: str | None, result_payload: BaseModel) -> BillingOperation`
- Produces: `complete_failed(db: Session, *, operation_id: str, code: str, http_status: int, sanitized_detail: object | None) -> BillingOperation`
- Produces: `lookup_operation(db: Session, *, tenant_id: str, user_id: str, operation: str, idempotency_key: UUID) -> BillingOperationLookup | None`
- Produces: `register_billing_result_schema(result_type: str, schema: type[BaseModel]) -> None`.
- Produces: `run_db_transaction_with_retry(session_factory: sessionmaker[Session], operation: Callable[[Session], T], *, max_attempts: int = 3) -> T` for SQLSTATE `40P01`/`40001` only.
- Produces route: `GET /api/v1/billing/operations/by-idempotency/{operation}/{idempotency_key}`.

- [ ] **Step 1: Write failing lifecycle, concurrency, and lookup tests**

```python
def test_partial_success_rounds_success_subtotal_once(db_session, reserved_batch):
    operation = complete_succeeded(
        db_session,
        operation_id=reserved_batch.id,
        actual_quantities={0: Decimal("1"), 2: Decimal("1")},
        result_type="ecom_image_batch",
        result_id=reserved_batch.result_id,
        result_payload=EcomBatchResult(item_ids=["item-0", "item-2"]),
    )
    assert operation.requested_credits == 3
    assert operation.settled_credits == 2
    assert operation.released_credits == 1
    assert billing_summary(operation).status == "partially_settled"


def test_lookup_hides_same_tenant_other_users(client, user_a_headers, operation_for_user_b):
    response = client.get(
        f"/api/v1/billing/operations/by-idempotency/{operation_for_user_b.operation}/{operation_for_user_b.idempotency_key}",
        headers=user_a_headers,
    )
    assert response.status_code == 404


def test_same_key_same_request_does_not_reserve_twice(db_session, verified_quote):
    first = reserve_scene_prompt(db_session, verified_quote)
    second = reserve_scene_prompt(db_session, verified_quote)
    assert second.operation.id == first.operation.id
    assert second.replayed is True
    assert wallet_reserved(db_session) == 30
```

Also cover same key/different request, two-user isolation, concurrent first submission, `TENANT_QUOTA_EXCEEDED` and `SUBSCRIPTION_NOT_FOUND` with zero rows/zero supplier calls, zero-price CosyVoice idempotency, actual quantity shorter/equal to reservation, actual quantity greater than reservation failing without overcharge, repeated settle/release under row locks, 64/16 KiB bounds, schema-invalid stored payload fail closed, all four lookup unions, 404 invisibility, GET always 200 for visible failures, terminal conservation, and legacy unassociated reservations coexisting with operation-based reservations. Fault-inject PostgreSQL `40P01` and `40001` to prove at most three attempts with jitter, and prove all other SQLSTATEs are raised immediately.

- [ ] **Step 2: Run tests and verify failure**

```powershell
uv run pytest tests/test_billing_operations.py tests/test_quota_concurrency.py -q
```

Expected: FAIL because operation services, strict lock-aware settlement, and lookup route are absent.

- [ ] **Step 3: Implement reservation and idempotency under wallet locks**

Use the existing AIBrain pattern: initial lookup → lock active subscription → repeat lookup → validate snapshot and allocations → insert operation/usages → increment wallet reserved → commit. Each `UsageAllocation` includes the exact fields in Interfaces; `provider` is non-empty, `cost_cents` initializes to zero, and `video_task_id` links task-backed allocations. Simple batch uses item indices `0..N-1` and line 0, while composite video gives each component a unique item index matching its pricing line index.

```python
@dataclass(frozen=True)
class BillingStart:
    operation: BillingOperation
    replayed: bool


@dataclass(frozen=True)
class UsageAllocation:
    item_index: int
    pricing_line_index: int
    quantity: Decimal
    credits: Decimal
    provider: str
    model: str | None
    video_task_id: str | None
```

The externally returned summary must use these exact fields and derivation:

```python
class BillingSummary(BaseModel):
    operation_id: str
    idempotency_key: UUID
    status: Literal["reserved", "settled", "partially_settled", "released"]
    requested_credits: int
    held_credits: int
    settled_credits: int
    released_credits: int


def held_credits(operation: BillingOperation) -> int:
    if operation.status == "completed":
        return 0
    return operation.requested_credits
```

Never calculate requested wallet units from child usage rows. Before terminal mutation, `complete_succeeded()` revalidates every quoted allocation against the immutable snapshot, then requires each provided actual quantity to be finite, positive for a positive-price success, and no greater than its reserved quantity. It multiplies by the snapshot line price, sums all successful exact subtotals, rounds once, updates settled usage `quantity/credits` to the actual billed allocation, leaves omitted usages with their quoted allocation but marks them released, and moves the parent requested amount between used/released once. It never resolves a live rate. `complete_failed()` releases the entire parent and persists sanitized error data before returning. A task whose actual requirement exceeds its quote is failed/released and requires a new confirmed quote; no helper adds reservation or charges an overage.

- [ ] **Step 4: Implement typed lookup and replay validation**

Implement an initially empty closed registry through `register_billing_result_schema()`. Every business task registers its exact payload model while wiring the endpoint, and its focused test proves lookup round-trip. Persist only the business payload without a nested billing summary; lookup validates that payload and composes the current `BillingSummary` from the operation. Return the exact union: in-progress with optional typed resource; succeeded with typed result/resource; rejected with reviewed brand-order resource; failed with `{code, original_http_status, detail}`. Unknown types, invalid payloads or broken conservation raise an internal invariant error and emit a high-priority log; raw JSON is never returned.

- [ ] **Step 5: Implement bounded transaction retry without retrying suppliers**

`run_db_transaction_with_retry()` creates a fresh Session per attempt, rolls it back and closes it on failure, retries only deadlock/serialization SQLSTATEs `40P01`/`40001`, stops after three total attempts, and uses bounded random jitter between attempts. Reservation, completion, subscription activation/refund and manual resolve call this helper at orchestration boundaries. Supplier calls occur only after a successful reservation transaction and before a separate completion transaction, so a database retry never repeats a supplier request.

- [ ] **Step 6: Run focused tests and lint**

```powershell
uv run pytest tests/test_billing_operations.py tests/test_transaction_retry.py tests/test_quota_concurrency.py -q
uv run ruff check app/services/billing_operations.py app/services/transaction_retry.py app/api/v1/routes/billing.py tests/test_billing_operations.py tests/test_transaction_retry.py
```

Expected: PASS, including concurrent duplicate submission and exact partial-success rounding.

- [ ] **Step 7: Commit operation lifecycle**

```powershell
git add backend/app/services/billing_operations.py backend/app/services/transaction_retry.py backend/app/api/v1/routes/billing.py backend/app/api/v1/router.py backend/app/services/quota.py backend/tests/test_billing_operations.py backend/tests/test_transaction_retry.py backend/tests/test_quota_concurrency.py backend/tests/conftest.py
git commit -m "feat(billing): add idempotent reserve and settlement lifecycle"
```

---

### Task 5: 接入口播文案与画面提示词的同步计费闭环

**Files:**
- Modify: `backend/app/api/v1/routes/scripts.py`
- Modify: `backend/app/schemas/scripts.py`
- Modify: `backend/app/api/v1/routes/videos.py`（`scene_prompt` 路由）
- Modify: `backend/app/schemas/videos.py`（`ScenePromptResponse`）
- Modify: `backend/app/services/provider_costs.py`
- Modify: `backend/app/services/billing_operations.py`（注册脚本与提示词结果 schema）
- Create: `backend/tests/test_scripts_billing.py`
- Create: `backend/tests/test_scene_prompt_billing.py`
- Modify: `backend/tests/test_deepseek_provider.py`
- Modify: `backend/tests/test_video_pipeline_contract.py`

**Interfaces:**
- Consumes: `issue_quote()`, `verify_quote()`, `find_replay()`, `create_reserved_operation()`, `complete_succeeded()`, `complete_failed()`.
- Produces: `POST /api/v1/scripts/estimate -> BillingQuote(operation="script_generate")`.
- Produces: `POST /api/v1/videos/scene-prompt/estimate -> BillingQuote(operation="scene_prompt")`.
- Produces: `attach_deepseek_usage(record: UsageRecord, *, result: object) -> UsageRecord`.
- Produces: `attach_scene_prompt_usage(record: UsageRecord, *, result: object) -> UsageRecord`.
- Produces registrations: `script_generate_result -> ScriptGenerateStoredResult` and `scene_prompt_result -> ScenePromptStoredResult`; stored payloads exclude `billing`.
- Produces: success responses whose `data.billing` is required; controllable post-reservation failures expose the same schema at `error.detail.billing`.

- [ ] **Step 1: Write failing endpoint and cost-allocation tests**

```python
def test_script_reservation_commits_before_supplier_call(client, auth_headers, db_session, deepseek_mock):
    quote = estimate_script(client, auth_headers, {"topic": "新品介绍"})

    def assert_reserved_before_call(*args, **kwargs):
        db_session.expire_all()
        operation = latest_billing_operation(db_session, "script_generate")
        assert operation.status == "in_progress"
        assert operation.requested_credits == 1

    deepseek_mock.side_effect = assert_reserved_before_call
    submit_script(client, auth_headers, quote, {"topic": "新品介绍"})


def test_scene_failure_releases_and_keeps_provider_cost(client, auth_headers, luna_failure_with_usage):
    quote = estimate_scene_prompt(client, auth_headers, {"topic": "春季上新"})
    response = submit_scene_prompt(client, auth_headers, quote, {"topic": "春季上新"})
    assert response.status_code == 502
    billing = response.json()["error"]["detail"]["billing"]
    assert billing["status"] == "released"
    usage = usage_for_operation(billing["operation_id"])
    assert usage.unit == "call"
    assert usage.quantity == Decimal("1")
    assert usage.provider_usage["input_tokens"] > 0
```

Also test estimate read-only/zero supplier calls, missing/expired quote, request mutation, rate mutation, empty or invalid supplier result, response-loss replay returning the exact cleaned result, internal SDK retries sharing one reservation, and absence of a second zero-credit supplier-cost usage row.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
uv run pytest tests/test_scripts_billing.py tests/test_scene_prompt_billing.py tests/test_deepseek_provider.py tests/test_video_pipeline_contract.py -q
```

Expected: FAIL because the estimate routes, required quote gate, billing response fields, and in-place provider cost attachment are absent.

- [ ] **Step 3: Implement script estimate and submit**

Normalize the script request by excluding only billing headers, build a simple one-call draft, and keep the estimate route read-only. Submit order is fixed: compute request hash → replay lookup → verify current quote → create/commit one reservation → call provider → validate non-empty cleaned script → in one transaction attach provider usage, store schema-validated `ScriptGenerateResponse` as result payload, settle and commit → return result plus billing.

The response model must require the field:

```python
class ScriptGenerateResponse(BaseModel):
    script: str
    billing: BillingSummary
```

Define `ScriptGenerateStoredResult(script: str)` separately, register it as `script_generate_result`, and compose `ScriptGenerateResponse.billing` from the current operation during submit replay/lookup. Do the same with both cleaned prompt fields in `ScenePromptStoredResult`; never persist a stale nested summary.

On a controllable failure after operation creation, attach any parsed supplier usage/cost, call `complete_failed()`, commit, and raise the existing `AppError` with `detail={"billing": billing_summary(operation).model_dump(mode="json")}`.

- [ ] **Step 4: Implement scene-prompt estimate and submit**

Apply the same transaction order with operation `scene_prompt`, price `30`, and the existing positive/negative prompt schema. A result settles only after both cleaned prompts are non-empty and schema-valid. Persist the exact response payload before returning so same-key replay never invokes Luna/APIMart again.

- [ ] **Step 5: Change provider cost helpers to preserve billing allocation**

Replace the mutation of `unit/quantity/credits` with sanitized supplier fields:

```python
def attach_deepseek_usage(record: UsageRecord, *, result: object) -> UsageRecord:
    parsed = deepseek_usage_from_result(result)
    record.provider = parsed.provider
    record.model = parsed.model
    record.provider_usage = {
        "input_tokens": parsed.input_tokens,
        "output_tokens": parsed.output_tokens,
        "total_tokens": parsed.total_tokens,
    }
    record.cost_cents = parsed.cost_cents
    record.provider_cost_usd = parsed.provider_cost_usd
    return record
```

Implement the scene equivalent with its provider-specific sanitized fields. Failure parsing may leave `provider_usage=None`, but must never rewrite the billing allocation.

- [ ] **Step 6: Run tests and commit**

```powershell
uv run pytest tests/test_scripts_billing.py tests/test_scene_prompt_billing.py tests/test_deepseek_provider.py tests/test_video_pipeline_contract.py -q
uv run ruff check app/api/v1/routes/scripts.py app/api/v1/routes/videos.py app/services/provider_costs.py tests/test_scripts_billing.py tests/test_scene_prompt_billing.py
git add backend/app/api/v1/routes/scripts.py backend/app/schemas/scripts.py backend/app/api/v1/routes/videos.py backend/app/schemas/videos.py backend/app/services/provider_costs.py backend/app/services/billing_operations.py backend/tests/test_scripts_billing.py backend/tests/test_scene_prompt_billing.py backend/tests/test_deepseek_provider.py backend/tests/test_video_pipeline_contract.py
git commit -m "feat(billing): close script and scene prompt charging"
```

---

### Task 6: 接入电商单张/批量报价与聚合部分结算

**Files:**
- Create: `backend/app/services/ecom_billing.py`
- Modify: `backend/app/api/v1/routes/ecom_images.py`
- Modify: `backend/app/schemas/ecom_images.py`
- Modify: `backend/app/workers/image_gen.py`
- Modify: `backend/app/services/billing_operations.py`（注册电商结果 schema）
- Create: `backend/tests/test_ecom_image_billing.py`
- Modify: `backend/tests/test_ecom_images_pipeline.py`
- Modify: `backend/tests/test_image_gen_worker.py`

**Interfaces:**
- Consumes: `BillingQuote`, `VerifiedQuote`, `UsageAllocation`, operation lifecycle from Tasks 3–4.
- Produces: `normalize_cutout_items(payload: EcomCutoutRequest | EcomCutoutBatchRequest) -> tuple[EcomCutoutRequest, ...]`.
- Produces: `normalize_model_items(payload: EcomModelRequest | EcomModelBatchRequest) -> tuple[EcomModelRequest, ...]`.
- Produces: `try_finalize_ecom_operation(db: Session, *, billing_operation_id: str) -> BillingOperation | None`.
- Produces registration: `ecom_image_batch -> EcomImageBatchStoredResult`; payload stores typed task/item references but no billing summary.
- Produces estimates: `/api/v1/ecom-images/cutout/estimate` and `/api/v1/ecom-images/model/estimate`.

- [ ] **Step 1: Write failing normalization and aggregation tests**

```python
@pytest.mark.parametrize("count", [1, 20])
def test_ecom_batch_quote_quantity_matches_validated_items(client, auth_headers, count):
    payload = cutout_batch_payload(count)
    response = client.post("/api/v1/ecom-images/cutout/estimate", headers=auth_headers, json=payload)
    assert response.status_code == 200
    assert response.json()["data"]["quantity"] == str(count)
    assert response.json()["data"]["payable_credits"] == 80 * count


def test_ecom_batch_rejects_twenty_first_item_without_side_effects(client, auth_headers):
    response = client.post(
        "/api/v1/ecom-images/model/estimate",
        headers=auth_headers,
        json=model_batch_payload(21),
    )
    assert response.status_code == 422
    assert count_tasks() == 0
    assert count_billing_operations() == 0


def test_ecom_partial_success_rounds_once(client, auth_headers, decimal_image_rate):
    accepted = submit_three_item_batch(client, auth_headers, decimal_image_rate("0.4"))
    mark_items_terminal(accepted.id, succeeded={0, 2}, failed={1})
    finalized = try_finalize_ecom_operation(db_session(), billing_operation_id=accepted.billing.operation_id)
    assert finalized.settled_credits == 1
    assert finalized.released_credits == 1
```

Add tests that the single-item wrapper and one-item batch have identical request/pricing hashes; a different 20th item changes both hashes; task creation and all reservations roll back together on insufficient balance; success asset readability and item success share one transaction; all-success/all-failed/partial finalization is idempotent; and the old 10-credit fallback is absent.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
uv run pytest tests/test_ecom_image_billing.py tests/test_ecom_images_pipeline.py tests/test_image_gen_worker.py -q
```

Expected: FAIL because batch input is silently truncated, estimates are absent, and workers settle per item.

- [ ] **Step 3: Enforce one canonical `1..20` item list**

Use Pydantic constrained lists in both batch request schemas:

```python
class EcomCutoutBatchRequest(BaseModel):
    items: list[EcomCutoutRequest] = Field(min_length=1, max_length=20)


class EcomModelBatchRequest(BaseModel):
    items: list[EcomModelRequest] = Field(min_length=1, max_length=20)
```

The normalization functions wrap a single request into a one-element tuple and return a tuple copy of a validated batch. Estimate, submit, request digest, quote quantity, task creation, usage item indexes and worker enqueue must consume that exact tuple; remove every slice/truncation.

- [ ] **Step 4: Implement estimate, atomic submit, and finalizer**

Build one simple pricing line with quantity N and N usage allocations, each referencing line 0 with quantity 1 and exact per-image subtotal. In the submit transaction, create all tasks plus the operation and usages; commit before enqueueing workers. If enqueue fails, persist tasks as failed and invoke the same finalizer.

The worker records each item terminal state and readable `TaskAsset` in one transaction. `try_finalize_ecom_operation()` returns `None` until all N items are terminal; then it locks operation/subscription/usages/tasks in the global order, calls `complete_succeeded()` with `{successful_index: Decimal("1")}`, stores a schema-validated `EcomImageBatchStoredResult`, and commits the parent once. Register that result type so operation lookup rebuilds the current billing summary without trusting stored billing JSON.

- [ ] **Step 5: Run tests and commit**

```powershell
uv run pytest tests/test_ecom_image_billing.py tests/test_ecom_images_pipeline.py tests/test_image_gen_worker.py -q
uv run ruff check app/services/ecom_billing.py app/api/v1/routes/ecom_images.py app/schemas/ecom_images.py app/workers/image_gen.py
git add backend/app/services/ecom_billing.py backend/app/api/v1/routes/ecom_images.py backend/app/schemas/ecom_images.py backend/app/workers/image_gen.py backend/app/services/billing_operations.py backend/tests/test_ecom_image_billing.py backend/tests/test_ecom_images_pipeline.py backend/tests/test_image_gen_worker.py
git commit -m "feat(billing): aggregate ecom image reservations"
```

---

### Task 7: 统一视频三类定价契约与 CosyVoice 字符费

**Files:**
- Create: `backend/app/services/video_pricing.py`
- Modify: `backend/app/api/v1/routes/videos.py`
- Modify: `backend/app/schemas/videos.py`
- Modify: `backend/app/services/voices.py`
- Modify: `backend/app/workers/avatar_talk.py`
- Modify: `backend/app/services/provider_costs.py`
- Modify: `backend/app/services/billing_operations.py`（注册视频任务 resource schema）
- Create: `backend/tests/test_video_pricing_contract.py`
- Modify: `backend/tests/test_video_pipeline_contract.py`
- Modify: `backend/tests/test_video_pipeline_quota.py`
- Modify: `backend/tests/test_avatar_talk_worker.py`
- Modify: `backend/tests/test_batch_prod_pipeline.py`

**Interfaces:**
- Consumes: price/quote/operation services and existing video base-price calculators.
- Produces: `resolve_effective_video_mode(payload: VideoGenerateRequest) -> str`.
- Produces: `normalize_billable_tts_text(payload: VideoGenerateRequest) -> str`.
- Produces: `resolve_video_pricing_context(db: Session, *, user: User, payload: VideoGenerateRequest, requested_at: datetime | None = None) -> VideoPricingContext`.
- Produces: `build_video_estimate(db: Session, *, user: User, payload: VideoGenerateRequest) -> BillingQuote | LegacyVideoEstimate | DeferredUnpricedEstimate`.
- Produces discriminated `VideoAccepted` union with required billing only on `pricing_contract="billing_quote"`.
- Produces registration: `video_task -> VideoTaskBillingResource`, containing only the allowed task reference/status fields.

- [ ] **Step 1: Write failing routing and contract tests**

```python
@pytest.mark.parametrize(
    ("literal_mode", "legacy_field", "expected"),
    [
        ("static_template", {"voice_id": "voice-1"}, "avatar_talk"),
        ("seedance_t2v", {"avatar_asset_id": "asset-1"}, "avatar_talk"),
        ("static_template", {}, "static_template"),
        ("seedance_t2v", {}, "seedance_t2v"),
        ("photo", {"voice_id": "voice-1"}, "photo"),
        ("video_gen", {"voice_id": "voice-1"}, "video_gen"),
    ],
)
def test_effective_mode_is_single_source_of_truth(literal_mode, legacy_field, expected):
    payload = VideoGenerateRequest(video_mode=literal_mode, script="正文", **legacy_field)
    assert resolve_effective_video_mode(payload) == expected


def test_cosyvoice_brand_video_requires_frozen_script(client, auth_headers, cosy_voice):
    response = client.post(
        "/api/v1/videos/estimate",
        headers=auth_headers,
        json={"video_mode": "avatar_talk", "voice_id": cosy_voice.id, "script": None},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BILLABLE_TEXT_REQUIRED"


def test_cosyvoice_composite_has_one_tts_component_not_two(client, auth_headers, cosy_voice):
    quote = estimate_brand_video(client, auth_headers, voice_id=cosy_voice.id, script="  你好，世界！  ")
    tts_lines = [line for line in quote["breakdown"] if line["capability"] == "tts"]
    assert len(tts_lines) == 1
    assert tts_lines[0]["quantity"] == "6"
```

Add tests for three mutually exclusive estimate contracts, no token on legacy/deferred, fake billing headers not creating operations on those branches, server-side provider resolution, current-user Doubao ownership, expired voices, Doubao zero brand-character component, CosyVoice `0.1/字` counted from stripped text including punctuation, mixed rate provenance, quote replay before revalidating a later-deleted voice, task+operation+usages atomic creation, task text persistence, worker text equality, supplier call occurring only after reservation commit, actual video duration shorter/equal to reserved duration, and output exceeding reserved duration failing/releasing without an extra debit.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
uv run pytest tests/test_video_pricing_contract.py tests/test_video_pipeline_contract.py tests/test_video_pipeline_quota.py tests/test_avatar_talk_worker.py tests/test_batch_prod_pipeline.py -q
```

Expected: FAIL because estimate has one legacy shape, mode logic is duplicated, and billable TTS text is not frozen.

- [ ] **Step 3: Implement the single effective-mode and pricing-context resolver**

Implement precedence exactly: explicit `photo`/`seedance_i2v`/`video_gen`; otherwise explicit `avatar_talk` or any of `voice_id/avatar_asset_id/avatar_video_asset_id` means `avatar_talk`; only then retain true `static_template/seedance_t2v`. Normalize the request with `effective_video_mode` before hashing.

Resolve voice records server-side. If the effective mode is `avatar_talk` or `seedance_i2v` and the voice is a Doubao/CosyVoice brand voice, return `billing_quote`; preset voices return `legacy_estimate`; true deferred modes return `deferred_unpriced` with `estimated_credits=0`, `unpriced=true`, and no operation/token.

- [ ] **Step 4: Freeze TTS text and build the composite quote**

```python
def normalize_billable_tts_text(payload: VideoGenerateRequest) -> str:
    text = (payload.script or "").strip()
    if not text:
        raise AppError(
            code="BILLABLE_TEXT_REQUIRED",
            message="使用品牌音色前请先生成或填写口播文案",
            status_code=422,
        )
    return text
```

For branded video quotes, construct base video lines plus exactly one existing TTS line for CosyVoice and no brand-character line for Doubao. Do not add a second TTS charge to the existing avatar/video formula. Persist the normalized text in `VideoTask.params["billing_tts_text"]` after quote verification; the task `script` must equal the same string.

- [ ] **Step 5: Wire branded video submit into the operation lifecycle**

For a new billing-quote key, perform: replay lookup → resolve effective mode/voice/permissions → rebuild and verify quote → generate task ID → in one transaction create `VideoTask`, parent `video_create` operation, base-duration allocation and the one optional TTS allocation with `video_task_id` → commit → enqueue. A same-key replay is returned before current voice expiry/deletion/rate checks. The branded branch must not call legacy `reserve_quota`, `settle_reserved_quota` or `release_reserved_quota`; only `billing_operations` changes its wallet. Enqueue failure persists a failed task and completes/releases the operation through the same service.

- [ ] **Step 6: Make the worker consume frozen text and settle actual quantities**

For billing-quote brand tasks, `_tts_voice_for_task()` uses `VideoTask.created_by_user_id` for the ownership check and reads only `billing_tts_text`; it raises an invariant error if the value is absent or differs from `task.script`. Attach supplier character count/cost to `provider_usage` on the corresponding TTS allocation without creating a second user charge. On deliverable success, call `complete_succeeded()` with the measured base seconds and, for CosyVoice, the exact frozen character count; both must be less than or equal to their reserved snapshot quantities. If measured duration exceeds the reservation, do not expose the deliverable, complete the task failed, release the reservation and require a fresh user-confirmed quote. Register `VideoTaskBillingResource` as `video_task`; every supplier/validation/timeout failure calls `complete_failed()`. Legacy preset-voice tasks keep their current text-generation and fee behavior.

- [ ] **Step 7: Run tests and commit**

```powershell
uv run pytest tests/test_video_pricing_contract.py tests/test_video_pipeline_contract.py tests/test_video_pipeline_quota.py tests/test_avatar_talk_worker.py tests/test_batch_prod_pipeline.py -q
uv run ruff check app/services/video_pricing.py app/api/v1/routes/videos.py app/schemas/videos.py app/services/voices.py app/workers/avatar_talk.py
git add backend/app/services/video_pricing.py backend/app/api/v1/routes/videos.py backend/app/schemas/videos.py backend/app/services/voices.py backend/app/workers/avatar_talk.py backend/app/services/provider_costs.py backend/app/services/billing_operations.py backend/tests/test_video_pricing_contract.py backend/tests/test_video_pipeline_contract.py backend/tests/test_video_pipeline_quota.py backend/tests/test_avatar_talk_worker.py backend/tests/test_batch_prod_pipeline.py
git commit -m "feat(video): add branded voice pricing contracts"
```

---

### Task 8: 增加人工品牌音色、provider registry 与退款凭证数据模型

**Files:**
- Modify: `backend/app/db/models.py`（`BrandVoice` 在当前约 392–419 行；`AdminAuditLog` 在当前约 981–1009 行）
- Create: `backend/alembic/versions/20260829_0038_manual_brand_voice.py`
- Create: `backend/app/schemas/brand_voice_orders.py`
- Create: `backend/tests/test_manual_brand_voice_migration.py`
- Create: `backend/tests/test_pricing_closure_migration.py`（跨 `0036 → 0037 → 0038` 的综合升级、重复升级与 downgrade 保护）

**Interfaces:**
- Produces models: `BrandVoiceOrder`, `BrandVoiceProviderId`, `CreditRefundGrant`.
- Extends: `BrandVoice.owner_user_id`, `activated_at`, `expires_at`.
- Produces request schemas: `BrandVoiceOrderCreateRequest`, `BrandVoiceOrderResolveRequest`.
- Produces audit actions: `brand_voice_order_audio_access`, `brand_voice_order_fulfill`, `brand_voice_order_reject`.

- [ ] **Step 1: Write failing migration/model tests**

```python
def test_brand_voice_migration_does_not_backfill_historic_rows(upgrade_from_0037, historic_brand_voice):
    upgrade_from_0037("20260829_0038")
    row = load_brand_voice(historic_brand_voice.id)
    assert row.owner_user_id is None
    assert row.activated_at is None
    assert row.expires_at is None
    assert count_brand_voice_orders() == 0
    assert count_billing_operations() == 0


def test_only_one_awaiting_renewal_per_voice(db_session, expired_brand_voice):
    db_session.add(make_renew_order(expired_brand_voice.id, status="awaiting_fulfillment"))
    db_session.commit()
    db_session.add(make_renew_order(expired_brand_voice.id, status="awaiting_fulfillment"))
    with pytest.raises(IntegrityError):
        db_session.commit()
```

Cover every create/renew/fulfilled/rejected CHECK, unique operation/order link, provider ID global uniqueness and permanent retired rows, refund grant uniqueness and state fields, positive finite refund amounts, all RESTRICT FKs, admin action values, SQLite/PostgreSQL parity, and downgrade refusal when financial/order history exists.

- [ ] **Step 2: Run migration tests and verify failure**

```powershell
uv run pytest tests/test_manual_brand_voice_migration.py tests/test_pricing_closure_migration.py -q
```

Expected: FAIL because the three tables, rights fields, constraints, indexes, and audit actions do not exist.

- [ ] **Step 3: Add the exact models and constraints**

`BrandVoiceOrder` fields are: immutable tenant/payment user, `order_type`, requested name, source asset, sanitized immutable source metadata snapshot, consent time, optional existing voice, unique operation, status, fulfilled voice/provider ID, resolver, mutually exclusive fulfilled/rejected timestamps and rejection reason, timestamps. Create and renew both require current source audio and consent; renew requires an existing voice; create forbids it; fulfilled renewal must fulfill the same voice.

`BrandVoice.owner_user_id` is a nullable `ON DELETE RESTRICT` FK; historic rows remain null. `BrandVoiceProviderId` stores provider, normalized provider ID, `official|customer`, optional brand voice/first order, `active|retired`, and timestamps. There is no delete cascade and no function or migration that frees a retired ID.

`CreditRefundGrant` stores unique billing operation, tenant/payment user, source and optional target subscription, integer positive amount, `pending|applied`, created/applied times. Pending requires null target/time; applied requires both.

- [ ] **Step 4: Implement `0038` without business guesses**

Use `down_revision = "20260829_0037"`. Add the new fields as nullable and do not update any existing `brand_voices`, `ProviderConfig.config`, env-derived slot, wallet, usage, or operation. Add partial indexes for awaiting renewals and queue/user queries. The migration only changes schema and audit constraints; official registry rows are created later by an explicit readiness/registration action, never during migration.

- [ ] **Step 5: Run tests and commit**

```powershell
uv run pytest tests/test_manual_brand_voice_migration.py tests/test_pricing_closure_migration.py -q
uv run ruff check app/db/models.py app/schemas/brand_voice_orders.py tests/test_manual_brand_voice_migration.py
git add backend/app/db/models.py backend/app/schemas/brand_voice_orders.py backend/alembic/versions/20260829_0038_manual_brand_voice.py backend/tests/test_manual_brand_voice_migration.py backend/tests/test_pricing_closure_migration.py
git commit -m "feat(voice): add manual delivery order schema"
```

---

### Task 9: 建立 provider-ID registry、收紧平台管理员权限并退役 slot 写入口

**Files:**
- Create: `backend/app/services/provider_voice_registry.py`
- Modify: `backend/app/services/voice_slots.py`
- Modify: `backend/app/api/deps.py`（当前 `require_platform_admin()` 约 199–209 行）
- Modify: `backend/app/services/plan_access.py`（`admin_console` entitlement）
- Modify: `backend/app/api/v1/routes/admin_console.py`（voice-slot POST 当前约 185–215 行）
- Modify: `backend/app/services/admin_console.py`（slot service 当前约 356–390 行；inventory 当前约 1262–1323 行）
- Modify: `backend/app/core/config.py`
- Modify: `backend/scripts/ops/reset_billing_and_admin.py`
- Create: `backend/tests/test_brand_voice_provider_registry.py`
- Modify: `backend/tests/test_voice_slot_concurrency.py`
- Modify: `backend/tests/test_admin_console_api.py`
- Modify: `backend/tests/test_admin_vip_gate.py`
- Modify: `backend/tests/test_config_cors.py`

**Interfaces:**
- Produces: `normalize_provider_voice_id(value: str) -> str`.
- Produces: `lock_provider_voice_ids(db: Session, *, provider: str, provider_voice_ids: Sequence[str]) -> None`.
- Produces: `claim_customer_provider_voice_id(db: Session, *, provider_voice_id: str, brand_voice_id: str, order_id: str, previous_provider_voice_id: str | None = None) -> BrandVoiceProviderId`.
- Produces: `retire_customer_provider_voice_id(db: Session, *, brand_voice_id: str, provider_voice_id: str, retired_at: datetime) -> BrandVoiceProviderId`.
- Produces: `register_official_provider_voice_ids(db: Session, *, provider_voice_ids: Sequence[str]) -> list[BrandVoiceProviderId]`.
- Produces: `assert_doubao_registry_ready(db: Session) -> None` and `provider_voice_inventory(db: Session) -> ProviderVoiceInventory`.

- [ ] **Step 1: Write failing registry, permission, and retirement tests**

```python
def test_registry_rejects_id_found_only_in_inactive_provider_config(db_session):
    add_provider_config(
        db_session,
        active=False,
        config={"speaker_ids": ["voice-hidden"], "used_speaker_ids": {}},
    )
    with pytest.raises(AppError) as exc:
        claim_customer_provider_voice_id(
            db_session,
            provider_voice_id="voice-hidden",
            brand_voice_id="brand-a",
            order_id="order-a",
        )
    assert exc.value.code == "PROVIDER_VOICE_ID_CONFLICT"


def test_platform_non_admin_cannot_read_admin_console(client, platform_creator_headers):
    response = client.get("/api/v1/admin/console/brand-voice-orders", headers=platform_creator_headers)
    assert response.status_code == 403


def test_legacy_voice_slot_write_is_gone(client, platform_admin_headers):
    response = client.post(
        "/api/v1/admin/console/tenants/tenant-a/voice-slots",
        headers=platform_admin_headers,
        json={"speaker_id": "voice-new"},
    )
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "VOICE_SLOT_ASSIGNMENT_RETIRED"
```

Also test official env IDs, old env IDs, active/inactive ProviderConfig `speaker_ids` and `used_speaker_ids` keys, all BrandVoice rows including soft-deleted, active/retired registry rows, own-ID renewal reuse, cross-voice rejection, new/old ID sorted double-lock, same advisory key as `sha256("huading:voice-slot:<id>")`, concurrent claim/official registration, CLI `--apply` non-zero, dry-run inventory read-only, and platform admin success.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
uv run pytest tests/test_brand_voice_provider_registry.py tests/test_voice_slot_concurrency.py tests/test_admin_console_api.py tests/test_admin_vip_gate.py tests/test_config_cors.py -q
```

Expected: FAIL because there is no registry and platform-tenant membership currently grants admin access and slot writes.

- [ ] **Step 3: Implement normalized IDs, shared advisory locks, and exhaustive conflict scan**

Use the existing lock namespace exactly:

```python
DOUBAO_VOICE_CLONE_PROVIDER = "doubao-voice-clone"


def provider_voice_lock_key(provider_voice_id: str) -> int:
    normalized = normalize_provider_voice_id(provider_voice_id)
    digest = hashlib.sha256(f"huading:voice-slot:{normalized}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)
```

Acquire locks for sorted unique IDs. Inside the lock, query registry, both official/legacy env lists, every ProviderConfig regardless of active flag, and every BrandVoice regardless of `deleted_at`. A customer ID is claimable only if absent everywhere; renewal may reuse an active registry entry bound to the exact same BrandVoice. Switching IDs claims the new one and retires the old one atomically. Expose no release/reassign function.

- [ ] **Step 4: Add official config and strict readiness semantics**

Add `engine_doubao_official_voice_ids: list[str]` mapped from `ENGINE_DOUBAO_OFFICIAL_VOICE_IDS`. In production-mode readiness, missing/empty official configuration, mismatch with official registry, or any historical Doubao/slot ID outside the confirmed official set and customer registry must raise `DOUBAO_REGISTRY_NOT_READY`. Registration is an explicit admin/ops transaction using `register_official_provider_voice_ids()`; schema migration never invokes it.

- [ ] **Step 5: Retire all slot writes and require the actual admin role**

Strengthen `require_platform_admin()` to require both the platform tenant and `user.role == Role.ADMIN.value`; update session permissions so `admin_console` is absent for creator/ops roles. Keep inventory GETs for audit. Make HTTP slot POST return 410, make `assign_speaker_slot(..., apply=True)` raise `VOICE_SLOT_ASSIGNMENT_RETIRED`, and make the CLI apply mode exit non-zero without mutating ProviderConfig. Remove UI-facing service paths that release or re-add an ID; retain compatibility lock helpers as wrappers around the registry key.

- [ ] **Step 6: Run tests and commit**

```powershell
uv run pytest tests/test_brand_voice_provider_registry.py tests/test_voice_slot_concurrency.py tests/test_admin_console_api.py tests/test_admin_vip_gate.py tests/test_config_cors.py -q
uv run ruff check app/services/provider_voice_registry.py app/services/voice_slots.py app/api/deps.py app/services/plan_access.py app/api/v1/routes/admin_console.py app/services/admin_console.py
git add backend/app/services/provider_voice_registry.py backend/app/services/voice_slots.py backend/app/api/deps.py backend/app/services/plan_access.py backend/app/api/v1/routes/admin_console.py backend/app/services/admin_console.py backend/app/core/config.py backend/scripts/ops/reset_billing_and_admin.py backend/tests/test_brand_voice_provider_registry.py backend/tests/test_voice_slot_concurrency.py backend/tests/test_admin_console_api.py backend/tests/test_admin_vip_gate.py backend/tests/test_config_cors.py
git commit -m "feat(voice): protect provider ids and retire slot assignment"
```

---

### Task 10: 实现 Tenant 锁、跨期退款与始终可读的配额快照

**Files:**
- Modify: `backend/app/services/subscription.py`
- Modify: `backend/app/services/quota.py`
- Modify: `backend/app/api/v1/routes/quota.py`
- Modify: `backend/app/schemas/quota.py`
- Modify: `backend/app/services/admin_console.py`（`change_plan()` 当前约 271–319 行；`change_tenant_status()` 当前约 322–353 行）
- Modify: `backend/app/api/v1/routes/auth.py`（默认订阅激活调用）
- Create: `backend/tests/test_subscription_refund_grants.py`
- Modify: `backend/tests/test_quota_concurrency.py`
- Modify: `backend/tests/test_admin_console_api.py`

**Interfaces:**
- Produces: `lock_tenant_for_subscription_lifecycle(db: Session, *, tenant_id: str) -> Tenant`.
- Produces: `current_active_subscription_for_update(db: Session, *, tenant_id: str, now: datetime) -> Subscription | None`.
- Produces: `apply_pending_refund_grants(db: Session, *, tenant_id: str, target_subscription: Subscription, applied_at: datetime) -> int`.
- Produces: `activate_subscription(db: Session, *, tenant_id: str, plan: Plan, period_start: datetime) -> Subscription`.
- Produces: `tenant_quota_snapshot(db: Session, *, tenant_id: str) -> TenantQuotaSnapshot`.
- Produces `QuotaResponse` fields: `has_active_subscription`, `active_subscription_id`, `total`, `used`, `reserved`, `remaining`, `manual_fulfillment_held_credits`, `pending_refund_credits`.

- [ ] **Step 1: Write failing grant, activation, and quota tests**

```python
def test_quota_without_active_subscription_still_reports_cross_period_values(client, auth_headers):
    expire_current_subscription_with_manual_hold(30000)
    add_pending_refund_grant(30000)
    response = client.get("/api/v1/quota", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["data"] == {
        "has_active_subscription": False,
        "active_subscription_id": None,
        "total": 0,
        "used": 0,
        "reserved": 0,
        "remaining": 0,
        "manual_fulfillment_held_credits": 30000,
        "pending_refund_credits": 30000,
    }


def test_reject_and_activation_race_applies_grant_exactly_once(postgres_session_factory):
    run_reject_and_activation_concurrently(postgres_session_factory)
    grant = load_only_refund_grant()
    active = load_active_subscription()
    assert grant.status == "applied"
    assert grant.target_subscription_id == active.id
    plan = load_plan(active.plan_id)
    assert active.quota_credits_total == plan.quota_credits + 30000
```

Also test reject while source remains current (no grant), expired source with current target (immediate applied grant), expired source with no target (pending), later activation, repeated/concurrent application, plan change lock order, manual admin adjustment preserving holds/grants, active and inactive quota snapshots, and charging writes still returning `SUBSCRIPTION_NOT_FOUND` without an active subscription.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
uv run pytest tests/test_subscription_refund_grants.py tests/test_quota_concurrency.py tests/test_admin_console_api.py -q
```

Expected: FAIL because quota returns 404 without a subscription and subscription creation/grant application do not share a Tenant lock.

- [ ] **Step 3: Implement the subscription lifecycle lock and grant application**

Every activate/create subscription path runs through `run_db_transaction_with_retry()`, first locks the stable Tenant row, then re-queries active subscriptions. `activate_subscription()` creates or confirms the one active row, locks pending grants by primary key, adds their exact integer sum to `quota_credits_total`, and marks each applied with target/time in the same transaction. `create_default_subscription()` delegates to this function. Fault-injection tests cover two serialization failures followed by success and three failures followed by a surfaced error.

`change_plan()` must lock Tenant before Subscription and never copy manual order reservations. Add only these audited wallet mutation helpers to the AST allowlist in `test_quota_concurrency.py`; direct balance mutation remains forbidden elsewhere.

- [ ] **Step 4: Implement tenant quota snapshots**

```python
class QuotaResponse(BaseModel):
    has_active_subscription: bool
    active_subscription_id: str | None
    total: int
    used: int
    reserved: int
    remaining: int
    manual_fulfillment_held_credits: int
    pending_refund_credits: int
```

When active, return the existing wallet plus explanatory manual holds and pending grants. When inactive, current wallet fields are zero but manual holds are summed from expired/non-active source subscriptions linked to in-progress Doubao operations and pending refund credits from grants. These two fields are not added again to `total` or `reserved`.

- [ ] **Step 5: Run tests and commit**

```powershell
uv run pytest tests/test_subscription_refund_grants.py tests/test_quota_concurrency.py tests/test_admin_console_api.py -q
uv run ruff check app/services/subscription.py app/services/quota.py app/api/v1/routes/quota.py app/schemas/quota.py
git add backend/app/services/subscription.py backend/app/services/quota.py backend/app/api/v1/routes/quota.py backend/app/schemas/quota.py backend/app/services/admin_console.py backend/app/api/v1/routes/auth.py backend/tests/test_subscription_refund_grants.py backend/tests/test_quota_concurrency.py backend/tests/test_admin_console_api.py
git commit -m "feat(billing): preserve manual holds across subscriptions"
```

---

### Task 11: 实现豆包人工订单创建、管理员队列与 fulfill/reject

**Files:**
- Create: `backend/app/services/brand_voice_orders.py`
- Create: `backend/app/services/asset_retention.py`
- Create: `backend/app/api/v1/routes/brand_voice_orders.py`
- Modify: `backend/app/api/v1/routes/admin_console.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/services/admin_console.py`
- Modify: `backend/app/services/history.py`
- Modify: `backend/app/services/billing_operations.py`（注册人工订单 resource schema）
- Modify: `backend/app/api/v1/routes/brand_voices.py`（待续费目标删除保护）
- Create: `backend/tests/test_brand_voice_orders.py`
- Create: `backend/tests/test_brand_voice_order_concurrency.py`
- Modify: `backend/tests/test_admin_console_api.py`
- Modify: `backend/tests/test_subscription_refund_grants.py`
- Modify: `backend/tests/test_brand_voice_pipeline.py`

**Interfaces:**
- Consumes: quote verification, operation reservation, registry, Tenant/subscription helpers, models from Task 8.
- Produces: `create_brand_voice_order(db: Session, *, user: User, payload: BrandVoiceOrderCreateRequest, verified_quote: VerifiedQuote, submission_headers: BillingSubmissionHeaders, now: datetime | None = None) -> BrandVoiceOrder`.
- Produces: `list_user_brand_voice_orders(db: Session, *, tenant_id: str, user_id: str) -> list[BrandVoiceOrder]`.
- Produces: `get_user_brand_voice_order(db: Session, *, tenant_id: str, user_id: str, order_id: str) -> BrandVoiceOrder`.
- Produces: `list_admin_brand_voice_orders(db: Session, *, status: str | None, page: int, page_size: int) -> Page[BrandVoiceOrder]`.
- Produces: `resolve_brand_voice_order(db: Session, *, actor: User, order_id: str, action: Literal["fulfill", "reject"], provider_voice_id: str | None = None, rejection_reason: str | None = None, now: datetime | None = None) -> BrandVoiceOrder`.
- Produces: `assert_asset_not_held_by_manual_order(db: Session, *, asset_id: str) -> None`.
- Produces: `assert_brand_voice_not_held_by_manual_order(db: Session, *, brand_voice_id: str) -> None`.
- Produces: `assert_no_awaiting_orders_for_principal(db: Session, *, tenant_id: str, user_id: str | None = None) -> None`.
- Produces registration: `brand_voice_order -> BrandVoiceOrderRead`; lookup reloads the payment-user-scoped order and composes current billing/refund fields.
- Produces order refund fields: `refund_disposition: not_applicable | source_subscription_released | current_subscription_credited | pending_next_subscription`, `refund_grant_status: pending | applied | null`, and `refund_applied_at: datetime | null`.

- [ ] **Step 1: Write failing customer-order tests**

```python
def test_doubao_order_only_freezes_and_never_calls_provider(client, auth_headers, provider_mocks):
    quote = estimate_doubao_order(client, auth_headers, create_order_payload())
    response = submit_doubao_order(client, auth_headers, quote, create_order_payload())
    assert response.status_code == 201
    order = response.json()["data"]
    assert order["status"] == "awaiting_fulfillment"
    assert order["billing"]["status"] == "reserved"
    assert order["billing"]["held_credits"] == 30000
    assert count_brand_voices_for_order(order["id"]) == 0
    provider_mocks.assert_no_clone_allocate_or_delete_calls()


def test_order_list_is_payment_user_scoped(client, user_a_headers, user_b_headers):
    order = create_order_as(user_a_headers)
    response = client.get("/api/v1/brand-voice-orders", headers=user_b_headers)
    assert order["id"] not in {item["id"] for item in response.json()["data"]["items"]}
```

Cover fixed 30000 despite tenant rate 42, create/renew request validation, renewed voice ownership/provider/expired/deleted checks under row lock, new audio and consent for renewal, no quote/insufficient balance/duplicate key, awaiting order not cancellable, source asset lock/delete race, pending renewal uniqueness, registry-not-ready fail closed, operation `result_type=brand_voice_order`/`result_id=order.id`, and PostgreSQL races between order creation and tenant/user deactivation. Whichever transaction wins, the final state must never contain an awaiting order owned by an inactive tenant/user.

- [ ] **Step 2: Write failing resolve and concurrency tests**

```python
def test_fulfill_binds_payment_user_settles_and_starts_365_days(
    client, platform_admin_headers, awaiting_order, frozen_time
):
    response = resolve_order(
        client,
        platform_admin_headers,
        awaiting_order.id,
        {"action": "fulfill", "provider_voice_id": "customer-voice-001"},
    )
    assert response.status_code == 200
    resource = response.json()["data"]
    assert resource["status"] == "fulfilled"
    assert resource["billing"]["status"] == "settled"
    voice = load_brand_voice(resource["fulfilled_brand_voice_id"])
    assert voice.owner_user_id == awaiting_order.ordered_by_user_id
    assert voice.activated_at == frozen_time
    assert voice.expires_at == frozen_time + timedelta(days=365)


def test_conflicting_resolve_changes_nothing(client, platform_admin_headers, fulfilled_order):
    before = financial_snapshot(fulfilled_order.id)
    response = resolve_order(
        client,
        platform_admin_headers,
        fulfilled_order.id,
        {"action": "reject", "rejection_reason": "不同动作"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BRAND_VOICE_ORDER_ALREADY_RESOLVED"
    assert financial_snapshot(fulfilled_order.id) == before
```

Also test reject with active/expired/no current subscription, authoritative refund disposition and pending→applied re-query, same-action/same-content idempotency, different ID/reason conflict, fulfill-vs-reject concurrency, exact global lock order, each independent operation/usage/order/subscription/snapshot invariant violation, expected wallet reconciliation with decimal batch and legacy rows, official/unknown/reused IDs, own-ID renewal, provider ID swap locking, readiness failure after order creation, audit entries, short-lived audio URL only on detail, deletion of a renewal target while awaiting, and transaction failure leaving the order awaiting. Every invariant/readiness failure keeps order awaiting, hold unchanged, and registry untouched.

- [ ] **Step 3: Run focused tests and verify failure**

```powershell
uv run pytest tests/test_brand_voice_orders.py tests/test_brand_voice_order_concurrency.py tests/test_admin_console_api.py tests/test_subscription_refund_grants.py -q
```

Expected: FAIL because order services/routes and strict resolve do not exist.

- [ ] **Step 4: Implement estimate/create and user-scoped reads**

`POST /brand-voice-orders/estimate` validates product/VIP gate, current user, source Asset, current consent, and for renew the expired Doubao voice owned by that user without writes. It returns the platform-fixed 30000 quote. Submit performs replay lookup first, then runs a retryable transaction that locks Tenant → active Subscription → User/source Asset/renewal BrandVoice, rechecks `tenant.status=active`, `user.is_active=true`, `user.status=active`, asserts registry readiness, and revalidates all materials. It creates operation, original-subscription usage reservation and order in that transaction; the allocation uses `provider="doubao-voice-clone"`, `model="manual_fulfillment"`, `video_task_id=None`. It does not create a BrandVoice or call any provider/slot function.

User list/detail predicates always contain both `tenant_id` and `ordered_by_user_id`; invisible records return 404. Awaiting orders expose no cancel/delete mutation. Register `BrandVoiceOrderRead` for `result_type=brand_voice_order`; lookup resolves `result_id` through the same visibility predicate rather than returning stored raw JSON.

- [ ] **Step 5: Implement deterministic reconciliation and resolve**

Before row locks, locate only the required IDs. Run the entire no-supplier resolve transaction through `run_db_transaction_with_retry()` and use the global order from Global Constraints. After an exact terminal replay check, and before any first-time write, require: order is awaiting; operation is in-progress with null completion; exactly one order usage is still reserved; operation, usage, order and source subscription IDs agree; snapshot contains the one platform-fixed Doubao line and `payable_credits=30000`; operation requested and source hold are exactly 30000. At the provider-lock stage rerun `assert_doubao_registry_ready()` for both fulfill and reject, so configuration drift cannot permit a new terminal transition. Calculate each subscription's expected reserved amount as: distinct requested credits for in-progress linked operations with reserved usages, plus `ceil(credits)` for every legacy `billing_operation_id IS NULL AND status='reserved'` row. Do not sum/ceil linked child rows. Any mismatch logs a high-priority invariant error and leaves the order, hold and registry untouched.

Fulfill claims/reuses the provider ID, creates or renews the payment user's ready voice, writes `fulfilled_at=activated_at=transaction_now`, `expires_at=transaction_now+365 days`, settles the original subscription usage and operation, and writes audit in one transaction. Reject releases the original hold, creates/applies the unique refund grant when cross-period, sets the server-authoritative refund disposition/grant fields, marks operation rejected, and writes audit in one transaction. A later quota activation changes a pending grant to applied; subsequent order and operation lookups must expose the updated grant status/time rather than infer it client-side.

- [ ] **Step 6: Add protected admin queue and audio detail**

Add the three spec endpoints under `/api/v1/admin/console/brand-voice-orders`. List returns metadata only. Detail signs a short-lived URL with `presign_tenant_storage_key()` and commits `brand_voice_order_audio_access` audit before responding. Resolve accepts this strict union:

```python
class BrandVoiceOrderFulfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["fulfill"]
    provider_voice_id: str = Field(min_length=1)


class BrandVoiceOrderRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["reject"]
    rejection_reason: str = Field(min_length=1)


BrandVoiceOrderResolveRequest = Annotated[
    BrandVoiceOrderFulfillRequest | BrandVoiceOrderRejectRequest,
    Field(discriminator="action"),
]
```

Test both mixed-field payloads as 422. Use `asset_retention.assert_asset_not_held_by_manual_order()` from storage cleanup/history paths after locking the Asset, and `assert_brand_voice_not_held_by_manual_order()` in `brand_voices.py` before deleting a voice that is the target of an awaiting renewal. Guard `change_tenant_status()` with the shared Tenant lock and `assert_no_awaiting_orders_for_principal()` before disabling a tenant. The project currently has no user-deactivation write endpoint; keep the same guard public for the first such endpoint and add a static test that no current production path writes `User.is_active/status` outside approved account services.

- [ ] **Step 7: Run tests and commit**

```powershell
uv run pytest tests/test_brand_voice_orders.py tests/test_brand_voice_order_concurrency.py tests/test_admin_console_api.py tests/test_subscription_refund_grants.py -q
uv run ruff check app/services/brand_voice_orders.py app/services/asset_retention.py app/api/v1/routes/brand_voice_orders.py app/api/v1/routes/admin_console.py app/api/v1/routes/brand_voices.py
git add backend/app/services/brand_voice_orders.py backend/app/services/asset_retention.py backend/app/services/billing_operations.py backend/app/api/v1/routes/brand_voice_orders.py backend/app/api/v1/routes/admin_console.py backend/app/api/v1/routes/brand_voices.py backend/app/api/v1/router.py backend/app/services/admin_console.py backend/app/services/history.py backend/tests/test_brand_voice_orders.py backend/tests/test_brand_voice_order_concurrency.py backend/tests/test_admin_console_api.py backend/tests/test_subscription_refund_grants.py backend/tests/test_brand_voice_pipeline.py
git commit -m "feat(voice): add manual Doubao delivery lifecycle"
```

---

### Task 12: 隔离用户豆包权益并将 CosyVoice 创建接入零价 operation

**Files:**
- Modify: `backend/app/api/v1/routes/brand_voices.py`（create 当前约 70–192；delete 当前约 247–308；旧 slot helpers 当前约 311–445）
- Modify: `backend/app/schemas/brand_voices.py`
- Modify: `backend/app/api/v1/routes/voices.py`
- Modify: `backend/app/services/voices.py`
- Modify: `backend/app/services/batches.py`
- Modify: `backend/app/api/v1/routes/batches.py`
- Modify: `backend/app/workers/avatar_talk.py`
- Modify: `backend/app/services/plan_access.py`
- Modify: `backend/tests/test_brand_voice_pipeline.py`
- Modify: `backend/tests/test_batch_prod_pipeline.py`
- Modify: `backend/tests/test_avatar_talk_worker.py`
- Modify: `backend/tests/test_video_pipeline_quota.py`

**Interfaces:**
- Consumes: owner/time fields, registry, quote/operation lifecycle.
- Produces: `is_official_doubao_voice_for_user(db: Session, *, user: User, brand_voice: BrandVoice) -> bool`.
- Produces: `assert_brand_voice_usable(db: Session, brand_voice: BrandVoice, *, user: User, requested_at: datetime) -> None`.
- Produces: `resolve_narration_voice(db: Session, *, user: User, voice_id: str | None, requested_at: datetime | None = None) -> tuple[Voice | None, BrandVoice | None]`.
- Produces separate `order_status: awaiting_fulfillment | fulfilled | rejected` and derived customer `delivery_status: awaiting_fulfillment | active | expired | rejected`, without changing provider status `processing | ready | failed`.

- [ ] **Step 1: Write failing access, deletion, and CosyVoice tests**

```python
def test_paid_doubao_voice_is_invisible_to_other_tenant_user(client, payer_headers, colleague_headers):
    voice = fulfilled_doubao_voice(owner_headers=payer_headers)
    listing = client.get("/api/v1/voices", headers=colleague_headers)
    assert voice.id not in {item["id"] for item in listing.json()["data"]["items"]}
    submit = submit_video_with_voice(colleague_headers, voice.id)
    assert submit.status_code == 404


def test_deleting_delivered_doubao_is_local_only(client, payer_headers, delivered_voice, provider_mocks):
    before = wallet_snapshot()
    response = client.delete(f"/api/v1/brand-voices/{delivered_voice.id}", headers=payer_headers)
    assert response.status_code == 204
    assert wallet_snapshot() == before
    assert provider_registry_row(delivered_voice.speaker_id).status in {"active", "retired"}
    provider_mocks.assert_no_delete_or_slot_release_calls()


def test_cosyvoice_create_is_free_success_not_release(client, auth_headers, cosy_provider_mock):
    quote = estimate_cosyvoice_create(client, auth_headers, cosy_create_payload())
    response = submit_cosyvoice_create(client, auth_headers, quote, cosy_create_payload())
    billing = response.json()["data"]["billing"]
    assert billing["status"] == "settled"
    assert billing["requested_credits"] == 0
    assert billing["released_credits"] == 0
```

Also test owner can list/detail/rename/select before expiry, expiry blocks only new submissions, a task accepted before expiry continues, historic unknown Doubao with null owner/time is excluded from customer APIs, confirmed official IDs remain listable/selectable/submittable/worker-usable only from the platform tenant, customer tenants cannot see them, official records receive zero owner/date backfill and zero year fee, CosyVoice remains tenant-shared, old `POST /brand-voices` Doubao/default provider returns `DOUBAO_MANUAL_ORDER_REQUIRED` with zero side effects, CosyVoice disclosure persists at its signed reference rate, remote creation is idempotent, and CosyVoice TTS charges only the parent video once.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
uv run pytest tests/test_brand_voice_pipeline.py tests/test_batch_prod_pipeline.py tests/test_avatar_talk_worker.py tests/test_video_pipeline_quota.py -q
```

Expected: FAIL because brand voices are tenant-shared, Doubao auto-clones/releases slots, and CosyVoice creation bypasses the common zero-price quote flow.

- [ ] **Step 3: Centralize customer visibility and usability**

For new manual Doubao, require tenant, exact `owner_user_id`, ready, not deleted, and `requested_at < expires_at`. For CosyVoice/non-Doubao history preserve tenant-level behavior. For null-rights Doubao, check the exact provider ID against the configured and registered official list: it remains usable only when `user.tenant_id` is the platform tenant and equals the BrandVoice tenant; every customer tenant sees 404. A null-rights ID outside the official list is unknown and always fail closed. This predicate is shared by list, picker, video, batch and worker and does not create rights fields or billing rows.

Pass `current_user.id` and server request time through brand voice list/detail/update/delete, voice picker, videos, batches and workers. Worker revalidation uses `VideoTask.created_by_user_id` and the task submission timestamp rather than client params or current clock, so an already accepted task is not invalidated by crossing expiry in queue.

- [ ] **Step 4: Replace old Doubao create/delete behavior**

`POST /brand-voices` accepts only explicit CosyVoice. Omitted provider or any Doubao alias returns 422 `DOUBAO_MANUAL_ORDER_REQUIRED` before creating a row, charging, allocating a slot or resolving a provider. Delete of an owner-bound Doubao voice marks `deleted_at`, does not refund, does not call remote delete, and never makes its registry ID reusable. Remove `_allocate_voice_clone_speaker_id()` and `_release_voice_clone_speaker_id()` from the customer route.

- [ ] **Step 5: Put CosyVoice creation through signed zero-price billing**

Add `POST /brand-voices/estimate` for CosyVoice only. Its fixed-policy line is zero and its signed disclosure contains disclosure key, rendered Chinese copy, copy version, current `tts/character` unit/scope/source/provenance and reference rate. Submit uses operation `cosyvoice_brand_voice_create`, creates a zero usage line 0, commits before the remote clone call, then settles zero on valid ready result or completes failed with released zero on error. Provider cost attaches to that usage's `provider_usage`; it does not become a second user fee.

- [ ] **Step 6: Run tests and commit**

```powershell
uv run pytest tests/test_brand_voice_pipeline.py tests/test_batch_prod_pipeline.py tests/test_avatar_talk_worker.py tests/test_video_pipeline_quota.py -q
uv run ruff check app/api/v1/routes/brand_voices.py app/api/v1/routes/voices.py app/services/voices.py app/services/batches.py app/api/v1/routes/batches.py
git add backend/app/api/v1/routes/brand_voices.py backend/app/schemas/brand_voices.py backend/app/api/v1/routes/voices.py backend/app/services/voices.py backend/app/services/batches.py backend/app/api/v1/routes/batches.py backend/app/workers/avatar_talk.py backend/app/services/plan_access.py backend/tests/test_brand_voice_pipeline.py backend/tests/test_batch_prod_pipeline.py backend/tests/test_avatar_talk_worker.py backend/tests/test_video_pipeline_quota.py
git commit -m "feat(voice): enforce paid ownership and free CosyVoice creation"
```

---

### Task 13: 增加 stale recovery、readiness、只读存量清单与网关头限制

**Files:**
- Modify: `backend/app/services/task_recovery.py`
- Modify: `backend/app/api/v1/routes/health.py`
- Create: `backend/scripts/ops/pricing_closure_readiness.py`
- Modify: `infra/nginx/conf.d/huadingai.conf`
- Modify: `backend/tests/test_task_recovery.py`
- Create: `backend/tests/test_pricing_readiness.py`
- Modify: `backend/tests/test_deploy_prod_infra.py`
- Modify: `backend/tests/test_brand_voice_provider_registry.py`

**Interfaces:**
- Consumes: billing operation invariants, provider inventory, rate validator.
- Produces: `recover_stale_billing_operations(db: Session, *, now: datetime) -> RecoverySummary`.
- Produces: `pricing_closure_readiness(db: Session, *, production_mode: bool) -> PricingReadinessReport`.
- Produces CLI modes: default `audit` (read-only JSON report) and explicit `register-official` (requires the configured exact official list and a transaction); no mode writes rates or user rights.

- [ ] **Step 1: Write failing recovery/readiness/infra tests**

```python
def test_recovery_never_releases_waiting_manual_order(db_session, stale_manual_order):
    before = financial_snapshot(stale_manual_order.id)
    summary = recover_stale_billing_operations(
        db_session,
        now=stale_manual_order.created_at + timedelta(days=400),
    )
    assert stale_manual_order.id in summary.exempt_manual_order_ids
    assert financial_snapshot(stale_manual_order.id) == before


def test_readiness_fails_on_unknown_historic_doubao_id(db_session):
    add_historic_doubao_voice("unknown-id")
    report = pricing_closure_readiness(db_session, production_mode=True)
    assert report.ready is False
    assert report.blockers[0].code == "UNKNOWN_HISTORIC_DOUBAO_ID"
    assert report.blockers[0].record_ids


def test_nginx_accepts_4096_byte_quote_header():
    config = load_prod_nginx_config()
    assert nginx_single_header_capacity(config) >= 16 * 1024
```

Also test queued operation whose enqueue failed, running stale success/failed/timeout/not-yet-stale cases, idempotent repeated recovery, schema-invalid result release, rate duplicates/zero/negative/non-finite blockers with record IDs, fallback mismatch, official config/registry mismatch, inactive ProviderConfig and soft-deleted voice inventory, old slot write surface detection, no mutation in audit mode, and manual orders surviving every cutoff.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
uv run pytest tests/test_task_recovery.py tests/test_pricing_readiness.py tests/test_deploy_prod_infra.py tests/test_brand_voice_provider_registry.py -q
```

Expected: FAIL because recovery is not operation-aware, there is no pricing readiness report, and the header capacity is not asserted.

- [ ] **Step 3: Implement operation-aware recovery**

Scan only automatic in-progress operations associated with each business type's authoritative timeout. For each candidate, lock in the global order and re-read state: a persisted deliverable settles once; an explicit failed/cancelled task releases; a timed-out task without a deliverable releases; a still-valid task remains held. Include queued tasks whose transaction committed but enqueue failed. Explicitly filter both Doubao order operations and verify that an awaiting manual order cannot enter a generic release helper.

- [ ] **Step 4: Implement read-only readiness and official registration separation**

The default command prints machine-readable JSON containing migration revision, platform rates, every tenant override, invalid/duplicate IDs, wallet/operation invariant failures, official/customer/unknown provider inventory and legacy write-surface status. It opens no write transaction. `register-official` accepts only `settings.engine_doubao_official_voice_ids`, calls the registry function, writes no BrandVoice rights or billing rows, and aborts on any unknown inventory.

Health readiness calls the same validators in production mode and returns not-ready without repairing data. It must not expose provider secrets or user source URLs.

- [ ] **Step 5: Set and test the Nginx quote-header capacity**

Configure a per-request header capacity of at least 16 KiB using the repository's existing Nginx style (`large_client_header_buffers` or equivalent). Keep the application-level hard maximum at 4096 ASCII bytes; the larger gateway buffer prevents valid tokens from being rejected before FastAPI performs the stricter check.

- [ ] **Step 6: Run tests and commit**

```powershell
uv run pytest tests/test_task_recovery.py tests/test_pricing_readiness.py tests/test_deploy_prod_infra.py tests/test_brand_voice_provider_registry.py -q
uv run ruff check app/services/task_recovery.py app/api/v1/routes/health.py scripts/ops/pricing_closure_readiness.py tests/test_pricing_readiness.py
git add backend/app/services/task_recovery.py backend/app/api/v1/routes/health.py backend/scripts/ops/pricing_closure_readiness.py infra/nginx/conf.d/huadingai.conf backend/tests/test_task_recovery.py backend/tests/test_pricing_readiness.py backend/tests/test_deploy_prod_infra.py backend/tests/test_brand_voice_provider_registry.py
git commit -m "feat(ops): add pricing recovery and readiness gates"
```

---

### Task 14: 建立前端严格计费类型、确认状态机与断线查询

**Files:**
- Create: `frontend/src/lib/api/billing.ts`
- Create: `frontend/src/lib/api/billing.test.ts`
- Create: `frontend/src/lib/billing/use-billing-action.ts`
- Create: `frontend/src/lib/billing/use-billing-action.test.tsx`
- Create: `frontend/src/components/billing/pricing-confirm-dialog.tsx`
- Create: `frontend/src/components/billing/pricing-confirm-dialog.test.tsx`
- Create: `frontend/src/components/billing/billing-status.tsx`
- Create: `frontend/src/components/billing/billing-status.test.tsx`
- Modify: `frontend/src/lib/api/client.ts`
- Modify: `frontend/src/lib/api/types.ts`

**Interfaces:**
- Produces: `billingHeaders(confirmation: BillingConfirmation): Record<string, string>`.
- Produces: `getBillingOperation(operation: string, idempotencyKey: string): Promise<BillingOperationLookup>`.
- Produces: `parseBillingSummary(value: unknown): BillingSummary | null`.
- Produces: `billingFromApiError(error: unknown): BillingSummary | null`.
- Produces hook: `useBillingAction<TInput, TQuote, TResult>(options: UseBillingActionOptions<TInput, TQuote, TResult>): UseBillingActionResult<TInput, TQuote, TResult>`.

- [ ] **Step 1: Write failing runtime-parser and union tests**

```ts
it("rejects a terminal summary whose money does not conserve", () => {
  expect(
    parseBillingSummary({
      operation_id: "op-1",
      idempotency_key: "11111111-1111-4111-8111-111111111111",
      status: "settled",
      requested_credits: 30,
      held_credits: 0,
      settled_credits: 29,
      released_credits: 0,
    }),
  ).toBeNull();
});

it("reads billing from ApiError.detail but not from an envelope root", () => {
  const error = new ApiError("supplier_failed", "失败", 502, {
    billing: releasedBillingSummary(30),
  });
  expect(billingFromApiError(error)?.status).toBe("released");
});
```

Cover malformed decimal strings, simple/composite top-level field exclusivity, pricing line/disclosure provenance, four summary states and conservation, zero-price settled CosyVoice, lookup union resource/result/failure exclusivity, unknown state/type rejection, and exact header names.

- [ ] **Step 2: Define exact wire types without client-side money math**

```ts
export interface BillingConfirmation {
  quote_token: string;
  idempotency_key: string;
}

export interface BillingSummary {
  operation_id: string;
  idempotency_key: string;
  status: "reserved" | "settled" | "partially_settled" | "released";
  requested_credits: number;
  held_credits: number;
  settled_credits: number;
  released_credits: number;
}

export interface BillingPricingLine {
  operation: string;
  capability: string;
  unit: string;
  quantity: string;
  unit_credits: string;
  subtotal_credits: string;
  rate_scope: "tenant_overridable" | "platform_fixed";
  rate_source: "tenant_rate" | "platform_rate" | "code_default" | "fixed_policy";
  rate_id: string | null;
  effective_at: string | null;
  policy_key: string | null;
  policy_version: number | null;
  label: string;
}

export interface BillingDisclosure {
  key: string;
  rendered_text: string;
  copy_version: number;
  unit: string;
  rate_scope: BillingPricingLine["rate_scope"];
  rate_source: BillingPricingLine["rate_source"];
  rate_id: string | null;
  effective_at: string | null;
  policy_key: string | null;
  policy_version: number | null;
  reference_unit_credits: string;
}

interface BillingQuoteBase {
  pricing_contract: "billing_quote";
  operation: string;
  subtotal_credits: string;
  payable_credits: number;
  disclosures: BillingDisclosure[];
  quote_token: string;
  expires_at: string;
}

export type BillingQuote =
  | (BillingQuoteBase & {
      pricing_shape: "simple";
      unit: string;
      quantity: string;
      unit_credits: string;
      rate_scope: BillingPricingLine["rate_scope"];
      rate_source: BillingPricingLine["rate_source"];
      breakdown: [];
    })
  | (BillingQuoteBase & {
      pricing_shape: "composite";
      unit: null;
      quantity: null;
      unit_credits: null;
      rate_scope: null;
      rate_source: null;
      breakdown: [BillingPricingLine, ...BillingPricingLine[]];
    });
```

Decimal strings and server `payable_credits` are display-only inputs; do not calculate `0.1 × characters` in JavaScript. Define the lookup union with `state/completion_kind` discrimination and schema-validated typed resource/result/failure fields.

- [ ] **Step 3: Write failing hook/dialog tests**

```tsx
it("keeps confirmation disabled when estimate fails", async () => {
  const submit = vi.fn();
  renderBillingDialog({ estimate: vi.fn().mockRejectedValue(new Error("offline")), submit });
  await screen.findByText("暂时无法获取价格，请稍后重试");
  expect(screen.getByRole("button", { name: "确认并继续" })).toBeDisabled();
  expect(submit).not.toHaveBeenCalled();
});

it("queries the original operation after an unknown network result", async () => {
  const lookup = vi.fn().mockResolvedValue(completedLookup(settledBillingSummary(30)));
  const view = renderBillingAction({ submit: rejectWithNetworkError(), lookup });
  await view.confirm();
  expect(lookup).toHaveBeenCalledWith(view.operation, view.idempotencyKey);
  expect(view.current.billing?.status).toBe("settled");
});
```

Also test loading, successful estimate, expiry countdown/re-estimate, input hash change clearing quote, submit-time stale-input guard, double-click, visible failed lookup despite HTTP 200, three 404 lookups followed by one safe same-key replay, expired quote plus persistent 404 requiring a new confirmation, terminal new attempt generating a new key/quote, released wording, free-CosyVoice wording, and unknown wording `计费结果确认中`.

- [ ] **Step 4: Implement the reusable state machine and components**

`useBillingAction` owns a stable UUID for one confirmed attempt, quote expiry, normalized input fingerprint, submit lock and lookup polling. Any bound input change clears quote and key before the next estimate. After a network/gateway/unparseable POST result, poll lookup at one-second intervals for three attempts. If all three are 404, the operation does not yet exist: when the quote is still valid and the input fingerprint is unchanged, replay the original POST exactly once with the same request, key and token, then resume bounded lookup; when the quote expired, clear it and require the user to estimate/confirm again. A second unknown result remains `计费结果确认中` with an explicit “继续查询” action; it never silently creates a new key. A known terminal failure starts a new key only after explicit user retry; `PRICE_CHANGED` clears quote and requires a new estimate.

`PricingConfirmDialog` displays server unit, quantity, lines, subtotal and highlighted payable; it never has an “按实际结算” escape. `BillingStatus` maps only the parsed server summary to `reserved/settled/partially_settled/released` copy and distinguishes legal zero-price success from released failure.

- [ ] **Step 5: Run tests and commit**

```powershell
pnpm --filter @huading/frontend test -- src/lib/api/billing.test.ts src/lib/billing/use-billing-action.test.tsx src/components/billing/pricing-confirm-dialog.test.tsx src/components/billing/billing-status.test.tsx
pnpm --filter @huading/frontend typecheck
git add frontend/src/lib/api/billing.ts frontend/src/lib/api/billing.test.ts frontend/src/lib/billing/use-billing-action.ts frontend/src/lib/billing/use-billing-action.test.tsx frontend/src/components/billing/pricing-confirm-dialog.tsx frontend/src/components/billing/pricing-confirm-dialog.test.tsx frontend/src/components/billing/billing-status.tsx frontend/src/components/billing/billing-status.test.tsx frontend/src/lib/api/client.ts frontend/src/lib/api/types.ts
git commit -m "feat(frontend): add authoritative billing confirmation flow"
```

---

### Task 15: 将文案、提示词、电商图片与视频 UI 接入服务端报价

**Files:**
- Modify: `frontend/src/lib/api/scripts.ts`
- Modify: `frontend/src/lib/api/videos.ts`
- Modify: `frontend/src/lib/api/ecom-images.ts`
- Modify: `frontend/src/lib/api/hooks.ts`
- Modify: `frontend/src/lib/api/use-generate-confirm.ts`
- Modify: `frontend/src/components/workbench/confirm-generate-dialog.tsx`
- Modify: `frontend/src/components/workbench/new-video-form.tsx`
- Modify: `frontend/src/components/workbench/ecom-video-form.tsx`
- Modify: `frontend/src/components/workbench/ecom-image-tool.tsx`
- Modify: `frontend/src/lib/videos/tasks-context.tsx`
- Modify: `frontend/src/mocks/handlers.ts`
- Create: `frontend/src/components/workbench/script-pricing.test.tsx`
- Create: `frontend/src/components/workbench/scene-prompt-pricing.test.tsx`
- Create: `frontend/src/components/workbench/ecom-image-pricing.test.tsx`
- Create: `frontend/src/components/workbench/video-pricing-contract.test.tsx`
- Modify: `frontend/src/lib/api/scripts.test.ts`
- Modify: `frontend/src/lib/api/videos.test.ts`
- Modify: `frontend/src/lib/api/hooks.test.tsx`
- Modify: `frontend/src/lib/videos/tasks-context.test.tsx`

**Interfaces:**
- Consumes: `BillingQuote`, `BillingConfirmation`, `useBillingAction()`.
- Produces API functions `estimateScript`, `generateScript`, `estimateScenePrompt`, `generateScenePrompt`, `estimateEcomCutout`, `createEcomCutout`, `estimateEcomModel`, `createEcomModel` with confirmation on submit.
- Produces strict `VideoEstimateContract` and `VideoAcceptedContract` discriminated by `pricing_contract`.

- [ ] **Step 1: Write failing API and form tests**

```tsx
it("quotes and confirms the 1-credit script operation before generating", async () => {
  server.use(scriptEstimateHandler({ payable_credits: 1 }), scriptSubmitHandler());
  render(<NewVideoForm />);
  await user.click(screen.getByRole("button", { name: "AI 生成文案" }));
  expect(await screen.findByText("本次应付 1 积分")).toBeVisible();
  expect(scriptSubmitCalls()).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "确认并继续" }));
  expect(scriptSubmitHeaders()).toMatchObject({
    "Idempotency-Key": expect.any(String),
    "X-Huading-Quote": expect.any(String),
  });
});

it("rejects an ecom batch of 21 before estimate and never truncates", async () => {
  renderEcomImageToolWithItems(21);
  expect(screen.getByText("每批最多 20 张图片")).toBeVisible();
  expect(ecomEstimateCalls()).toHaveLength(0);
});
```

Cover scene price 30 from server, ecom `80 × N` server total, partial settlement copy, all estimate failures disabling submit, bound input changes clearing quote, no authority constants, MSW 21-item 422, different 20th item hash behavior, network lookup recovery, and no double submit.

- [ ] **Step 2: Replace API signatures and video wire union**

```ts
export type VideoEstimateContract =
  | BillingQuote
  | {
      pricing_contract: "legacy_estimate";
      estimated_credits: number;
      unit: "credits";
      note?: string | null;
      billing?: never;
    }
  | {
      pricing_contract: "deferred_unpriced";
      estimated_credits: 0;
      unpriced: true;
      reason_code: string;
      note: string;
      billing?: never;
    };

export type VideoAcceptedContract =
  | { pricing_contract: "billing_quote"; id: string; status: string; billing: BillingSummary }
  | { pricing_contract: "legacy_estimate"; id: string; status: string; billing?: never }
  | { pricing_contract: "deferred_unpriced"; id: string; status: string; billing?: never };
```

Each new submit function takes `BillingConfirmation` and attaches headers through `billingHeaders()`. Legacy/deferred video calls must not attach them.

- [ ] **Step 3: Integrate script/scene and ecom confirmation UI**

Wrap both script buttons in `new-video-form.tsx` and `ecom-video-form.tsx` with the shared state machine. The ecom tool submits the exact validated list shown in the quote, displays per-image unit/quantity/total and partial-success explanation, and refuses a 21st item rather than slicing.

- [ ] **Step 4: Integrate the three video branches and billable text requirement**

The confirmation container switches exhaustively on `pricing_contract`: branded billing quote shows the common dialog; legacy shows the existing estimate behavior; deferred shows the approved “延期处理／尚未闭环” copy without a fake free label. For CosyVoice, render the returned TTS line and actual server count/rate; if `BILLABLE_TEXT_REQUIRED`, focus the script editor and block submit. Doubao branded video has no CosyVoice character line.

- [ ] **Step 5: Make task retry query the original operation first**

Persist operation name, idempotency key and pricing contract alongside the original request in `frontend/src/lib/videos/tasks-context.tsx`. Unknown POST outcome triggers lookup polling; visible completed failure is still handled as failure despite GET 200. Only explicit “重新尝试” after a terminal failure obtains a fresh estimate/key; quote expiry never auto-reuses the old token.

- [ ] **Step 6: Update MSW to mirror authoritative contracts**

Implement estimates, headers, idempotent operation storage, `data.billing`, `error.detail.billing`, lookup unions, partial batches, rate-change/expiry fixtures and all three video contracts. Remove every `.slice(0, 20)` and every hardcoded product price used as client authority; fixture amounts are returned only from estimate handlers.

- [ ] **Step 7: Run tests and commit**

```powershell
pnpm --filter @huading/frontend test -- src/components/workbench/script-pricing.test.tsx src/components/workbench/scene-prompt-pricing.test.tsx src/components/workbench/ecom-image-pricing.test.tsx src/components/workbench/video-pricing-contract.test.tsx src/lib/api/scripts.test.ts src/lib/api/videos.test.ts src/lib/api/hooks.test.tsx src/lib/videos/tasks-context.test.tsx
pnpm --filter @huading/frontend typecheck
git add frontend/src/lib/api/scripts.ts frontend/src/lib/api/videos.ts frontend/src/lib/api/ecom-images.ts frontend/src/lib/api/hooks.ts frontend/src/lib/api/use-generate-confirm.ts frontend/src/components/workbench/confirm-generate-dialog.tsx frontend/src/components/workbench/new-video-form.tsx frontend/src/components/workbench/ecom-video-form.tsx frontend/src/components/workbench/ecom-image-tool.tsx frontend/src/lib/videos/tasks-context.tsx frontend/src/mocks/handlers.ts frontend/src/components/workbench/script-pricing.test.tsx frontend/src/components/workbench/scene-prompt-pricing.test.tsx frontend/src/components/workbench/ecom-image-pricing.test.tsx frontend/src/components/workbench/video-pricing-contract.test.tsx frontend/src/lib/api/scripts.test.ts frontend/src/lib/api/videos.test.ts frontend/src/lib/api/hooks.test.tsx frontend/src/lib/videos/tasks-context.test.tsx
git commit -m "feat(frontend): require quotes for priced generation actions"
```

---

### Task 16: 实现豆包人工订单 UI、管理员交付、额度解释与权限隔离

**Files:**
- Create: `frontend/src/lib/api/brand-voice-orders.ts`
- Modify: `frontend/src/lib/api/brand-voices.ts`
- Modify: `frontend/src/lib/api/admin-console.ts`
- Modify: `frontend/src/lib/api/quota.ts`
- Modify: `frontend/src/lib/api/hooks.ts`
- Modify: `frontend/src/components/brand-voice/brand-voice-create.tsx`
- Modify: `frontend/src/components/brand-voice/brand-voice-list.tsx`
- Create: `frontend/src/components/brand-voice/brand-voice-order-list.tsx`
- Create: `frontend/src/components/brand-voice/brand-voice-order-list.test.tsx`
- Modify: `frontend/src/components/workbench/voice-picker.tsx`
- Modify: `frontend/src/components/layout/quota-badge.tsx`
- Create: `frontend/src/app/(admin)/admin/brand-voice-orders/page.tsx`
- Create: `frontend/src/app/(admin)/admin/brand-voice-orders/page.test.tsx`
- Modify: `frontend/src/app/(admin)/admin/voice-slots/page.tsx`
- Modify: `frontend/src/mocks/handlers.ts`
- Modify: `frontend/src/mocks/admin-console.test.ts`
- Modify: `frontend/src/components/brand-voice/brand-voice-create.test.tsx`
- Modify: `frontend/src/components/brand-voice/brand-voice-list.test.tsx`
- Modify: `frontend/src/components/workbench/voice-picker.test.tsx`
- Create: `frontend/src/lib/api/no-authoritative-prices.test.ts`

**Interfaces:**
- Consumes: billing confirmation/summary and backend order/admin/quota APIs.
- Produces: `estimateBrandVoiceOrder`, `createBrandVoiceOrder`, `listBrandVoiceOrders`, `getBrandVoiceOrder`.
- Produces: `listAdminBrandVoiceOrders`, `getAdminBrandVoiceOrder`, `resolveAdminBrandVoiceOrder`.
- Produces `BrandVoiceOrderStatus = "awaiting_fulfillment" | "fulfilled" | "rejected"`, `BrandVoiceDeliveryStatus = "awaiting_fulfillment" | "active" | "expired" | "rejected"`, and rights-aware `BrandVoiceRead`.

- [ ] **Step 1: Write failing customer flow and isolation tests**

```tsx
it("shows manual delivery instead of pretending Doubao is cloning", async () => {
  render(<BrandVoiceCreate />);
  await chooseDoubaoAndUploadAuthorizedAudio();
  await user.click(screen.getByRole("button", { name: "提交开通" }));
  expect(await screen.findByText("本次冻结 30000 积分")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "确认并提交人工开通" }));
  expect(await screen.findByText("已冻结 30000 积分，等待平台人工交付；订单不自动超时且无法取消")).toBeVisible();
  expect(screen.queryByText("供应商生成中")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "取消订单" })).not.toBeInTheDocument();
});


it("shows free CosyVoice creation as success rather than released failure", async () => {
  mockCosyCreateSettledAtZero();
  render(<BrandVoiceCreate />);
  await submitCosyVoice();
  expect(await screen.findByText("创建成功，本次创建免费（扣除 0 积分）")).toBeVisible();
  expect(screen.queryByText(/冻结已释放/)).not.toBeInTheDocument();
});
```

Cover fulfilled/rejected copy, accurate expiry, all four server refund dispositions, pending grant later becoming applied on re-query, new audio/consent renewal, no cancellation, deletion no refund promise, payer-only listing/picker, official/other-user exclusion, tenant-overridden CosyVoice disclosure, no `DOUBAO_CLONE_CREDITS`, and no quote = no submit.

- [ ] **Step 2: Write failing admin, quota, and retired-slot tests**

```tsx
it("resolves one order and disables both terminal actions", async () => {
  render(<AdminBrandVoiceOrdersPage />);
  await user.click(await screen.findByRole("button", { name: "查看订单" }));
  await user.type(screen.getByLabelText("豆包音色 ID"), "customer-voice-001");
  await user.click(screen.getByRole("button", { name: "确认交付" }));
  expect(await screen.findByText("已交付并结算 30000 积分")).toBeVisible();
  expect(screen.getByRole("button", { name: "确认交付" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "拒绝订单" })).toBeDisabled();
});


it("separates manual holds from ordinary reserved credits", async () => {
  mockQuota({ reserved: 12, manual_fulfillment_held_credits: 30000, pending_refund_credits: 0 });
  render(<QuotaBadge />);
  expect(await screen.findByText("运行任务冻结 12")).toBeVisible();
  expect(screen.getByText("人工交付冻结 30000")).toBeVisible();
});
```

Also test platform non-admin 403 handling, queue list without audio URL, detail URL, fulfill/reject validation, same-content replay, conflicting 409, slot page no write button, slot POST mock 410, no-active-subscription zero wallet plus cross-period fields, and error states that never claim “未扣费” without a released summary.

Add an AST-backed source test using the installed TypeScript compiler API. Scan the production files modified in Tasks 14–16 (exclude `*.test.*` and `src/mocks/**`), reject price-like constant declarations whose initializer is `1`, `30`, `80`, `30000` or `0.1`, reject identifiers such as `DOUBAO_CLONE_CREDITS`/`SCRIPT_GENERATE_CREDITS`/`SCENE_PROMPT_CREDITS`/`ECOM_IMAGE_CREDITS`/`COSYVOICE_CHARACTER_RATE`, and reject literal Chinese price strings in production components. Values inside estimate/mock/test payloads remain allowed only outside this production scan.

- [ ] **Step 3: Implement customer order API and replace automatic Doubao UI**

The Doubao branch uploads current audio and consent, gets the fixed quote, confirms freeze/manual terms, creates an order and renders the order list. It never polls `BrandVoice.status=processing`. Renewal is available only from an expired owner voice and repeats audio/consent/quote. Remove all authoritative price constants; every displayed amount comes from quote or returned billing/order data.

CosyVoice runs through its zero-price quote and renders `quote.disclosures[].rendered_text` verbatim. Add a tenant override fixture `tts/character=0.2` and assert the UI shows the signed `0.2 积分/字` reference rather than the platform default `0.1`; only the standalone workbench may describe the platform default.

- [ ] **Step 4: Implement payer-only voice and order states**

Render only backend-authorized voice records. Awaiting shows the exact frozen/manual/non-cancellable copy. Fulfilled shows settled amount and ISO date formatted in the user's timezone. Rejected copy is selected only from the order's server-authoritative `refund_disposition/refund_grant_status/refund_applied_at`: source/current credited states say available now, pending says next activation, and a later applied response updates the copy. Never infer refund destination from current subscription or `BillingSummary` alone. Deleted delivered voices do not promise refund. Picker excludes expired/deleted/unowned records.

- [ ] **Step 5: Implement minimum admin queue and retire slot UI**

Create a protected queue page with filters, metadata list, on-demand signed audio detail, provider ID input, reject-reason input and mutually exclusive action payloads. After resolve, use returned idempotent resource and disable actions. Convert the old voice-slot page to a read-only inventory/conflict view and remove assignment controls; MSW POST returns HTTP 410 `VOICE_SLOT_ASSIGNMENT_RETIRED`.

- [ ] **Step 6: Implement quota explanation and mocks**

Update `QuotaResponse` exactly to the backend schema. Without active subscription, render zero current wallet rather than a generic error. Show `manual_fulfillment_held_credits` and `pending_refund_credits` as explanatory rows and never add them to total/reserved. Extend MSW with payer-scoped orders, admin queue/detail/resolve, audit side effects, registry conflicts and refund states.

- [ ] **Step 7: Run tests and commit**

```powershell
pnpm --filter @huading/frontend test -- src/components/brand-voice/brand-voice-create.test.tsx src/components/brand-voice/brand-voice-list.test.tsx src/components/brand-voice/brand-voice-order-list.test.tsx src/components/workbench/voice-picker.test.tsx 'src/app/(admin)/admin/brand-voice-orders/page.test.tsx' src/mocks/admin-console.test.ts src/lib/api/no-authoritative-prices.test.ts
pnpm --filter @huading/frontend typecheck
git add frontend/src/lib/api/brand-voice-orders.ts frontend/src/lib/api/brand-voices.ts frontend/src/lib/api/admin-console.ts frontend/src/lib/api/quota.ts frontend/src/lib/api/hooks.ts frontend/src/lib/api/no-authoritative-prices.test.ts frontend/src/components/brand-voice/brand-voice-create.tsx frontend/src/components/brand-voice/brand-voice-list.tsx frontend/src/components/brand-voice/brand-voice-order-list.tsx frontend/src/components/brand-voice/brand-voice-order-list.test.tsx frontend/src/components/workbench/voice-picker.tsx frontend/src/components/layout/quota-badge.tsx 'frontend/src/app/(admin)/admin/brand-voice-orders/page.tsx' 'frontend/src/app/(admin)/admin/brand-voice-orders/page.test.tsx' 'frontend/src/app/(admin)/admin/voice-slots/page.tsx' frontend/src/mocks/handlers.ts frontend/src/mocks/admin-console.test.ts frontend/src/components/brand-voice/brand-voice-create.test.tsx frontend/src/components/brand-voice/brand-voice-list.test.tsx frontend/src/components/workbench/voice-picker.test.tsx
git commit -m "feat(frontend): add manual brand voice delivery UI"
```

---

### Task 17: 创建源码化定价工作台并完成关键 E2E

**Files:**
- Create: `docs/04-UIUX设计/定价工作台.html`
- Create: `frontend/e2e/pricing-closure.smoke.spec.ts`
- Create: `frontend/e2e/brand-voice-manual-order.smoke.spec.ts`
- Create: `frontend/e2e/pricing-workbench-doc.smoke.spec.ts`
- Modify: `frontend/playwright.config.ts` only if the existing projects lack desktop and mobile viewports.

**Interfaces:**
- Consumes: completed UI/MSW contracts from Tasks 14–16.
- Produces workbench data shape with independent closure status and visibility.
- Produces computed statistics; no manually typed totals.

- [ ] **Step 1: Write failing E2E tests for the workbench and priced flows**

```ts
test("pricing workbench computes six waiting, three deferred and one offline", async ({ page }) => {
  const { readFile } = await import("node:fs/promises");
  const { resolve } = await import("node:path");
  const html = await readFile(resolve(process.cwd(), "../docs/04-UIUX设计/定价工作台.html"), "utf8");
  await page.setContent(html);
  await expect(page.getByTestId("count-priced-waiting-release")).toHaveText("6");
  await expect(page.getByTestId("count-deferred-unpriced")).toHaveText("3");
  await expect(page.getByTestId("count-offline")).toHaveText("1");
  await expect(page.getByText("先冻结，人工交付后结算")).toBeVisible();
});


test("unknown script submit result recovers without a second supplier action", async ({ page }) => {
  await openWorkbenchWithMockBilling(page, { dropFirstSubmitResponse: true });
  await page.getByRole("button", { name: "AI 生成文案" }).click();
  await page.getByRole("button", { name: "确认并继续" }).click();
  await expect(page.getByText("计费结果确认中")).toBeVisible();
  await expect(page.getByText("文案已生成，扣除 1 积分")).toBeVisible();
  expect(await mockSupplierInvocationCount(page, "script_generate")).toBe(1);
});
```

Add desktop/mobile checks for all workbench row prices/states, hidden publish-draft marker, marketing poster offline, no console errors, estimate failure blocking, ecom 20/21 boundary, partial result copy, video billing/legacy/deferred branches, CosyVoice text requirement/line, Doubao order awaiting→fulfilled/rejected, payer isolation, old slot page read-only, and no false “未扣费” on unknown network state.

- [ ] **Step 2: Create the canonical standalone workbench data model**

The prior visual shown in chat has no recoverable file in the repository, known project directories, or the empty Codex visualization workspace. Therefore this task creates the first authoritative, source-controlled workbench at the exact path above and does not claim to overwrite an external original. The HTML must declare one data array and derive cards/statistics from it:

```js
const pricingRows = [
  { key: "script_generate", name: "AI 口播文案", closure: "priced_waiting_release", visibility: "visible", price: "1 积分/次", settlement: "成功返回合法文案后结算" },
  { key: "scene_prompt", name: "画面提示词", closure: "priced_waiting_release", visibility: "visible", price: "30 积分/次", settlement: "成功返回正向与负向提示词后结算" },
  { key: "ecom_cutout", name: "电商抠图", closure: "priced_waiting_release", visibility: "visible", price: "80 积分/张", settlement: "按成功图片聚合结算" },
  { key: "ecom_model", name: "AI 模特图", closure: "priced_waiting_release", visibility: "visible", price: "80 积分/张", settlement: "按成功图片聚合结算" },
  { key: "doubao_brand_voice", name: "豆包品牌音色", closure: "priced_waiting_release", visibility: "visible", price: "30000 积分/个/365 天", settlement: "先冻结，人工交付后结算" },
  { key: "cosyvoice", name: "CosyVoice", closure: "priced_waiting_release", visibility: "visible", price: "创建免费；使用时 0.1 积分/字", settlement: "字符费随父视频成功结算" },
  { key: "seedance_t2v", name: "seedance_t2v", closure: "deferred_unpriced", visibility: "visible", price: null, settlement: "延期处理／尚未闭环" },
  { key: "static_template", name: "static_template", closure: "deferred_unpriced", visibility: "visible", price: null, settlement: "延期处理／尚未闭环" },
  { key: "publish_draft", name: "发布草稿", closure: "deferred_unpriced", visibility: "hidden", price: null, settlement: "保持隐藏" },
  { key: "marketing_poster", name: "电商营销海报", closure: "offline", visibility: "hidden", price: null, settlement: "已下线" },
];

const countByClosure = (closure) => pricingRows.filter((row) => row.closure === closure).length;
```

Use accessible semantic cards/table, visible legends, responsive desktop/mobile layout, and badges that say “价格完成，待上线” rather than “已上线”. Include a persistent banner “生产环境冻结；以下状态不代表已生效”.

- [ ] **Step 3: Implement priced-action and manual-order E2E flows**

Drive the app through MSW using user-visible controls. Assert request headers, operation lookup after a dropped response, no duplicate supplier action, quote invalidation after input edits, three video contracts, and the complete manual order state/copy. Run the workbench doc test at desktop and a 390px mobile viewport, and fail on any `pageerror` or console error.

- [ ] **Step 4: Run E2E, inspect the visual artifact, and commit**

```powershell
pnpm --filter @huading/frontend build:mock
pnpm --filter @huading/frontend test:e2e -- pricing-closure.smoke.spec.ts brand-voice-manual-order.smoke.spec.ts pricing-workbench-doc.smoke.spec.ts
git add docs/04-UIUX设计/定价工作台.html frontend/e2e/pricing-closure.smoke.spec.ts frontend/e2e/brand-voice-manual-order.smoke.spec.ts frontend/e2e/pricing-workbench-doc.smoke.spec.ts frontend/playwright.config.ts
git commit -m "docs(pricing): add visual closure workbench and e2e"
```

Expected: all flows pass at desktop/mobile; the workbench shows 6 price-complete/waiting-release rows, 3 deferred rows, and 1 offline row, with no claim that production is live.

---

### Task 18: 执行全量验证并固化生产上线门禁

**Files:**
- Create: `docs/02-方案设计/定价闭环生产上线门禁.md`
- Verification fixes belong in the task that introduced the failing behavior and must be committed there before returning to this task; this task itself adds only the gate document.

**Interfaces:**
- Consumes every prior task.
- Produces a read-only release checklist that names the exact audit command, evidence categories, stop conditions and separate user approval required for production.

- [ ] **Step 1: Run all focused backend pricing/voice suites**

From the repository root:

```powershell
Push-Location backend
uv run pytest tests/test_billing_core_migration.py tests/test_pricing_rates_migration.py tests/test_manual_brand_voice_migration.py tests/test_pricing_closure_migration.py -q
uv run pytest tests/test_pricing_service.py tests/test_billing_quotes.py tests/test_billing_operations.py tests/test_transaction_retry.py -q
uv run pytest tests/test_scripts_billing.py tests/test_scene_prompt_billing.py tests/test_ecom_image_billing.py tests/test_video_pricing_contract.py -q
uv run pytest tests/test_brand_voice_orders.py tests/test_brand_voice_order_concurrency.py tests/test_brand_voice_provider_registry.py tests/test_subscription_refund_grants.py -q
uv run pytest tests/test_task_recovery.py tests/test_pricing_readiness.py tests/test_quota_concurrency.py tests/test_voice_slot_concurrency.py -q
Pop-Location
```

Expected: PASS with no xfail/skip added for implemented behavior.

- [ ] **Step 2: Run the real PostgreSQL constraint/concurrency gate**

```powershell
$testRunId = [guid]::NewGuid().ToString("N")
$testContainer = "huading-pricing-test-$testRunId"
$testDatabase = "huading_pricing_test_$($testRunId.Substring(0, 12))"
$testPassword = "HuadingTest_$testRunId"

docker run --detach --rm --name $testContainer `
  --env "POSTGRES_PASSWORD=$testPassword" `
  --env "POSTGRES_DB=$testDatabase" `
  --publish "127.0.0.1::5432" `
  postgres:16-alpine
if ($LASTEXITCODE -ne 0) { throw "Failed to start isolated PostgreSQL container" }

try {
  $ready = $false
  foreach ($attempt in 1..30) {
    docker exec $testContainer pg_isready --username postgres --dbname $testDatabase | Out-Null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Milliseconds 500
  }
  if (-not $ready) { throw "Isolated PostgreSQL did not become ready" }

  $portText = docker port $testContainer 5432/tcp
  if ($portText -notmatch '127\.0\.0\.1:(\d+)$') { throw "Unexpected PostgreSQL port mapping: $portText" }
  $testPort = $Matches[1]
  $env:TEST_POSTGRES_URL = "postgresql+psycopg://postgres:$testPassword@127.0.0.1:$testPort/$testDatabase"

  Push-Location backend
  uv run pytest tests/test_billing_core_migration.py tests/test_pricing_rates_migration.py tests/test_manual_brand_voice_migration.py tests/test_billing_operations.py tests/test_transaction_retry.py tests/test_brand_voice_order_concurrency.py tests/test_brand_voice_provider_registry.py tests/test_subscription_refund_grants.py tests/test_voice_slot_concurrency.py tests/test_quota_concurrency.py -q
  $postgresTestExit = $LASTEXITCODE
  Pop-Location
  if ($postgresTestExit -ne 0) { throw "PostgreSQL pricing gate failed" }
}
finally {
  Remove-Item Env:TEST_POSTGRES_URL -ErrorAction SilentlyContinue
  $runningContainer = docker ps --quiet --filter "name=^/$testContainer$"
  if ($runningContainer) { docker rm --force $testContainer | Out-Null }
}
```

Expected: PASS against a newly created localhost-only PostgreSQL 16 container whose unique database name starts with `huading_pricing_test_`; cleanup targets only the exact generated container. This gate has no code path for a production/shared URL.

- [ ] **Step 3: Run full backend quality gates**

```powershell
Push-Location backend
uv run ruff check app tests scripts
uv run pytest
Pop-Location
```

Expected: PASS. Fix only regressions caused by this implementation; record unrelated pre-existing failures separately and do not mask them with skips.

- [ ] **Step 4: Run full frontend quality gates**

From the repository root:

```powershell
pnpm --filter @huading/frontend lint
pnpm --filter @huading/frontend typecheck
pnpm --filter @huading/frontend test
pnpm --filter @huading/frontend build
pnpm --filter @huading/frontend build:mock
pnpm --filter @huading/frontend test:e2e
pnpm --filter @huading/frontend verify:full
```

Expected: PASS with no console errors in new E2E flows.

- [ ] **Step 5: Run static guard scans and read-only readiness locally**

```powershell
$forbiddenOutput = rg -n "DOUBAO_CLONE_CREDITS|items\[:20\]|slice\(0, 20\)|按实际结算|avatar.*150|video_gen.*80|reverse_prompt.*1" backend/app frontend/src -g "!**/*.test.*" -g "!frontend/src/mocks/**"
$forbiddenExit = $LASTEXITCODE
if ($forbiddenExit -eq 0) { $forbiddenOutput; throw "Forbidden pricing/truncation pattern found" }
if ($forbiddenExit -gt 1) { throw "Static guard scan failed to run" }
pnpm --filter @huading/frontend test -- src/lib/api/no-authoritative-prices.test.ts
Push-Location backend
uv run python scripts/ops/pricing_closure_readiness.py audit
Pop-Location
git diff --check origin/develop...HEAD
git diff --check
git diff --cached --check
$dirty = git status --short
if ($dirty) { $dirty; throw "Working tree must be clean before creating the gate document" }
```

Expected: the explicit `rg` guard finds no active authority/truncation/stale-fallback occurrences, the TypeScript AST guard finds no renamed numeric authority in production components, readiness succeeds only for the local fixture/config and makes zero writes, all diff checks are clean, and there are no staged/unstaged/untracked files.

- [ ] **Step 6: Create the production gate document**

Document this exact sequence: read-only production revision/ENV/rates/tenant overrides; export wallet/BrandVoice/all active and inactive ProviderConfig/official slot snapshot; platform owner signs the exact official ID list; verify `owner_user_id/activated_at/expires_at` remain null for history; run pre-deploy audit; obtain a separate explicit production approval; deploy backend+frontend+migrations together while customer Doubao routes remain fail closed; execute `register-official` exactly once using only the signed `ENGINE_DOUBAO_OFFICIAL_VOICE_IDS`; immediately rerun read-only `audit`; enable/accept customer routing only when registry/config/inventory are identical; smoke-test estimate/reserve/settle/release/manual fulfill/reject/idempotency/refund/recovery; reconcile wallet/usage/provider cost; only then change the workbench from “待上线” to “已上线”. The current implementation phase only builds/tests these commands and never runs production registration. Every blocker stops the release; the document forbids manual DB edits as a substitute.

- [ ] **Step 7: Commit the gate document and any narrowly required verification fixes**

```powershell
git add docs/02-方案设计/定价闭环生产上线门禁.md
git diff --cached --check
git commit -m "test(pricing): verify closure and document release gates"
```

Expected: working tree clean after commit. Do not push, merge, migrate or deploy without a separate explicit user instruction.

---

## Final Executor Checklist

- [ ] Every endpoint estimate is read-only and every new charged submit requires both billing headers.
- [ ] Every supplier mock asserts the reservation transaction committed before invocation.
- [ ] Every operation terminal state conserves requested = settled + released and returns `held_credits=0`.
- [ ] `UsageRecord` billing allocation remains intact after supplier token/character cost attachment.
- [ ] 20 ecom items work; 21 fail at schema, API, client and mock without truncation.
- [ ] Branded video estimate/submit/worker use one effective mode, one normalized TTS text and no duplicate CosyVoice fee.
- [ ] Doubao order waits indefinitely until admin resolve; no automatic provider registration/allocation/deletion occurs.
- [ ] Official/history provider IDs and new customer rights remain strictly separated; registry conflicts fail closed.
- [ ] Cross-period reject restores value exactly once and quota GET remains readable without an active subscription.
- [ ] Frontend never treats estimate failure as permission to continue and never claims release on an unknown network result.
- [ ] Workbench statistics are computed from rows and remain “待上线” while production is frozen.
- [ ] No production write, migration, registration, deployment, merge or push occurred during implementation without separate approval.
