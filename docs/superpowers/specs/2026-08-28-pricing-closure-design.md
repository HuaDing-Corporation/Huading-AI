# 尚未闭环功能定价与计费闭环设计

- 状态：设计已批准，等待用户复核书面规格
- 日期：2026-08-28
- 审计基线：`origin/develop@3d50df178d124d3718c8cb5565cb4caebb3126fd`
- 生产状态：冻结；本规格不授权部署、执行生产迁移或修改生产费率

## 1. 背景

项目已经具备订阅余额、积分预留、成功结算、失败释放和供应商成本记录等基础能力，但部分活跃功能仍存在以下缺口：

- 服务端没有权威报价，前端只能写死价格或完全不披露价格；
- 调用供应商前没有预留积分，失败路径也无法形成可审计的释放记录；
- 同一价格在前端、数据库费率和 `quota.py` fallback 之间可能漂移；
- 内部重试、重复点击和网络重试缺少统一幂等边界；
- 豆包品牌音色仍可被租户费率覆盖，且现有用户创建路径会自动分配平台槽位并调用供应商，这与“付款后由平台负责人在豆包官方手工注册并交付”的实际业务不一致；
- CosyVoice 创建当前免费，但其实际语音合成用量没有以品牌音色价格向用户清晰披露；
- 部分遗留功能仍开放后端模式，却没有形成产品定价决策。

本设计采用“统一价格策略层 + 复用现有配额账本”的方式闭合上述缺口，不重建整个计费系统。

## 2. 已确认的产品决策

### 2.1 本轮完成闭环的功能

| 产品功能 | 计价口径 | 费率作用域 | 结算条件 |
|---|---:|---|---|
| AI 生成口播文案 `/scripts/generate` | `1 积分/次` | 平台默认，可租户覆盖 | 返回非空、合法文案 |
| AI 生成画面提示词 `/videos/scene-prompt` | `30 积分/次` | 平台默认，可租户覆盖 | 返回非空、合法正向与负向提示词 |
| 电商抠图 | `80 积分/张` | 平台默认，可租户覆盖 | 对应图片产物成功并可读取 |
| 电商 AI 模特图 | `80 积分/张` | 平台默认，可租户覆盖 | 对应图片产物成功并可读取 |
| 豆包品牌音色 | `30000 积分/个/365 天` | 全平台固定，禁止租户覆盖 | 管理员人工交付成功；冻结积分转为结算，并从交付时起算 365 天 |
| CosyVoice 品牌音色 | 创建免费；使用时 `0.1 积分/字` | 平台默认，可租户覆盖 | 父视频任务最终成功 |

补充规则：

- 豆包品牌音色的 `30000` 是人工交付音色的年费。用户确认下单时只冻结积分，管理员交付后才结算；使用该豆包品牌音色时不再叠加 CosyVoice 的按字品牌音色费，数字人或视频本身的基础价格不受影响。
- CosyVoice 创建音色不收年费。`0.1 积分/字` 仅在实际使用该音色合成语音时产生，并作为数字人或电商视频总价中的一条 TTS 明细统一预留和结算。
- 非品牌音色的既有 TTS 价格与流程不在本轮改变。
- 上述可覆盖项目的公开价格是平台默认价；有效租户费率仍可优先于平台默认价。
- 豆包品牌音色仅解析平台固定价，即使数据库存在历史租户 `voice_clone/call` 费率也不得生效。
- 本轮只闭合价格和资金生命周期，不放宽任何现有套餐、VIP、角色、授权同意或素材合规门禁；权限检查必须先于报价或至少在报价与正式提交两处一致执行。

### 2.2 豆包人工交付与年费语义

- 当前平台官方自用的豆包音色与任何用户账号无关，保持原状：不迁移为用户音色、不创建订单或账单、不补扣积分、不回填生效或到期时间。
- 用户确认购买或续费时，系统原子创建人工交付订单并冻结 `30000` 积分；此时不得记作已结算，也不得创建一个假装可用的音色。
- 系统不得为用户自动调用豆包注册接口，也不得自动从平台官方自用槽位池分配音色。平台负责人在豆包官方后台手工完成注册或续期。
- 管理员以 `fulfill` 提交豆包音色 ID 后，系统才把音色绑定给付款用户、把冻结积分结算，并设置 `activated_at = fulfilled_at`、`expires_at = fulfilled_at + 365 天`。
- 管理员以 `reject` 并填写原因后，系统把订单标记为拒绝并完整释放原冻结积分，不创建或恢复音色权益。
- `awaiting_fulfillment` 订单不自动超时，用户不能取消。它只能由平台管理员 `fulfill` 或 `reject` 进入终态；用户也不能通过删除订单、源音频、待续费音色或账号内其他关联资源间接取消。
- 不自动续费。已到期音色不可用于新任务；续费同样执行“重新报价并确认 → 冻结 30000 → 人工处理 → 交付结算或拒绝释放”，新的 365 天只从本次交付成功时起算。
- 用户提前删除已交付音色不退款。删除只撤销本地可用性；豆包官方侧资源由管理员按运营流程人工处理，本轮不增加自动删除调用。
- 数据库中的音色状态仍表示 `processing | ready | failed`；人工订单状态独立表示 `awaiting_fulfillment | fulfilled | rejected`。API 将二者合成为用户可理解的 `delivery_status = awaiting_fulfillment | active | expired | rejected`。

### 2.3 延期和下线项目

| 功能 | 本轮处理 |
|---|---|
| `seedance_t2v` | 不下线、不定价、不新增计费；保持“延期处理／尚未闭环” |
| `static_template` | 不下线、不定价、不新增计费；保持“延期处理／尚未闭环” |
| 发布草稿 | 保持前端隐藏，不确定正式产品价，不纳入本轮计费闭环 |
| 电商营销海报 | 维持已下线状态和 HTTP 410，不恢复、不定价 |

本轮不得为了“顺手统一”而改变上述四项的现有可见性、路由行为或费用。

## 3. 当前实现与缺口

### 3.1 AI 口播文案

`backend/app/api/v1/routes/scripts.py` 当前直接调用 LLM 并记录供应商成本，没有用户积分估价、预留、结算或失败释放。

### 3.2 画面提示词

`backend/app/api/v1/routes/videos.py` 的 `/scene-prompt` 当前记录 Luna/APIMart 成本，但用户积分为零，没有计费生命周期。

### 3.3 电商抠图与 AI 模特图

`backend/app/api/v1/routes/ecom_images.py` 和图片 worker 已具备按任务预留、结算与释放的基础结构，但：

- 仍依赖通用图片费率及旧的 `10` 积分 fallback；
- 没有操作前估价与确认；
- 单张和批量请求没有统一报价绑定；
- 部分测试仍把 `10` 当作权威价格。

### 3.4 品牌音色

`backend/app/services/quota.py` 当前把豆包默认值设为 `30000`，但使用 tenant-first 的 `_rate()`，租户覆盖仍可改变价格；CosyVoice 克隆分支直接返回零积分。前端 `brand-voice-create.tsx` 还保存 `30000` 常量，服务端没有权威报价。现有 `POST /brand-voices` 会为豆包分配平台槽位、立即扣费并同步调用克隆供应商，既没有人工订单，也无法表达“冻结后等待平台负责人交付”。`brand_voices` 表还没有付款用户、生效和到期字段。

### 3.5 旧 fallback 与生产门禁

`quota.py` 中 avatar、video、video_gen、image、reverse_prompt 等路径仍存在旧 fallback。生产环境的 migration、ENV、`credit_rates` 以及租户覆盖尚未做同批核验，因此“代码合并”不能等同于“价格已上线”。

## 4. 方案选择

### 4.1 采用方案

新增统一的价格策略与报价服务，本轮进入新计费闭环的权威报价和实扣通过同一服务解析费率；明确保留的 legacy estimate 继续沿用既有契约，deferred unpriced 不解析费率。资金变更继续落到现有订阅计数器和 `usage_records`。另增加轻量的计费操作协调表承载幂等、请求绑定和结果查询；只为人工订单补充跨期退款凭证和不可复用供应商 ID 保护登记，不替换现有资金账本。

### 4.2 未采用方案

- **逐端点硬编码价格**：实现快，但会延续前后端和 fallback 漂移，无法可靠处理租户覆盖与豆包固定价。
- **重建事件化计费系统**：审计能力更强，但改动面和上线风险超过本轮需求。

## 5. 价格策略层

### 5.1 逻辑操作

价格策略使用产品操作名，而不是直接把所有调用塞入 `llm/call`：

- `script_generate`
- `scene_prompt`
- `ecom_cutout`
- `ecom_model`
- `doubao_brand_voice_order_create`
- `doubao_brand_voice_order_renew`
- `cosyvoice_brand_voice_create`
- `video_create`
- `cosyvoice_brand_tts`

每个策略至少包含：费率 capability、单位、代码默认价、作用域、数量解析器、取整方式和成功判据。

`video_create` 是需要新报价门的视频正式提交 operation，按 `video_mode` 组合既有父任务基础价和音色明细；`cosyvoice_brand_tts` 只作为其中的 breakdown 明细，不是可单独提交或查询的 billing operation。延期的 `seedance_t2v/static_template` 不创建 `video_create` operation。

### 5.2 费率映射

| 逻辑操作 | `credit_rates` capability/unit | 默认值 | 解析方式 |
|---|---|---:|---|
| `script_generate` | `script_generate/call` | `1.0000` | tenant → platform → code default |
| `scene_prompt` | `scene_prompt/call` | `30.0000` | tenant → platform → code default |
| `ecom_cutout` | `image/image` | `80.0000` | tenant → platform → code default；固定 1K 倍率 |
| `ecom_model` | `image/image` | `80.0000` | tenant → platform → code default；固定 1K 倍率 |
| 豆包人工创建与续费订单 | `voice_clone/call` | `30000.0000` | **platform → code default**；不得查询 tenant |
| CosyVoice 音色创建 | `voice_clone/call` | `0.0000` | operation 固定免费；不读取豆包年费或租户 voice_clone 行 |
| `video_create` | 既有父任务 capability + 可选 `tts/character` | 按 breakdown | 每条明细按自身现有作用域解析，聚合后一次取整 |
| CosyVoice 使用 | `tts/character` | `0.1000` | tenant → platform → code default |

迁移需要扩展 `credit_rates` 和 `usage_records` 的 capability 约束，使 `script_generate`、`scene_prompt` 成为合法值。历史记录不得被改写。

### 5.3 作用域规则

- `TENANT_OVERRIDABLE`：先读取当前租户有效费率，再读取平台有效费率，最后使用与迁移目标完全一致的代码默认价。
- `PLATFORM_FIXED`：只读取 `tenant_id IS NULL` 的有效平台费率；租户行即使存在也必须被忽略。
- 管理端拒绝新建或启用租户级豆包品牌音色费率，并给出明确错误码。
- 历史租户 `voice_clone/call` 行保留用于审计，不删除、不重写，但对豆包策略不再生效。

### 5.4 费率唯一性与生效时间

- 每个 scope/capability/unit 同一时刻最多有一条 active 费率。平台费率和租户费率分别使用 partial unique index 保证唯一。
- 解析器只接受 `is_active = true AND effective_at <= now` 的行；不得读取未来费率。
- 管理端可预先创建 inactive 的未来费率。到生效时必须在一个事务内停用旧行、启用新行，不能让两条 active 行重叠。
- 迁移前只读审计重复 active 行；发现重复时迁移失败并输出精确主键，不得用“查询到第一条”掩盖脏数据。
- 费率来源使用显式 tagged union，而不是假定每次都有数据库行：`source = tenant_rate | platform_rate` 时 `rate_id/effective_at` 必填；`source = code_default | fixed_policy` 时二者必须为 null，并改为记录非空 `policy_key`、单调递增的 `policy_version` 和精确费率。CosyVoice 创建免费固定使用 `fixed_policy`，不得伪造一条 `credit_rates`；数据库缺行时的代码 fallback 使用 `code_default`。报价快照和 token 绑定完整 provenance，费率行变化、默认值变化或对应 policy version 升级后，旧报价在正式提交复核时返回 `PRICE_CHANGED`。

### 5.5 取整

- 单价和中间小计使用 `Decimal`，禁止浮点计算。
- 先汇总一笔业务的全部明细，再按现有钱包整数积分规则向上取整为 `payable_credits`。
- 不得逐字符或逐明细分别取整。
- 报价同时返回精确 `subtotal_credits` 和真正影响钱包的 `payable_credits`；前端确认框必须突出显示后者。
- 批量业务只在整个 operation 最终完成时调整一次钱包：`settled_payable = ceil(成功 items 的精确小计之和)`，`released_payable = reserved_payable - settled_payable`。不得对每个 item 单独向上取整后相加。

### 5.6 金额定义域与零价例外

- 费率、数量、每条明细小计、聚合小计、钱包应付、预留、结算和释放金额都必须为有限且非负的 `Decimal` 或整数；任何负数、NaN、Infinity、精度溢出或字段间算术不一致都属于服务端配置/数据错误，不能被裁成零继续执行。
- 本轮所有收费策略均为 `REQUIRE_POSITIVE`：命中的单价、业务数量、精确小计和 `payable_credits` 都必须大于零。唯一的 `ALLOW_ZERO` 策略是 `cosyvoice_brand_voice_create`，且只能是固定的 `quantity = 1`、`unit_credits = 0`、`subtotal_credits = 0`、`payable_credits = 0`；它不读取 `credit_rates`，不能把任意数据库零价解释成免费活动。
- 价格解析器命中负费率时一律返回 `INVALID_ACTIVE_RATE` 并 fail closed；`REQUIRE_POSITIVE` 命中零费率时同样失败。失败不得签发报价、创建 operation、改余额或调用供应商。管理端保存费率、报价签发、报价复核和 worker 读取快照时都执行同一验证器。
- 报价和快照验证器必须重新证明 `unit_credits × quantity = line_subtotal`、各明细之和等于 `subtotal_credits`、`ceil(subtotal_credits) = payable_credits`。不得信任客户端金额，也不得仅因签名有效就接受内部字段不一致的令牌。

## 6. 服务端权威报价

### 6.1 报价响应

所有进入本轮新报价确认门的 `billing_quote` 响应返回统一结构：

```json
{
  "pricing_contract": "billing_quote",
  "operation": "scene_prompt",
  "pricing_shape": "simple",
  "unit": "call",
  "quantity": "1",
  "unit_credits": "30.0000",
  "subtotal_credits": "30.0000",
  "payable_credits": 30,
  "rate_scope": "tenant_overridable",
  "rate_source": "platform_rate",
  "breakdown": [],
  "disclosures": [],
  "quote_token": "<signed-token>",
  "expires_at": "<ISO-8601>"
}
```

金额通过 JSON 字符串传输精确小数；`payable_credits` 是整数钱包值。

报价 schema 区分两种形态：`simple` 的顶层 `unit/quantity/unit_credits/rate_scope/rate_source` 必须非空；`composite` 的这五个字段必须为 null，真实单位、数量、单价、scope 和 tagged provenance source 只出现在非空收费 `breakdown` 中。`video_create` 固定使用 composite，即使某次恰好只有一条明细，也不改变 schema；所有形态都只在顶层返回一份聚合 `subtotal_credits/payable_credits`。不得用虚构的“总单价”或虚假的单一 scope 填充混合单位字段。

`breakdown` 只包含本次报价会参与聚合的收费明细，每条都自带 scope/source，其精确小计之和必须等于顶层 subtotal。CosyVoice 创建时对未来字符费当前解析值（平台默认 `0.1/字`，可租户覆盖）的说明放入独立、签名保护但不计入本次总价的 `disclosures`；每条 disclosure 也带自己的 scope/source，并标明“当前参考费率，实际使用时重新报价”。不能把未来费率伪装成当前零价 breakdown。

服务端内部必须把每个 billing quote 规范化成非空 canonical `pricing_lines[]`：simple quote 由顶层 unit/quantity/rate/provenance 生成恰 1 条 line，即使展示用 `breakdown = []`；composite quote 的 pricing lines 必须逐项等于 response breakdown。后续 token digest、snapshot、预留分配和结算只认 pricing lines，禁止从“展示 breakdown 是否为空”推断有没有收费明细。

### 6.2 报价令牌

`quote_token` 是服务端签名、无状态、有效期 10 分钟的令牌，至少绑定：

- tenant 与当前用户；
- 逻辑操作；
- 去除计费头后规范化业务请求的 SHA-256；
- 报价 schema version、顶层精确 subtotal/整数 payable，以及规范化完整 pricing payload 的 SHA-256；该 payload 包含 canonical `pricing_lines[]` 与 disclosures；
- pricing payload 内每条明细的完整 rate provenance：数据库来源绑定费率行和有效时间，代码默认/固定策略绑定 policy key、version 和精确值；
- 签发和过期时间。

令牌不得原样内嵌逐 item breakdown；服务端返回完整报价给 UI，但 token 只签名绑定其规范化 digest 和必要顶层字段，base64url 编码后硬上限为 4096 ASCII bytes。服务端与网关 header 限制必须覆盖该上限，超过上限的输入在解码前返回 `QUOTE_TOKEN_TOO_LARGE`，不得创建 operation。

正式提交时服务端必须重新规范化请求、重新解析当前费率与完整 pricing payload，比较 digest 后再验证令牌。请求内容或价格变化时返回 `PRICE_CHANGED`，不得预留积分或调用供应商。

单张与批量电商请求共享一套规范化器：单张请求先包装成一个 item，再与批量 items 使用相同的稳定字段顺序计算 hash。batch request schema 在 estimate 和 submit 两端统一强制 `1..20` 项；第 21 项直接 422，严禁路由使用 `items[:20]` 静默截断。估价、提交、request hash、pricing payload、quantity 和 `billing_item_index` 全部使用同一份已验证列表，quantity 必须精确等于 N；不同尾部内容必须产生不同 hash。

### 6.3 价格快照

报价确认并预留时，`billing_operations.pricing_snapshot` 持久化不可变 JSON：完整保存 canonical `pricing_lines[]`，每条 line 包含逻辑操作、capability、unit、rate scope、完整的 tagged rate provenance、精确单价、报价数量和精确小计；数据库来源必须保存费率行 ID 和 `effective_at`，`code_default/fixed_policy` 必须保存 null 行 ID、policy key 和 version。snapshot 顶层只保存一份聚合后的 `subtotal_credits`、`payable_credits` 和 `ROUND_CEILING` 取整方式，禁止给每条 line 生成钱包应付值后相加。

每个关联 `usage_record` 保存 `billing_pricing_line_index`、自己的业务 `billing_item_index`、分配数量和精确分配 subtotal，并通过 operation snapshot 追溯完整 provenance。simple 单项引用唯一 line 0；simple 电商 batch 的 N 个 usage 都引用 line 0、各分配一张；composite 视频按收费组件建立 usage 并引用对应 line。一个 operation 的全部 usage 精确分配和必须逐 line 等于 pricing lines，再统一按顶层 payable 调整钱包。

snapshot 还必须原样持久化规范化 `disclosures[]`：至少包含 disclosure key、用户当时看到的已渲染文案、文案 version、unit、scope/source、完整 rate provenance 和精确参考费率。它与提交时重算的 pricing payload digest 必须一致，但永不参与 subtotal、钱包预留或结算，也不复制到 usage 金额。调价或披露文案升级后，历史 operation 仍必须能仅凭 snapshot 还原用户当时确认的说明。

结算只能读取该快照，禁止再次调用实时费率解析器。任务执行期间调价不得改变已经确认的实扣金额。实际成功量只能小于或等于预留量；任何路径都不得自动追加预留或扣除超过报价的积分。若业务必须扩大用量，应在调用供应商前终止本次 operation，并要求用户重新报价和确认。

### 6.4 估价端点

- `POST /api/v1/scripts/estimate`：请求体与 `ScriptGenerateRequest` 一致。
- `POST /api/v1/videos/scene-prompt/estimate`：请求体与 `ScenePromptRequest` 一致。
- `POST /api/v1/ecom-images/cutout/estimate`：接收精确的单张或批量抠图 items。
- `POST /api/v1/ecom-images/model/estimate`：接收精确的单张或批量模特图 items。
- `POST /api/v1/brand-voice-orders/estimate`：仅用于豆包人工订单；请求体用 `order_type = create | renew` 区分新购与续费。新购和续费都必须提交本次名称、源音频和授权确认；续费还要绑定付款用户拥有且已到期的豆包音色。两者都报价 `30000`。
- `POST /api/v1/brand-voices/estimate`：仅用于可自动创建的 CosyVoice，创建应付 `0`，同时在不计入本次 subtotal 的 `disclosures` 披露当前解析到的后续使用参考费率（平台默认 `0.1 积分/字`）；实际使用时重新报价。
- 现有 `POST /api/v1/videos/estimate` 使用 `pricing_contract` 判别联合响应，并与 §7.4 的 `effective_video_mode` 和服务端音色归属解析完全一致：
  - `billing_quote`：仅适用于进入新 `video_create` 门的品牌音色视频，返回完整 composite quote、breakdown、token 和 expires_at；CosyVoice 显示 TTS 字符费，豆包不增加品牌音色字符费；
  - `legacy_estimate`：适用于预置音色及 photo/video_gen 等继续沿用既有视频计费契约的路径，返回既有 `estimated_credits/unit/note`，新 operation/token/expires_at 必须为 null 或不存在；
  - `deferred_unpriced`：仅适用于解析后确实为 `static_template/seedance_t2v` 的延期路径，明确 `estimated_credits = 0`、`unpriced = true`，operation/token/expires_at 必须为 null 或不存在。这不是合法零价 billing quote，不创建 operation，也不扩大 §5.6 的零价例外。

同一响应对象不能同时满足两个 contract。只有 `billing_quote` 后续提交需要 `Idempotency-Key/X-Huading-Quote`；legacy 与 deferred 请求若伪带新 header，也不能借此创建 `video_create` operation。

估价端点只读，不得创建任务、预留积分、修改历史或调用供应商。

## 7. 幂等与计费操作协调

### 7.1 `billing_operations`

新增轻量协调表：

- `id`：服务端内部 UUID 主键；
- `idempotency_key`：客户端生成的 UUID；
- `tenant_id`、`user_id`、`operation`；
- `request_hash`、`quote_hash`；
- 不可变的 `pricing_snapshot`；
- `requested_credits`、`settled_credits`、`released_credits`；
- `status`：`in_progress | completed`；
- `completion_kind`：执行中为空，终态为 `succeeded | failed | rejected`；
- 可选的 `result_type`、`result_id`；
- 可选的、序列化后最大 64 KiB 的 `result_payload` JSON，用于保存脚本和画面提示词这类同步小结果；
- 可选的 `error_code`、`error_http_status` 和经过应用错误 schema 清洗、最大 16 KiB 的 `error_payload`，用于安全重放失败结果；
- `created_at`、`updated_at`、`completed_at`。

唯一约束为 `(tenant_id, user_id, operation, idempotency_key)`。财务终态仍由子 `usage_records` 的 `reserved | settled | released` 表示；协调表不另建一套余额账。成功结果和可控失败必须先持久化再返回，重复请求据此重放相同业务响应。`result_payload/error_payload` 只能保存经过响应 schema 校验的内容，不得写入供应商密钥、原始异常或未裁剪日志。

`usage_records` 新增可空的 `billing_operation_id` 外键（`ON DELETE RESTRICT`）、`billing_item_index` 和 `billing_pricing_line_index`。新计费路径中的每个 operation 对应一到多条 usage records；已关联的行必须使用从 `0` 开始的非空 item index，并保证 `(billing_operation_id, billing_item_index)` 唯一，pricing line index 必须落在该 operation snapshot 的 canonical lines 范围内。零价的 CosyVoice 创建也保留一条引用 line 0 的零积分 usage 记录，以承载幂等状态和真实供应商成本。财务记录不得随业务历史或协调记录删除。

数据库与应用层共同保证以下金额约束：`requested_credits >= 0`、`settled_credits >= 0`、`released_credits >= 0`。`in_progress` operation 必须没有 completion kind/完成时间，且 `settled_credits = released_credits = 0`；`completed` operation 必须有 completion kind/完成时间，并满足 `settled_credits + released_credits = requested_credits`。`failed/rejected` 终态必须 `settled_credits = 0` 且全部 requested 都进入 released；`succeeded` 的正价 operation 必须至少结算一个积分，批量有成功项时即使同时释放失败项也使用 `succeeded`；零价 CosyVoice 创建成功时允许 succeeded 且三个金额都为零。除 `cosyvoice_brand_voice_create` 外，`requested_credits` 必须大于零；该零价 operation 必须恰为零。关联 usage 的数量和精确 credits 也必须非负；零 credits 只允许属于该显式零价 operation。凡跨表条件无法由数据库 CHECK 表达，均由单一计费服务在写入和终态转换时校验，并由一致性测试覆盖。

豆包人工订单在等待期间保持 `billing_operations.status = in_progress`、子 `usage_records.status = reserved`；`fulfill` 或 `reject` 后才把 operation 改为 `completed`。operation 的 `result_type = brand_voice_order`、`result_id = order.id`，用于断线恢复和幂等重放。

### 7.2 `brand_voice_orders`

新增独立人工交付订单表，避免把供应商音色状态和财务审批状态混在一起：

- `id`：UUID 主键；
- `tenant_id`、`ordered_by_user_id`：付款租户和付款用户，终身不可改；
- `order_type`：`create | renew`；
- `requested_name`、`source_audio_asset_id`、`consent_confirmed_at`：本次请求的不可变交付材料；新购和续费都必须重新提交源音频并确认授权，不依赖一年前的历史对象仍然存在；
- `existing_brand_voice_id`：续费订单必填，创建订单必须为空；
- `billing_operation_id`：唯一外键，指向本次冻结的计费操作；
- `status`：`awaiting_fulfillment | fulfilled | rejected`；
- `fulfilled_brand_voice_id`、`provider_voice_id`：仅交付成功后写入；
- `resolved_by_user_id`、`fulfilled_at`、`rejected_at`：执行管理员及互斥的动作时间；
- `rejection_reason`：拒绝时必填，交付时必须为空；
- `created_at`、`updated_at`。

同一行的数据库 CHECK 保证：create 和 renew 都有本次名称、源音频和授权；create 的 `existing_brand_voice_id` 为空；fulfilled 有管理员、交付时间、供应商 ID 和最终音色且没有拒绝原因；rejected 有管理员、拒绝时间和非空原因且没有交付结果；续费交付的 `fulfilled_brand_voice_id` 等于 `existing_brand_voice_id`。同一已到期音色同时最多存在一张 `awaiting_fulfillment` 续费订单，订单到用户、计费操作、音色和源音频的审计外键使用 `ON DELETE RESTRICT`。

“续费音色属于付款用户、同 tenant、豆包 provider、已到期且未删除”是跨表条件，不能声称由普通 CHECK 保证。下单服务必须在事务中锁定 BrandVoice 并逐项校验，resolve 再次校验；任一不符都不冻结或不交付。并发创建由待处理续费唯一索引兜底。

新人工交付的豆包 `brand_voices` 增加可空 `owner_user_id`、`activated_at`、`expires_at`；创建交付时 `owner_user_id = ordered_by_user_id`，续费不得改变所有者。交付时固定写入 `provider = doubao-voice-clone`，并把订单的 `provider_voice_id` 映射到现有 `BrandVoice.speaker_id`。新购交付直接创建 `status = ready` 的 BrandVoice；等待和拒绝只存在订单表，不扩充供应商状态枚举。

新增不可复用的 `brand_voice_provider_ids` 保护登记表：`(provider, provider_voice_id)` 全局唯一，并记录 `ownership_kind = official | customer`、可空的 `brand_voice_id/first_order_id`、`status = active | retired` 及时间。客户新购交付必须先在同一事务中占用一个从未登记的 ID；续费可继续使用登记给同一 BrandVoice 的 ID，也可登记新 ID并把旧 ID标记 retired。retired 或已软删除音色的 ID 仍永久保留，不得交给另一个用户；本轮没有“远端已销毁后释放 ID”的接口。

所有会写入、预留或占用豆包 speaker/provider voice ID 的路径必须复用单一 provider-ID registry service，并使用现有 `huading:voice-slot:<speaker_id>` PostgreSQL transaction advisory lock 作为相同 `(provider, id)` 的互斥锁；不得另起一个不相交的锁命名空间。锁内统一检查 registry、`ENGINE_DOUBAO_OFFICIAL_VOICE_IDS`、所有 ProviderConfig 的 `speaker_ids` 与 `used_speaker_ids`、全部历史 BrandVoice，再执行登记/续费换 ID。registry 唯一约束是最后兜底，不能代替对 JSON 配置和历史记录的锁内检查。

旧的 tenant speaker-slot 预分配与新的“用户付款后人工注册并交付”模型不再兼容。本轮启用新模型时，`POST /api/v1/admin/console/tenants/{tenant_id}/voice-slots` 固定返回 HTTP 410 `VOICE_SLOT_ASSIGNMENT_RETIRED`，`assign_speaker_slot(..., apply=True)` 及对应 CLI 写模式同样 fail closed；只读清单可保留用于上线审计。任何通用 ProviderConfig 管理入口也不得直接改 `speaker_ids/used_speaker_ids`。这些只读/退役接口仍必须同时校验平台租户和 `User.role = admin`。已有配置不自动删除或转归属，而是进入上线存量清单：官方清单外的 ID 继续阻断启用，等待单独产品决策。

待处理订单对源音频建立删除保护：不得硬删除或软删除其 Asset，也不得把外键设空。订单创建与素材删除路径必须按相同顺序锁定 Asset 行，再检查是否存在 `awaiting_fulfillment`，消除“刚下单同时被清理”的竞态。管理员读取时由服务端临时签发短时下载地址；数据库和 API 不保存永久公开 URL。订单终结后 Asset 数据库行继续因审计外键保留，但允许按普通素材策略软删除并清理存储对象；授权确认时间和当时的文件元数据快照永久保留。

现有平台官方自用音色不创建 `brand_voice_orders`，不设置 `owner_user_id/activated_at/expires_at`，也不产生账务。上线门禁由平台负责人确认精确的 `ENGINE_DOUBAO_OFFICIAL_VOICE_IDS`，并把这些 ID 仅登记为 `ownership_kind = official` 的保护记录；这不是用户权益迁移，也不触碰历史 BrandVoice。生产环境配置缺失、为空或与保护登记不一致时，豆包客户下单和 resolve 路由必须 fail closed。官方账号继续沿用现有内部使用方式，客户账号不可见、不可选、不可获赠这些 ID。只有上线后通过订单 `fulfill` 产生的客户记录才具有 365 天权益。

上线前若发现任何不在官方确认清单内的历史豆包 BrandVoice 或已占用 slot，部署必须阻断并等待单独产品决策；不得把它默认视为永久免费、tenant 共享或用户年费权益。非豆包历史记录和 CosyVoice 仍保持现有 tenant 级兼容语义。

### 7.3 跨订阅周期的冻结与退款

豆包人工订单可能等待超过原订阅周期。reservation 的 `usage_records.subscription_id` 始终指向原订阅，退款凭证称其为 `source_subscription_id`；`fulfill` 即使在该订阅过期后也只把原 reservation 转为 used，不扣当前周期第二次费用。

`reject` 必须让冻结价值重新可用：

- 原订阅仍是当前 active subscription：正常减少其 reserved，立即恢复 remaining；
- 原订阅已过期或不再 active：在减少原 reserved 的同一事务中创建唯一的 `credit_refund_grants` 记录，金额固定等于原 reservation；若已有当前 active subscription，立即把该金额增加到其 `quota_credits_total` 并标记 `applied`；若当前没有订阅，则保持 `pending`，在下一张订阅激活时原子且只应用一次；
- grant 以 `billing_operation_id` 唯一，记录 source/target subscription、tenant、付款用户、金额、状态和应用时间。`pending` grant 不设过期时间、不得人工改额，也不得与普通管理员调额混为一条无来源记录；一旦 applied 到目标订阅，其积分与该订阅的其他积分采用相同 `period_end`，不形成第二套永久钱包。

配额 API 除当前订阅的 `total/used/reserved/remaining` 外，另返回 `manual_fulfillment_held_credits` 和 `pending_refund_credits`。套餐续订、换套餐和人工调额必须保留这两类值；任何路径都不得把同一冻结复制到两个订阅或重复应用退款 grant。

`reject` 与所有“创建或激活一张新订阅”的路径必须先取得同一 `Tenant` 行级互斥锁，并在锁内重新查询当前 active subscription。`reject` 在锁内创建唯一 grant，并根据锁内重查结果立即应用或保留 pending；订阅激活路径在同一锁内先写入/确认唯一 active subscription，再锁定并汇总该 tenant 的全部 pending grants，一次性加到新订阅并逐条标记 applied。这样即使“拒绝订单”和“新订阅激活”并发，也不可能出现已有 active subscription 而 grant 永久 pending 的丢唤醒状态。

`GET /api/v1/quota` 改为始终返回 tenant 级配额快照，而不是在没有 active subscription 时直接 404。无 active subscription 时返回 `has_active_subscription = false`、`active_subscription_id = null`，当前钱包的 `total/used/reserved/remaining` 均为 `0`，但仍汇总并返回过期原订阅上的 `manual_fulfillment_held_credits` 和未应用的 `pending_refund_credits`；有 active subscription 时返回正常钱包值及这两个附加字段。它们是解释性子集/跨期值，客户端不得再次加到 `total` 或 `reserved`。收费写接口在没有 active subscription 时仍返回 `SUBSCRIPTION_NOT_FOUND`，这一只读兼容不等于允许无套餐消费。

### 7.4 正式提交与查询接口

以下端点始终要求报价头：脚本生成、画面提示词、电商抠图、电商模特图、CosyVoice 创建，以及豆包人工创建/续费订单。

正式提交写入的 operation 值与 §5.1 完全一致；CosyVoice 创建使用 `cosyvoice_brand_voice_create`，符合下方条件的视频使用 `video_create`。operation 查询 URL 必须使用该父 operation，不能使用 breakdown-only 的 `cosyvoice_brand_tts`。

共用的 `POST /api/v1/videos` 不能直接按请求字面的 `video_mode` 判断报价门。服务端新增唯一的 `resolve_effective_video_mode(payload)`，严格复用当前实际分流优先级：显式 `photo`、`seedance_i2v`、`video_gen` 分别保持自身模式；其余请求只要显式为 `avatar_talk`，或携带旧版 `voice_id/avatar_asset_id/avatar_video_asset_id` 任一 avatar 字段，就解析为 `avatar_talk`；否则才保留 `static_template/seedance_t2v`。estimate、正式提交、权限校验、请求规范化/hash、报价 token 和 operation 创建必须全部调用该解析器，禁止各自复制条件。规范化请求把解析结果写入 `effective_video_mode` 并按同一规则处理旧字段，防止字面延期模式绕过收费分支。

随后按 `effective_video_mode` 使用条件矩阵：

- `effective_video_mode in {avatar_talk, seedance_i2v}` 且服务端解析出的 `voice_id` 属于豆包或 CosyVoice 品牌音色：要求本设计的报价头，并验证 provider-aware 视频报价；
- 预置音色继续使用现有视频计费契约，本轮不额外加一道新 header；
- 只有解析后的有效模式确实为 `seedance_t2v` 或 `static_template` 才不要求新报价头，现有路由、估价零值和费用行为保持不变；请求字面为这两者但携带旧 avatar 字段、最终被解析为 `avatar_talk` 时不得豁免；
- 判断必须基于服务端音色记录，不能相信客户端自报 provider；对 `owner_user_id` 非空的新人工豆包音色还必须校验当前用户就是付款所有者。

需要报价时要求：

- `Idempotency-Key: <billing operation UUID>`
- `X-Huading-Quote: <quote_token>`

正式提交先按当前 user/operation/key 查找既有 billing operation，再决定是否验证新报价：同 key 同 request hash 直接按持久化快照返回原操作状态、成功结果或可控失败，即使原 quote 已过期或当前费率已变化也不重新计价；同 key 不同请求返回 `IDEMPOTENCY_KEY_REUSED`。只有不存在 operation 的新 key 才验证 quote、当前费率和余额并创建 reservation。任何重放都不得重复预留或调用供应商。

豆包用户接口为：

- `POST /api/v1/brand-voice-orders`：验证报价后，在同一事务内冻结 `30000`、创建 billing operation、usage reservation 和 `awaiting_fulfillment` 订单；返回 `201`，不得调用豆包注册接口；
- `GET /api/v1/brand-voice-orders`：只列出当前付款用户自己的订单；
- `GET /api/v1/brand-voice-orders/{order_id}`：只允许付款用户查询自己的订单和冻结/结算状态。

现有 `POST /api/v1/brand-voices` 只保留 CosyVoice 自动创建。旧客户端若在该端点提交豆包 provider，服务端返回 `DOUBAO_MANUAL_ORDER_REQUIRED`，不得分配槽位、扣费或调用供应商。

现有 brand voice 列表、详情、改名、删除和视频选音查询对新人工豆包记录统一增加 `owner_user_id = current_user.id` 条件；其他同 tenant 用户不得查看、修改、删除或使用。经平台负责人确认的官方自用音色 ID 从客户接口排除，只保留官方账号的现有内部使用。未知归属的历史豆包记录在上线门禁中阻断，不进入兼容分支；非豆包历史记录和 CosyVoice 保持现有 tenant 级语义。

管理员操作面保持最小闭环：

- `GET /api/v1/admin/console/brand-voice-orders?status=awaiting_fulfillment`：复用现有后台权限，只返回人工队列元数据，不批量暴露音频 URL；
- `GET /api/v1/admin/console/brand-voice-orders/{order_id}`：仅平台管理员可读取订单详情并按需获取一次短时源音频下载地址，每次签发均写审计日志；
- `POST /api/v1/admin/console/brand-voice-orders/{order_id}/resolve`：唯一写操作，请求动作为 `fulfill` 或 `reject`。`fulfill` 必须提交 `provider_voice_id`，`reject` 必须提交非空原因。

管理员接口必须同时满足“属于平台租户”和 `User.role = admin`，不能只复用当前仅校验平台 tenant 的 `require_platform_admin`。每次决策记录操作者、动作、订单、前后状态和时间到 `AdminAuditLog`。`fulfill` 必须通过统一 registry service，在同一 provider-ID advisory lock 内核对保护登记、官方配置、所有 `ProviderConfig.speaker_ids/used_speaker_ids` 和全部历史 BrandVoice；只有从未出现在任一来源的新 ID，或续费时已登记给同一 BrandVoice 且未冲突的 ID 才能继续。相同动作和相同内容重放返回既有结果；相反动作、不同音色 ID 或不同拒绝原因重放返回 `BRAND_VOICE_ORDER_ALREADY_RESOLVED`（HTTP 409）。并发 `fulfill/reject` 只能有一个事务成功。

客户端在没有收到最终响应时可调用：

- `GET /api/v1/billing/operations/by-idempotency/{operation}/{idempotency_key}`

该端点必须同时按当前 tenant 和 `billing_operations.user_id = current_user.id` 隔离，返回经过业务 response schema 校验的 `BillingOperationLookup`，不泄露供应商密钥、原始异常、同 tenant 其他用户或其他租户信息。只要 operation 可见且存在，GET 一律返回 HTTP 200；404 仅表示不存在或对当前用户不可见，不能用 403 暴露记录存在性。原始 POST 的同 key 重放仍恢复当时业务 HTTP status，GET 不重放 4xx/5xx，而是在 payload 中报告原状态。

`BillingOperationLookup` 至少包含 `operation/idempotency_key/state/completion_kind/billing`，并以数据库状态约束以下联合：

- `state = in_progress`：`completion_kind = null`、sanitized failure 为空；同步任务可以没有 resource，已经创建人工订单或异步任务时允许返回经对应 response schema 校验的 typed resource/ref；
- `state = completed, completion_kind = succeeded`：返回经 `result_type` 对应 schema 校验的 typed result/resource，failure 为空；
- `state = completed, completion_kind = rejected`：仅返回经审查的 rejected brand-voice-order resource，failure 为空；
- `state = completed, completion_kind = failed`：result/resource 为空，返回 `failure = {code, original_http_status, detail}`；detail 必须经过应用错误 schema 清洗，不能包含原始异常或密钥。

所有四类都必须返回同一 `billing` schema。未知 `result_type`、存储 payload 无法通过 schema、终态缺少应有 result/failure 或金额不守恒时 fail closed 并告警，不能把不可信 JSON原样透传。客户端据此轮询 reserved→终态；网络中断后不根据 HTTP 200 本身猜测成功。

现有 `ApiResponse` 只有 `data/error/request_id`，前端 `apiFetch` 会解包并只返回 `data`。因此 billing 不能放在封套根级。所有进入本轮新报价门并创建 `billing_operations` 的提交端点，成功 response schema 都必须在 `data` 内直接增加 `billing`：

共用 `POST /api/v1/videos` 的 `VideoAccepted` 同样以 `pricing_contract` 作为 response discriminator：`billing_quote` 分支的 `billing` 必填且 operation 必须为 `video_create`；`legacy_estimate/deferred_unpriced` 分支不得伪造 operation，`billing` 必须为 null 或不存在。后端 response validator 与前端 TypeScript union 都按这一判别关系收窄，不能把 billing 全局设为 optional 后让品牌收费分支漏回计费状态。

`billing.requested_credits` 表示本次确认的总钱包金额，`held_credits` 表示此刻仍被冻结的金额，终态必须为零；它不能继续用名为 `reserved_credits` 的字段承载“原请求金额”。展示状态按 operation 终态和金额唯一派生：`reserved` = in progress；`settled` = succeeded 且 released 为零（包括合法零价成功）；`partially_settled` = succeeded 且 settled/released 都大于零；`released` = failed/rejected 且 settled 为零。前端不得自行从业务任务状态猜测计费状态。

```json
{
  "data": {
    "id": "business-result-id",
    "pricing_contract": "billing_quote",
    "billing": {
      "operation_id": "server-operation-uuid",
      "idempotency_key": "client-operation-uuid",
      "status": "settled",
      "requested_credits": 30,
      "held_credits": 0,
      "settled_credits": 30,
      "released_credits": 0
    }
  },
  "error": null,
  "request_id": "request-id"
}
```

在 operation 已创建后发生的应用层可控错误，把同一 `billing` 对象放进 `ApiResponse.error.detail.billing`；现有 `ApiError.detail` 会完整保留它，端点 API helper 负责运行时窄化。operation 创建前的校验错误可以没有 billing。网关错误、无法解析的响应或网络中断也不具备该字段，客户端必须按“未知”处理并查询 operation。后端 response schemas、前端 types/mocks 和成功/错误契约测试必须同时覆盖这两个精确位置，不能增加一个会被 `apiFetch` 丢弃的根级字段。

## 8. 业务数据流

### 8.1 通用顺序

```text
业务输入
  → 只读估价
  → 前端展示单价、数量、明细和 payable_credits
  → 用户确认
  → 服务端验证报价令牌与幂等键
  → 原子创建 billing operation、usage reservation 并预留余额
  → 提交事务
  → 自动业务：调用供应商并校验结果，成功结算 / 失败释放
  → 豆包人工订单：返回 awaiting_fulfillment，不调用供应商，等待管理员 fulfill / reject
  → 返回业务结果或订单、billing operation 与最新余额摘要
```

估价不可用时确认按钮必须禁用，不允许显示“按实际结算”后继续提交。

### 8.2 脚本与画面提示词

- 每次用户明确确认代表一笔新的收费意图。
- 在 LLM/Luna 调用前预留 `1` 或 `30` 积分。
- 内部 SDK 或供应商自动重试复用同一 operation 和 reservation。
- 结果必须通过现有清洗，并满足非空及 schema 约束后才能结算。
- 对同步成功结果，保存 `result_payload` 与资金结算必须位于同一事务；响应丢失后可按幂等键取回原结果。
- 供应商 token usage/cost 写入本次 reservation 对应的同一条 `usage_record`，不得再生成一条重复的零积分成本记录。即使调用最终失败，也尽可能保留真实成本；释放用户积分不抹去上游成本。

### 8.3 电商抠图与 AI 模特图

- 单张和批量请求都按 item 数量估价和聚合预留，每个 item 对应一个保存精确小计的 `usage_record`，同属一个 `billing_operation`。
- 任务创建与全部预留在同一数据库事务中完成；余额不足时不得创建任何任务。
- worker 只把每个 item 标记为“可结算成功”或“应释放失败”；当 operation 的全部 items 进入终态后，finalizer 在一笔事务中计算 `ceil(成功 items 精确小计之和)`，一次性把聚合预留拆为已用与已释放。租户图片费率即使是小数也不得发生逐项取整放大。
- 图片产物变为可读取状态与对应 item 的“可结算成功”标记必须在同一事务；整个 operation 在聚合钱包结算提交前不得标记 `completed`。
- 批量部分成功时，成功项结算、失败项释放；不得按整个批次全扣或全免。
- 旧测试中的 `10` 积分全部废止。

### 8.4 豆包品牌音色

创建或续费的用户提交阶段：

- 前端展示 `30000 积分/个/365 天`，明确“现在冻结、人工交付后结算、拒绝后解冻、不自动超时、无法取消”。
- 服务端验证付款用户、源音频、授权、报价和幂等键，在一笔事务内创建 billing operation、绑定原 subscription 的 `30000` reservation 和 `awaiting_fulfillment` 订单。
- 创建订单只保存交付材料和审计信息，不预建可用 `brand_voice`，不读取或占用官方音色槽位，不解析豆包 provider，也不发任何豆包注册网络请求。
- 续费订单引用原付款用户拥有且已到期的音色，并使用本次重新提交的源音频和授权；等待期间音色继续不可用。源音频和关联音色均受删除保护。

平台负责人先在豆包官方后台手工注册或续期，再调用管理员决策接口：

- resolve 取得全局顺序锁后先处理终态重放：终态动作和内容完全相同就返回既有结果，冲突则 409。只有首次状态转换才在任何业务写入前 fail closed 校验：order 仍是 `awaiting_fulfillment`；operation 是 `in_progress` 且没有 completion_kind；唯一 usage 仍是 `reserved`；operation、usage、报价快照和原 subscription hold 都指向同一订单且应付恰为 `30000`；subscription 的钱包 reserved 与下述确定性对账值一致。任一条件不成立都保持订单等待、写高优先级告警，绝不交付或拒绝。
- 对每张 subscription，`expected_wallet_reserved` 固定计算为：按 `billing_operation_id` 去重，汇总所有仍为 `in_progress` 且在该 subscription 至少有一条 `reserved` usage 的新 operation 的整数 `requested_credits`；再加上 `billing_operation_id IS NULL AND status = reserved` 的遗留 usage 按其既有 `_credit_units(credits) = ceil(credits)` 逐记录得到的钱包单位。新批量 operation 绝不能对子 usage 的小数 credits 求和或逐项 ceil；已关联 usage 还必须全部指向同一 subscription，并满足“operation in progress ⇔ 至少一条且仅有该 operation 的合法 reserved 子记录”的状态不变量。`expected_wallet_reserved != subscription.quota_credits_reserved` 时 fail closed。这样小数批量、旧单条 reservation 和人工订单并存时不会误报或漏报。
- `fulfill` 事务按全局锁顺序锁定 operation、相关 subscriptions、usage、order、provider ID 保护登记和目标音色。创建订单生成付款用户专属 `brand_voice`；续费订单更新原记录。两者都写入管理员提交的 `provider_voice_id`，原子完成保护登记、`ready + fulfilled + settled + activated_at + expires_at`、billing operation completion 和 `AdminAuditLog`。
- `fulfilled_at` 使用服务端事务时间，`activated_at = fulfilled_at`、`expires_at = fulfilled_at + 365 天`。前端只有在事务提交后才把音色放入付款用户的选择器，其他用户即使属于同一 tenant 也不能使用。
- `reject` 事务写入 `rejected_at`、管理员和拒绝原因，原子完成 `rejected + released`、必要的跨期退款 grant、billing operation completion 和 `AdminAuditLog`；创建订单不产生音色，续费音色继续保持过期。
- 如果 resolve 的数据库事务失败，积分保持冻结、订单保持等待；系统不尝试删除管理员已在豆包官方创建的音色。管理员修复输入后可用同一订单重试。
- 同一动作重放是幂等读取，相反动作并发时只有第一个提交成功；任何终态都不得再次改变余额。

人工订单明确排除在自动供应商 worker 和 stale reservation recovery 之外。用户删除已交付音色只做本地软删除且不退款；到期或删除后的豆包官方资源由管理员人工管理。

### 8.5 CosyVoice

- 创建品牌音色的报价应付为零，但仍走报价披露和幂等创建，防止重复建立远端音色。
- 选择 CosyVoice 品牌音色生成数字人或电商视频时，服务端按发送给供应商的规范化文本字符数计算 `0.1 积分/字`。规范化后的可计费文本在报价确认后持久化到父任务；估价、供应商调用和成本记录必须读取同一份文本，防止清洗差异导致报价漂移。
- 字符数包含正文与标点，忽略首尾空白；前端不得自行计数。
- 字符费进入父任务 breakdown，只随最终可交付视频成功结算；失败时随父任务释放。
- 不另外创建一笔重复的 CosyVoice 用户扣费记录；供应商成本记录仍可单独记 `cosyvoice-tts`。

同一段规范化文本的用户总价公式必须固定为：

- 预置音色：`父任务基础价 + 字符数 × 当前 tts/character 费率`（现有行为不变）；
- CosyVoice 品牌音色：`父任务基础价 + 字符数 × 当前 tts/character 费率`，只是将既有 TTS 分量明确披露为 CosyVoice 按量费，**不得再加第二份 0.1/字**；
- 豆包品牌音色：`父任务基础价 + 0 品牌音色字符费`，克隆音色使用权益包含在已支付的 365 天年费中。

所有 breakdown 精确小计之和必须等于 `subtotal_credits`，最终钱包取整后等于 `payable_credits`。

## 9. 错误、并发与恢复

### 9.1 全局锁顺序

会创建/激活/切换 active subscription、创建或应用退款 grant、或 resolve 人工订单的事务，必须先以 `SELECT ... FOR UPDATE` 锁定稳定存在的 `Tenant` 行；这是这些路径共享的 tenant 级互斥锁。其他不涉及订阅生命周期或退款 grant 的计费路径可以省略该锁，但一旦省略，本事务后续不得再反向取得 Tenant 锁。取得可选 Tenant 锁后，本轮涉及的其他锁统一使用：

1. `billing_operations`；
2. 本次涉及的 `subscriptions`，包括原冻结行和可能接收退款的当前行，按主键升序（原行即使已非 active 也必须锁）；
3. `usage_records`（多个时按主键升序）；
4. `credit_refund_grants`；
5. 若涉及 provider voice ID，取得统一的 `(provider, id)` advisory lock 后，再锁 `brand_voice_provider_ids` 和全部相关 `ProviderConfig`（按主键升序）；
6. 业务记录，如 `brand_voice_orders`、`video_tasks`、`brand_voices`、`assets`（同表多个时按主键升序）。

需要从 usage 找 subscription 时先做不加锁的 ID 查询，再按上述顺序取得 `FOR UPDATE` 锁。订阅激活和 grant 应用都在 Tenant 锁内按 grant 主键升序处理；人工订单 resolve 也在 Tenant 锁后才锁 operation。任何受影响代码不得反向持有业务行后再锁 subscription 或 Tenant。数据库死锁或可序列化冲突最多重试三次并加入抖动；供应商调用必须在锁事务之外，数据库重试不得重复调用供应商。

### 9.2 错误矩阵

| 场景 | 行为 |
|---|---|
| 估价失败或令牌过期 | 阻止提交；不预留、不调用供应商 |
| 请求或费率变化 | `PRICE_CHANGED`；不预留、不调用供应商 |
| 积分不足 | `TENANT_QUOTA_EXCEEDED`；不调用供应商 |
| 同 key 同请求重试 | 返回原操作状态或结果；不重复执行 |
| 同 key 不同请求 | `IDEMPOTENCY_KEY_REUSED`；不执行新调用 |
| 自动业务供应商失败、超时、空结果、非法结果 | 释放对应预留并记录可获得的供应商成本；不适用于豆包人工订单 |
| 电商批量部分成功 | 成功 item 结算，失败 item 释放 |
| 客户端断线但服务端成功 | 业务结果保持可查询并正常结算；客户端显示“计费结果确认中” |
| 豆包订单等待人工交付 | 保持 `reserved`，不调用供应商、不按时间自动释放 |
| 管理员交付豆包音色 | 绑定付款用户，原子结算并从交付时起算 365 天 |
| 管理员拒绝豆包订单 | 记录原因并完整释放原冻结积分 |
| 用户取消、删除待处理订单或关联材料 | `BRAND_VOICE_ORDER_NOT_CANCELLABLE`；保持订单与冻结不变 |
| 重复或并发 resolve | 同动作同内容幂等返回；冲突动作或内容返回 409 |
| 用户提前删除已交付豆包音色 | 本地软删除且不退款；豆包官方侧资源由管理员人工处理 |

所有 settle/release 都必须在订阅、usage record 行锁下幂等执行。已 `settled` 或 `released` 的记录再次处理时原样返回，不再次改变余额。

### 9.3 异常恢复

除豆包人工订单外，恢复任务按每种自动业务现有的权威超时配置扫描 stale reservation：

- 存在可向用户交付的成功结果：结算一次；
- 业务已失败或取消：释放；
- 已超过业务超时且没有持久化成功结果：释放，并尽力清理供应商残留；
- 仍在有效执行窗口：保持预留，等待下一轮。

自动业务不得无限期保留预留积分，也不得仅凭“供应商可能成功”向用户收费。

`doubao_brand_voice_order_create` 和 `doubao_brand_voice_order_renew` 是明确的人工例外：无论经过多少个恢复 cutoff，`awaiting_fulfillment` 都保持原 subscription 上的 reservation，不进入 stale 自动结算或释放。只有管理员 `fulfill/reject` 可以终结；跨期 reject 必须按 §7.3 生成或应用唯一退款 grant。订阅续订、换套餐或变为非 active 时不得复制、吞掉或转移这笔冻结；租户或付款用户停用、删除前，后台必须阻断并要求管理员先处理待办订单，不能静默释放。

## 10. 客户端设计

### 10.1 价格披露

- AI 口播文案和画面提示词在每次调用前显示服务端报价与确认按钮。
- 电商抠图和 AI 模特图显示单价、张数和总价；批量任务展示成功项按张结算说明。
- 豆包卡片显示 `30000 积分/个/365 天`、人工交付、到期时间、不自动续费和提前删除不退款。提交前确认框明确“先冻结，交付后结算；拒绝后解冻”。
- CosyVoice 卡片显示“创建免费，使用时 0.1 积分/字”；使用该音色的视频确认窗展示实际字符费明细。
- 前端删除 `DOUBAO_CLONE_CREDITS` 等权威价格常量，测试 fixture 只能模拟服务端响应，不能重新定义产品价格。

### 10.2 报价失效

用户修改任何与该业务请求绑定的输入、素材、数量、供应商或音色后，旧报价立即清除。确认处理函数还必须再次检查当前请求 hash 与报价一致，不能只依赖按钮 `disabled`。

### 10.3 失败文案

只有服务端明确返回 `released` 时，客户端才可用失败语义陈述“本次未扣费／冻结已释放”。唯一的成功零价例外是 `operation = cosyvoice_brand_voice_create && status = settled && requested_credits = 0`，此时显示“创建成功，本次创建免费（扣除 0 积分）”，不得说成失败释放。其他 settled 结果不能仅凭金额字段推断免费。网络断线、网关错误或缺少计费状态时统一显示“计费结果确认中”，并通过 operation 查询刷新状态和余额。

豆包订单专用文案按状态显示：

- `awaiting_fulfillment`：“已冻结 30000 积分，等待平台人工交付；订单不自动超时且无法取消”；
- `fulfilled`：“音色已交付并结算 30000 积分”，同时显示准确到期日；
- `rejected`：有当前订阅时显示“平台未能交付，冻结的 30000 积分已解冻”；没有当前订阅时显示“30000 退款积分已保留，将在下次套餐激活时自动到账”；同时显示经审查的拒绝原因。

等待状态不得显示“已扣款”“供应商生成中”或取消按钮；配额摘要单独显示跨期仍冻结的人工订单积分和待到账退款积分。用户尝试删除关联源音频或待续费音色时，界面解释必须先由平台处理订单。管理员本轮只要求受保护的队列读取和决策 API，不额外扩展复杂运营工作台。

### 10.4 延期入口

发布草稿继续隐藏；`seedance_t2v` 和 `static_template` 不新增前端价格展示；营销海报不恢复入口。

## 11. 数据库迁移与兼容

新增迁移必须：

1. 创建 `billing_operations` 及 `(tenant_id, user_id, operation, idempotency_key)` 唯一约束和查询索引；为 `usage_records` 增加 `billing_operation_id`、`billing_item_index`、`billing_pricing_line_index`、外键与关联索引；
2. 创建 `brand_voice_orders`、状态检查约束、`billing_operation_id` 唯一外键、待处理续费唯一索引及管理员队列索引；
3. 创建永久不可复用的 `brand_voice_provider_ids` 保护登记及 `(provider, provider_voice_id)` 唯一约束；创建以 `billing_operation_id` 唯一的 `credit_refund_grants`，支持 `pending | applied` 和 source/target subscription 审计；
4. 为 `brand_voices` 增加可空的 `owner_user_id`、`activated_at`、`expires_at`；保留现有供应商 `status` 约束，通过派生的 `delivery_status` 实施到期不可用语义；
5. 为待处理订单的 `source_audio_asset_id` 建立软删除/对象清理保护；Asset 行因审计外键永久保留，订单终态后才允许清理存储对象；
6. 扩展 `credit_rates`、`usage_records` capability 约束，并把 `brand_voice_order_audio_access | brand_voice_order_fulfill | brand_voice_order_reject` 加入 `AdminAuditLog.action` 约束；同时增加 `credit_rates.credits_per_unit >= 0`、`usage_records.quantity >= 0`、`usage_records.credits >= 0`、退款 grant 金额大于零，以及 §7.1 的 billing operation 非负、零价例外和终态守恒 CHECK。数值约束必须使用 PostgreSQL/SQLite 各自可验证的有限值保护，显式排除 Numeric `NaN` 和正负 Infinity，不能假设普通 `>= 0` 足以排除它们。迁移在加约束前只读扫描已有负数或非有限费率/用量并输出精确主键后失败，禁止自动改成零；
7. 幂等写入平台费率：
   - `script_generate/call = 1.0000`
   - `scene_prompt/call = 30.0000`
   - `image/image = 80.0000`
   - `voice_clone/call = 30000.0000`
   - `tts/character = 0.1000`
8. 保留正常的租户级 script、scene、image、tts 覆盖；
9. 保留历史租户 `voice_clone/call` 行但使豆包固定价解析器忽略它们；
10. 使用 compare-and-set 保护平台费率：新 capability 允许“缺行或已是目标值”；`image/image` 只接受已知旧值 `10` 或目标值 `80`；`voice_clone/call` 只接受已知旧值 `30` 或目标值 `30000`；`tts/character` 只接受目标值 `0.1`。其他值使迁移失败并要求人工审计，不静默覆盖未知生产配置；所有被本轮 `REQUIRE_POSITIVE` 策略实际读取的 active 平台/租户零价行也阻断迁移就绪检查和运行时报价，免费只能来自代码中显式的 CosyVoice 创建策略；
11. 迁移本身对现有 `brand_voices`、平台 `provider_configs.config.speaker_ids/used_speaker_ids` 和 `ENGINE_DOUBAO_VOICE_CLONE_SPEAKER_IDS` 执行零业务猜测：不创建订单、billing operation 或 usage record，不补扣积分，不写 `owner_user_id/activated_at/expires_at`，不默认 grandfather；
12. 启用豆包客户路由前生成只读存量清单。平台负责人确认的官方 ID 只写入保护登记，不触碰用户权益或账务；任何不在确认清单内的既有豆包 BrandVoice/slot 都阻断启用，等待单独决策。只有上线后由 `brand_voice_orders.fulfill` 产生的客户记录进入 365 天权益模型。

代码 fallback 必须与权威矩阵一致，至少包括：

- `avatar/second = 180`
- `video/second = 100`
- `video_gen/second = 100`
- `image/image = 80`
- `reverse_prompt/call = 100`
- `script_generate/call = 1`
- `scene_prompt/call = 30`
- `voice_clone/call = 30000`
- `tts/character = 0.1`

同一 capability 的估价、预留和结算不得各自重复写 fallback。

## 12. 测试设计

### 12.1 后端价格策略

- 每个策略的默认价、单位、数量和应付取整；
- 负费率、负数量、负小计、字段算术不一致和精度溢出全部 fail closed；收费策略命中 active 零费率也失败，只有固定的 CosyVoice 创建策略可产生合法零价；
- script、scene、image、tts 的租户覆盖优先级；
- 租户设置 `voice_clone/call = 42` 后豆包仍报价并冻结 `30000`，`fulfill` 后只结算 `30000`；
- CosyVoice 创建报价为零，使用报价为 `0.1/字`；
- CosyVoice 创建落 `cosyvoice_brand_voice_create`；需报价的视频落 `video_create`，TTS 只作为 breakdown，延期模式不创建该 operation；
- estimate、submit、hash 和报价门对 `effective_video_mode` 的解析完全一致；默认 `static_template + legacy voice/avatar fields` 以及 `seedance_t2v + legacy avatar fields` 都按实际 `avatar_talk` 分流，选择品牌音色时缺少报价头必须失败且零供应商调用；只有没有触发旧版 avatar 分流的真实延期模式可豁免；
- tenant/platform 数据库费率、code fallback 与 CosyVoice fixed zero 的 provenance 都能 token→snapshot→重放 round-trip；后两者 rate row 字段必须为 null 且不能伪造行，policy version 或默认值变化会触发 `PRICE_CHANGED`；
- simple 报价必须有顶层单位/数量/单价/scope/source；CosyVoice 视频的秒费 + 字符费使用 composite、顶层五字段为 null，每条 breakdown 自带独立 provenance 且混合 tenant/platform/code 来源仍精确求和；CosyVoice 创建的未来字符费只在 disclosures，绝不混入当前零价 subtotal；
- simple response 即使 breakdown 为空也生成恰一条 canonical pricing line；composite lines 与 breakdown 完全一致，token digest/snapshot 不会因展示形态丢失 capability、单位或 provenance；
- CosyVoice 创建确认后，snapshot 保存签名 disclosure 的已渲染文案/version/参考费率/provenance；随后调价或升级文案仍可还原原披露，且该数组始终不改变零价 subtotal 和账务；
- `/videos/estimate` 的 billing_quote、legacy_estimate、deferred_unpriced 三个 contract 互斥且与提交分支一致；只有品牌 billing_quote 有 token，新旧收费模式不伪造 operation，延期零估价不被当作合法零价 quote；
- 电商 batch 20 项可估价/提交且 quantity/item index 精确，21 项在两端都 422 且零账务；不同第 20 项产生不同 request/pricing hash，路由不存在静默 slice；
- 报价 token 的签名、tenant/user 隔离、请求绑定、过期和费率变化；20 项完整报价仍只把 pricing digest 放入 token，编码长度不超过 4096 bytes，超长 header fail closed。

### 12.2 后端资金闭环

- 供应商 mock 在被调用时断言 reservation 已提交；
- 余额不足、无报价、过期报价时断言供应商零调用；
- 成功只结算一次；失败、超时、空结果和 schema 错误全部释放；
- 内部重试不增加 reservation；
- 同一幂等键的并发请求只执行一次；
- 同 tenant 不同用户即使使用相同幂等键也互不泄露，operation 查询同时校验 user；
- operation lookup 对 in-progress、succeeded、rejected、failed 四类找到的记录都返回 200 和一致 billing schema，typed result/resource/failure 严格互斥；不存在与不可见均 404，失败 GET 只携带清洗后的 original HTTP status，不直接重放 5xx；
- 数据库锁下重复 settle/release 不改变余额；
- 电商批量 N 项的全成功、全失败和部分成功；
- usage 的 pricing line/item 索引与精确分配受校验：simple batch 多 item 共用 line 0，composite 视频按组件引用各 line，逐 line 分配和必须等于 snapshot；
- 数据库拒绝负 requested/settled/released、非法零价 operation 和不满足 `settled + released = requested` 的终态；小数批量 operation 与遗留未关联 reservation 同时存在时，按 parent requested 加 legacy 逐条 ceil 的公式精确对账，不能按子 usage 求和误判；
- 自动业务 stale recovery 的成功结算、失败释放、未超时保留；豆包人工订单跨越任意 cutoff 仍保持 reserved。

### 12.3 品牌音色

- 豆包订单提交后只冻结 `30000`，创建 `awaiting_fulfillment`；对豆包 provider 注册、槽位分配和删除 mock 均断言零调用；
- 无报价、余额不足或重复请求不创建订单；同 key 重试只返回同一订单和 reservation；
- 待处理订单跨越任意恢复 cutoff、订阅到期或换套餐仍绑定原 subscription 并保持冻结，不被复制、吞掉或自动释放；配额 API 正确显示跨期 hold；
- 用户取消、删除订单、删除待处理源音频、删除待续费音色和停用账号均被阻断；下单与素材删除并发时只有符合行锁后状态的一方成功；续费必须提交新的源音频和授权；
- 管理员 `fulfill` 先校验 operation/usage/snapshot/subscription hold 全部一致，再原子完成付款用户绑定、provider ID 保护登记、ready、settled、`fulfilled_at` 和准确 365 天到期；任一财务不一致或事务失败时仍保持等待和冻结并告警；
- 旧 voice-slot POST/CLI apply 均已 410/fail closed，平台 tenant 的非 admin 也不可读取或尝试写 slot；已交付客户 ID 再走旧入口、已有未使用 `speaker_ids` 再用于 fulfill、旧预留与 fulfill 并发三类测试都必须通过同一 ID 锁保证最多一方成功，失败方订单和资金保持原状；
- 管理员 `reject` 原子记录 `rejected_at` 和原因并释放全部冻结积分；原订阅过期时只创建一个可审计 refund grant，有当前订阅则立即到账，无当前订阅则下次激活只应用一次；
- `reject` 与新订阅激活并发时通过同一 Tenant 锁串行化：无论提交顺序如何，grant 最终要么在锁内直接 applied，要么被激活事务应用，不能出现 active subscription 已存在但 grant 永久 pending；无 active subscription 时配额 GET 仍以 200 返回零钱包、跨期 hold 和 pending refund；
- `fulfill/reject` 的重复、冲突和并发二选一；官方、未知占用或已登记给其他音色的 provider voice ID 被拒绝且不改变资金；软删除和换 ID 后旧 ID 仍不可交给他人，同一音色续费复用自己的 ID 允许；
- 仅平台管理员可读人工队列、短时源音频 URL 和 resolve，且每次决策写入 `AdminAuditLog`；跨租户、跨用户查询均返回不可见；
- 删除已交付音色只做本地软删除且不退款；到期不可选；未人工完成续费不得恢复；
- 续费同样先冻结，交付成功后才从该次 `fulfilled_at` 开始新的 365 天；拒绝则释放并保持过期；
- 现有平台官方自用音色和历史槽位数据零订单、零 usage、零补扣、零日期回填；官方账号保持原可用状态，客户账号不可见且管理员不能把其 ID 交付给客户；生产官方配置缺失/不一致或发现未知客户历史豆包记录时 fail closed；
- CosyVoice 创建不扣年费；CosyVoice TTS 随父任务按字结算；
- 豆包品牌音色不叠加 CosyVoice 字符费。

### 12.4 前端与端到端

- 所有金额来自 estimate response；生产组件内不存在权威价格常量；
- 估价加载、成功、失败、过期、价格变化状态；
- 估价失败时确认按钮和提交函数双重阻断；
- 修改输入后旧报价和金额立即消失；
- 重复点击和网络重试复用 operation id；
- `data.billing` 与 `error.detail.billing` 都按同一 schema 覆盖全成功、全释放、批量部分成功和零价成功：分别映射 `settled/released/partially_settled/settled`，终态 `held_credits` 恒为零且四个金额字段满足守恒；
- 共用视频 estimate/accepted 的 TypeScript 判别联合覆盖 billing/legacy/deferred；品牌收费分支 billing 必填，其他分支必须为空，不能把全局 optional 当作逃生口；
- 断网后按 operation lookup 从 pending 轮询到成功、拒绝或清洗后的失败，客户端不因 GET 200 误判业务成功，也不展示原始 502 内容；
- 手机、平板、桌面均能完整查看单价、数量、总价和失败说明；
- 网络中断时不谎报“未扣费”；
- released 失败显示“已释放”，合法 CosyVoice 零价 settled 成功显示“创建免费、扣除 0”，两者文案与状态不可混用；
- 豆包等待、交付、拒绝三种文案正确；等待状态无取消按钮，其他同 tenant 用户看不到付款用户的音色；
- 延期和已下线入口维持现状。

### 12.5 迁移测试

- 空数据库升级；
- 已有目标值重复升级；
- 合法租户覆盖保留；
- 意外平台值使迁移失败；
- 已有负数、NaN、正负 Infinity 费率/usage 数量/credits，或收费策略的 active 零费率，使迁移/就绪检查失败并报告具体记录；管理端保存、报价签发、快照结算同样 fail closed 且零账务/零供应商调用，只有合法 CosyVoice 零价 operation 可通过；
- 现有官方自用音色、provider slot 配置和历史 BrandVoice 不发生业务字段回填或账务写入；
- 待处理订单的源音频删除保护、状态检查和唯一约束在 SQLite/PostgreSQL 均生效；
- provider ID 保护登记永久不复用，跨期 refund grant 对 operation 唯一，迁移重复执行也不会重复登记或到账；
- 现有 `speaker_ids/used_speaker_ids` 全量进入存量审计，官方清单外记录阻断启用；迁移后没有任何通用 ProviderConfig 或 legacy slot 写入口可绕过 registry service；
- downgrade 不删除历史用户账目；
- SQLite 测试约束与 PostgreSQL 生产约束一致。

## 13. 定价工作台

实现完成并通过测试后，更新 `定价工作台.html`：

- AI 口播文案：`1 积分/次`，从“未定价”改为“价格完成，待上线”；
- 画面提示词：`30 积分/次`，从“未定价”改为“价格完成，待上线”；
- 电商抠图和 AI 模特图：保留 `80 积分/张`，从“待披露”改为“价格完成，待上线”；
- 豆包品牌音色：明确平台固定 `30000 积分/个/365 天`，并标注“先冻结、人工交付后结算”；
- CosyVoice：从“免费”改为“创建免费，使用时 0.1 积分/字”；
- `seedance_t2v`、`static_template`、发布草稿继续显示“延期处理／尚未闭环”；
- 营销海报继续显示“已下线”。

工作台的统计必须由更新后的条目计算，不手写总数；完成桌面和手机视图验证，控制台不得有错误。

## 14. 生产上线门禁

本设计和后续代码合并都不代表生产价格生效。生产操作必须另获用户明确批准，并按顺序完成：

1. 只读清点生产 migration revision、ENV、平台费率和所有租户覆盖；
2. 导出受影响费率、订阅余额、现有 BrandVoice、平台官方自用音色与 slot 配置快照，由平台负责人确认官方数据不迁移、不收费，并据此填写精确的 `ENGINE_DOUBAO_OFFICIAL_VOICE_IDS`；任何未知归属的既有豆包记录先阻断上线；
3. 核对新迁移对当前生产值不会产生意外覆盖，验证存量音色的 `owner_user_id/activated_at/expires_at` 仍为空，并确认官方配置与 provider ID 保护登记完全一致；
4. 同批部署后端、前端和迁移，避免前后端价格窗口；
5. 逐功能验收估价、预留、成功实扣、失败释放；
6. 真栈验收豆包下单只冻结且运行时没有注册网络调用，管理员队列可读取源音频，`fulfill/reject` 分别正确结算/释放，并验证历史 provider ID 不可复用；
7. 校验重复请求不双扣、人工订单不被 stale recovery 释放、跨订阅 reject 正确生成/应用退款 grant、自动业务无 stale reservation；
8. 核对 usage ledger、订阅余额与供应商成本账；
9. 通过后才把工作台状态从“待上线”改为“已上线”。

任何门禁失败都应停止上线，不得使用手工改库绕过迁移保护。

## 15. 预计涉及的主要文件

后端：

- `backend/app/services/quota.py`
- 新的价格策略/报价服务模块
- `backend/app/db/models.py`
- `backend/app/api/v1/routes/scripts.py`
- `backend/app/api/v1/routes/videos.py`
- `backend/app/api/v1/routes/ecom_images.py`
- `backend/app/api/v1/routes/brand_voices.py`
- `backend/app/api/v1/routes/voices.py` 及视频音色解析服务
- `backend/app/api/v1/routes/admin_console.py`
- 新的用户 `brand_voice_orders` 与管理员人工队列/resolve 路由、service 和 schemas
- `backend/app/services/admin_console.py` 或等价的审计写入复用点
- `backend/app/services/voice_slots.py` 与现有 admin voice-slot route（改为受双重权限保护的只读/HTTP 410 退役面）
- 统一的 provider-ID registry/锁服务，供人工 fulfill、续费换 ID、官方登记和所有允许的配置迁移复用
- 配额查询与订阅激活流程，用于跨期 hold 和 pending refund grant
- 对应 models、迁移、provider ID 保护登记、素材删除保护和恢复任务

前端：

- `frontend/src/lib/api/scripts.ts`
- `frontend/src/lib/api/videos.ts`
- `frontend/src/lib/api/ecom-images.ts`
- `frontend/src/lib/api/brand-voices.ts`
- 文案、画面提示词、电商图片与品牌音色的确认组件
- 豆包人工订单列表、状态文案和付款用户专属音色过滤；本轮不要求新增复杂管理员 UI
- mocks、单元测试和 e2e 测试

本列表是边界说明，不授权与计费闭环无关的重构。

## 16. 完成标准

- 六个已确认计价项均由服务端权威报价；
- 前端显示金额与实际钱包扣除额一致；
- 任何收费调用都在供应商调用前完成预留；
- 成功最多结算一次，失败不扣，部分成功按成功数量结算；
- 豆包 `30000` 不受任何租户覆盖影响；
- 豆包用户下单只冻结 `30000`，系统不调用注册接口；管理员交付后才绑定付款用户、结算并起算 365 天，拒绝则完整解冻；
- 待人工订单不超时、用户不可取消，源音频和续费对象在终态前受删除保护；
- 人工订单跨订阅周期仍可准确结算；拒绝时冻结价值立即恢复或形成只到账一次的待生效退款；
- 官方及历史客户 provider voice ID 永不误分配，已交付 ID 在软删除或换 ID 后也不复用；
- 旧 tenant voice-slot 和任何 ProviderConfig 写入口不能绕过统一 ID registry；同一 provider ID 的预留、官方登记与人工交付全局互斥；
- 平台官方自用音色及历史槽位数据不迁移、不收费、不设置用户权益时间；
- CosyVoice 创建免费、使用按 `0.1/字` 且不重复扣费；
- 所有金额均非负且终态资金守恒；除显式 CosyVoice 创建外不存在由零/负费率产生的免费调用；
- 豆包年费具备交付生效、到期、删除不退款和人工续期语义；
- 所有旧低价 fallback 与旧测试已清理；
- 延期和已下线功能没有被误改；
- 自动化测试、迁移测试和工作台可视化验证全部通过；
- 未经单独批准，没有执行任何生产变更。

## 17. 批准记录

用户已在 2026-08-28 逐项确认：价格矩阵、租户覆盖范围、豆包与 CosyVoice 的差异化计价、统一价格策略方案、报价数据流、失败与并发语义、测试和生产门禁；并进一步确认豆包采用“用户付款先冻结 30000 → 平台负责人在豆包官方手工注册 → 管理员交付后结算或拒绝后解冻”的人工订单模型。平台官方自用音色与用户账号无关，保持原状。
