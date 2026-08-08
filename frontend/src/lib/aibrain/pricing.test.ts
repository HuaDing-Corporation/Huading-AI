import { describe, expect, it } from "vitest";

import {
  MAX_COMPLETION_TOKENS,
  PROMPT_RATE_TIER_THRESHOLD_TOKENS,
  TIERS,
  TIER_ORDER,
  TYPICAL_COMPLETION_TOKENS,
  TYPICAL_PROMPT_TOKENS,
  formatCreditsExact,
  formatCreditsUp,
  formatRate,
  minReservationCredits,
  outstandingView,
  precheckSend,
  rateForPromptTokens,
  shortfallView,
  typicalCredits
} from "./types";

// ── PRICING-UI-0001 §二/§三 · 智脑价格展示承重 ────────────────────────────────────────────────
// 本包修的原始缺陷：`TIERS[tier].typical` 是写死的 6/15/30，没有一个字说明它是「500 输入 + 500 输出
// 的估算值」。BE 把费率降了 35% 之后（1.73/10.37 → 1.12/6.72），UI 静默错价，而**没有任何测试会红**
// —— 因为当时的测试也只是把 6/15/30 抄了一遍。
//
// 所以这里的门必须同时钉住三样东西，缺一样都能被"改一个常量"绕过：
//   门1 费率**逐个对齐 BE config**（价格的真源）
//   门2 展示值是**新费率下的值**，且**旧值不许再出现**（防止有人为了让测试绿而把常量改回去）
//   门3 预留下界 = 4096 × 输出费率（真值 27.52512 / 68.8128 / 137.6256；§三 表格里写的是它们被舍入的低报版）
// 三条落在不同断言上，互不塌缩。

describe("智脑分档费率（价格真源，镜像 BE config.py）", () => {
  /**
   * 🔴 门1：费率**逐字**对齐 `backend/app/core/config.py`
   * `engine_aibrain_{tier}_{input,output}_credits_per_1k`（PR #239 `codex/pricing-c3c4-be`:211-216）。
   * 变异：任意一档费率写错 → 本条红。
   * ⚠️ 这是**镜像**不是推导——BE 没有暴露费率的接口（钱包响应只有余额/预留/充值档位），前端要在
   *    「发送之前」给用户预期就只能镜像一份。故这条门的职责是：BE 改费率时逼前端一起改，而不是
   *    让前端自己算出费率。已写进回执建议 CA 把费率随钱包一起下发，届时这条门可以换成"读接口"。
   */
  it("门1：三档**低区间**费率逐个等于 BE config 的值（1.12/6.72 · 2.80/16.80 · 5.60/33.60）", () => {
    expect(TIERS.low.rate.standard).toEqual({ inputPer1k: 1.12, outputPer1k: 6.72 });
    expect(TIERS.mid.rate.standard).toEqual({ inputPer1k: 2.8, outputPer1k: 16.8 });
    expect(TIERS.high.rate.standard).toEqual({ inputPer1k: 5.6, outputPer1k: 33.6 });
  });

  // ══ PRICING-UI-0002 · 十二格 + 区间判据 ═════════════════════════════════════════════════
  // §三 的要求：断言前端费率表与后端契约**逐格一致**；做不到（前端拿不到后端费率、且售价侧
  // 双区间尚未落地）就**至少断言两个区间存在且高区间 = 低区间的「输入 ×2 / 输出 ×1.5」**。
  // 🔴 这个比例**不是整体翻倍** —— 照"双倍"写会把输出多算 33%。这正是下面那条门存在的理由。
  // ✅ 比例本身有源码依据：BE **成本侧** `apimart_token_pricing.py`（已在 develop）的 272K 双区间，
  //    三档一律 输入 8→16 / 20→40 / 40→80（×2）、输出 48→72 / 120→180 / 240→360（×1.5）。

  /**
   * 🔴 十二格逐格钉死。变异：任意一格写错 → 本条红。
   * 断言**字面量**而不是 `standard.x * 2`：后者会跟着实现一起漂（把 extended 写成
   * `standard×2` 的派生值时，期望也变成同一个错值，测试自我抵消 —— 本项目在 #228 栽过这个坑）。
   */
  it("🔴 门1b：十二格逐格钉死（高区间 2.24/10.08 · 5.60/25.20 · 11.20/50.40）", () => {
    expect(TIERS.low.rate.extended).toEqual({ inputPer1k: 2.24, outputPer1k: 10.08 });
    expect(TIERS.mid.rate.extended).toEqual({ inputPer1k: 5.6, outputPer1k: 25.2 });
    expect(TIERS.high.rate.extended).toEqual({ inputPer1k: 11.2, outputPer1k: 50.4 });
  });

  /**
   * 🔴🔴 §三 点名的那道门：**输入 ×2、输出 ×1.5**，逐档验证。
   * 变异：把任一档的 extended 写成"整体翻倍"（输出也 ×2）→ 本条红。
   * 与门1b 职责分离：1b 管"值对不对"，本条管"两区间的**关系**对不对"——后者才是
   * `PRICING-AIBRAIN-TIER-0001` 的正确性判据（每档两区间加价率相等）在前端的投影。
   */
  it("🔴 门1c：高区间 = 低区间的「输入 ×2 / 输出 ×1.5」（**不是整体翻倍**）", () => {
    for (const tier of TIER_ORDER) {
      const { standard, extended } = TIERS[tier].rate;
      expect(extended.inputPer1k).toBeCloseTo(standard.inputPer1k * 2, 6);
      expect(extended.outputPer1k).toBeCloseTo(standard.outputPer1k * 1.5, 6);
      // 🔴 显式否掉"输出也翻倍"这个最容易犯的错。
      expect(extended.outputPer1k).not.toBeCloseTo(standard.outputPer1k * 2, 6);
    }
  });

  /**
   * 🔴 区间判据只有一个实现，且边界是**严格大于**（BE `up_to_272k` 的上界含 272,000）。
   * 变异：把 `>` 写成 `>=` → 本条红（阈值那一格会跳到高区间）。
   */
  it("🔴 门1d：阈值 272,000 是显式常量，且恰在阈值上仍走低区间（严格大于才进高区间）", () => {
    expect(PROMPT_RATE_TIER_THRESHOLD_TOKENS).toBe(272_000);
    expect(rateForPromptTokens("low", 0)).toEqual(TIERS.low.rate.standard);
    expect(rateForPromptTokens("low", 272_000)).toEqual(TIERS.low.rate.standard); // 恰在阈值 → 低区间
    expect(rateForPromptTokens("low", 272_001)).toEqual(TIERS.low.rate.extended); // 超一个 token → 高区间
    expect(rateForPromptTokens("high", 500_000)).toEqual(TIERS.high.rate.extended);
  });

  /**
   * 🔴 门2：「约 N 积分/次」是**新费率**下的典型值，且**旧的 6/15/30 一个都不许再出现**。
   * 变异A：把 `typicalCredits` 改成写死 6/15/30（即回到本包修复前的状态）→ 前半段红。
   * 变异B：把口径从 500+500 改成别的（比如 1000+1000）→ 数值变 8/20/39 → 前半段红。
   * 🔴 为什么断言**字面量 4/10/20** 而不是 `Math.round((500*rate.in + 500*rate.out)/1000)`：
   *    后者会跟着实现一起漂 —— 实现改错公式时期望值也跟着错，测试自我抵消、变异抓不到。
   *    （本项目在 #228 图片计费门上栽过一模一样的坑，那次是 `copy.imageChargeMessage(credits)`。）
   */
  it("门2：典型值 = 4 / 10 / 20（新费率），且旧的 6 / 15 / 30 已不复存在", () => {
    expect(typicalCredits("low")).toBe(4);
    expect(typicalCredits("mid")).toBe(10);
    expect(typicalCredits("high")).toBe(20);
    const shown = [typicalCredits("low"), typicalCredits("mid"), typicalCredits("high")];
    expect(shown).not.toContain(6);
    expect(shown).not.toContain(15);
    expect(shown).not.toContain(30);
  });

  it("门2b：口径常量就是「500 输入 + 500 输出」——展示这个数时必须能说清它是怎么来的", () => {
    expect(TYPICAL_PROMPT_TOKENS).toBe(500);
    expect(TYPICAL_COMPLETION_TOKENS).toBe(500);
  });

  /**
   * 🔴🔴 PRICING-UI-0002 §二.2 的硬要求：**扩区间不许意外改动这两个推导值**。
   * 这是本包最容易出的错——给 `TierRate` 加一层之后顺手把 `typicalCredits` 改成读 `extended`，
   * 或把 `minReservationCredits` 改成取两区间的最大值，用户看到的数字就全变了。
   * 变异A：`typicalCredits` 改读 `extended` → 前半段红（会变成 6/15/30 —— 恰好是本包一开始
   *        修掉的那三个旧数字，讽刺但真实：2.24×0.5+10.08×0.5 = 6.16 ≈ 6）。
   * 变异B：`minReservationCredits` 改读 `extended` → 后半段红
   *       （27.52512→41.28768 / 68.8128→103.2192 / 137.6256→206.4384；旧注释写的 27.5→41.3 两边都是舍入值）。
   */
  it("🔴 门2c：扩区间后 typicalCredits / minReservationCredits **值一个都没变**", () => {
    // 500 输入远小于 272,000 → 必落低区间 → 仍是 4 / 10 / 20。
    expect([typicalCredits("low"), typicalCredits("mid"), typicalCredits("high")]).toEqual([4, 10, 20]);
    // 下界按低区间算（语义是「至少」，用高区间会把上界说成下界）→ 仍是 27.52512 / 68.8128 / 137.6256。
    // 🔴 这里钉的是**真值**不是展示值：本条门的语义是"扩区间后值没变"，那是数值的性质；
    //    展示值怎么格式化由 §展示格式 那一节守，两者不该纠缠在一条门里。
    // ⚠️ 这条断言原本写的是 `formatCredits(...) === "27.5"` —— 既用了 FIX6 已删除的函数，
    //    又把**低报的展示值**当成了"值"。27.5 不是下界，27.52512 才是（差额让用户充完仍发不出去）。
    expect(minReservationCredits("low")).toBeCloseTo(27.52512, 5);
    expect(minReservationCredits("mid")).toBeCloseTo(68.8128, 4);
    expect(minReservationCredits("high")).toBeCloseTo(137.6256, 4);
  });

  /**
   * 🔴 `typicalCredits` 的口径（500 输入）**必须落在低区间**——这是上一条"值不变"的**原因**，
   * 单独钉一条：万一哪天阈值被调到 500 以下，上一条会红但看不出为什么，这条直接指出原因。
   */
  it("门2d：典型对话的 500 输入落在低区间（这是 4/10/20 不变的原因）", () => {
    expect(TYPICAL_PROMPT_TOKENS).toBeLessThanOrEqual(PROMPT_RATE_TIER_THRESHOLD_TOKENS);
    expect(rateForPromptTokens("low", TYPICAL_PROMPT_TOKENS)).toEqual(TIERS.low.rate.standard);
  });

  /**
   * 🔴 门3：预留下界 = `max_completion_tokens × 输出费率` = **27.52512 / 68.8128 / 137.6256**。
   * ⚠️ §三 表格里写的是 27.5 / 68.8 / 137.6 —— **那是舍入后的低报值，不是下界**。
   * 这个数是 402 提示的核心（用户会看到它被"扣住"），算错就等于对用户报错价。
   * 变异：把 `MAX_COMPLETION_TOKENS` 改成别的、或下界改用输入费率 → 本条红。
   */
  it("门3：预留下界 = 4096 × 输出费率 → 真值 27.52512 / 68.8128 / 137.6256，展示 27.6 / 68.9 / 137.7", () => {
    expect(MAX_COMPLETION_TOKENS).toBe(4096);
    // 🔴 先钉**真值**（费率推导的结果），再钉展示值 —— 两者分开，格式化换实现时不会连累费率门。
    expect(minReservationCredits("low")).toBeCloseTo(27.52512, 5);
    expect(minReservationCredits("mid")).toBeCloseTo(68.8128, 4);
    expect(minReservationCredits("high")).toBeCloseTo(137.6256, 4);
    // 🔴🔴 下界是**缺口类**（"至少得留住这么多"）→ `formatCreditsUp`。
    //    FIX6 之前这三个位置显示的是 27.5 / 68.8 / 137.6 —— 任务包 §三 那张表抄的就是这组**低报值**，
    //    用户照着 27.5 充值仍然凑不齐 27.52512 的预留。这是本轮方向修复**真实改变界面数字**的地方。
    expect(formatCreditsUp(minReservationCredits("low"))).toBe("27.6");
    expect(formatCreditsUp(minReservationCredits("mid"))).toBe("68.9");
    expect(formatCreditsUp(minReservationCredits("high"))).toBe("137.7");
  });

  /** 🔴 §三 的用户体验前提：被扣住的数**远大于**实际花费——这正是必须解释「临时预留」的原因。 */
  it("门3b：预留下界 ≫ 典型消耗（high 档 137.6256 vs 20，约 7 倍）——不解释清楚会被当成一次对话的花费", () => {
    expect(minReservationCredits("high")).toBeGreaterThan(typicalCredits("high") * 5);
    expect(minReservationCredits("low")).toBeGreaterThan(typicalCredits("low") * 5);
  });
});

/**
 * 「余额偏低」的阈值（wallet-balance.tsx `LOW_BALANCE`）此前是 `TIERS.high.typical`（30）——
 * 一个估算值；本包换成 `minReservationCredits("low")`（**27.52512**），对应一条**硬边界**：低于它，
 * 连最低档都凑不齐一次预留、发送必被 402 拒。数值相近（视觉几乎不变），含义从"估算"变"事实"。
 * 变异：改回按 high 档取（137.6256）→ 本条红（只用低档的用户会长期看到告警）。
 */
describe("低余额阈值", () => {
  it("低余额阈值 = 最低档的预留下界（27.52512），不是高档的也不是旧的典型值（30）", () => {
    // ⚠️ 这里是**数值比较**的阈值（wallet-balance 的 LOW_BALANCE），不是展示值 —— 用真值断言。
    expect(minReservationCredits("low")).toBeCloseTo(27.52512, 5);
    expect(minReservationCredits("low")).toBeLessThan(minReservationCredits("high"));
    expect(minReservationCredits("low")).not.toBe(30);
  });
});

// ── FIX1 · 402 结构化 detail（BE #239 `e2bc2c02`）────────────────────────────────────────────
// 契约变更史见 types.ts 的注释。这里的门分成两组，**互不塌缩**：
//   「精确态」组：detail 在 → 读 BE 的数、exact=true（文案不带「至少」）
//   「回退态」组：detail 缺/形状不符 → 退回下界、exact=false（文案带「至少」）
// 🔴 回退那组是 FIX1 明确要求钉住的：BE 哪天不发 detail（回滚 / 新增第三种 402 忘了带），
//    UI 必须退回「至少 X」而不是什么都不显示 —— 没有这组门，那种退化是**静默**的。

describe("shortfallView · 精确态（BE 给了 detail）", () => {
  /** BE 真实形状（aibrain.py:578-583）。 */
  const DETAIL = {
    required_credits: 137.6256,
    available_credits: 20,
    shortfall_credits: 117.6256,
    temporary_reservation: true
  };

  /**
   * 🔴 三个数**全部来自 BE**，不是前端算的。
   * 变异：`shortfallView` 忽略 detail 参数（回到 FIX1 之前只用下界的版本）→ 本条红
   *       （exact 会变 false；且若 BE 的 required 与下界恰好相等也仍能抓到，因为 exact 断言独立）。
   */
  it("detail 在 → required/available/shortfall 全取 BE 的值，exact=true", () => {
    const v = shortfallView("high", 999, DETAIL);
    expect(v.exact).toBe(true);
    expect(v.required).toBe(137.6256);
    expect(v.available).toBe(20); // 🔴 取 BE 的 20，**不是**传进去的钱包值 999
    expect(v.shortfall).toBe(117.6256);
  });

  /**
   * 🔴 BE 的 required 与前端下界**不同**时，必须以 BE 为准（下界只算 completion 段，
   * BE 的精确值还含提示词那一段，恒 ≥ 下界）。变异：`required` 仍用 `minReservationCredits` → 本条红。
   */
  it("BE 的 required 高于前端下界 → 显示 BE 的（下界只是 completion 段，不含提示词）", () => {
    const v = shortfallView("high", 20, { ...DETAIL, required_credits: 300, shortfall_credits: 280 });
    expect(v.required).toBe(300);
    expect(v.required).toBeGreaterThan(minReservationCredits("high"));
  });

  /** 防御：BE 若给出 shortfall=0（不该发生），也不许显示「还差 0 积分」。 */
  it("BE 的 shortfall 为 0 → 不给差额（绝不显示「还差 0 积分」）", () => {
    const v = shortfallView("high", 20, { ...DETAIL, shortfall_credits: 0 });
    expect(v.shortfall).toBeUndefined();
  });
});

describe("shortfallView · 回退态（BE 没给 detail / 形状不符）", () => {
  /**
   * 🔴 FIX1 点名要钉的回退：detail 缺失时退回下界 + exact=false。
   * 变异：去掉 `shortfallView` 里 `if (parsed)` 之后的整段回退（直接返回 detail 的值）→ 本条红
   *       （undefined detail 会炸或给出 NaN）。
   */
  it("detail 缺失（如预检拦截根本没发请求）→ 回退到下界，exact=false", () => {
    const v = shortfallView("high", 20);
    expect(v.exact).toBe(false);
    expect(v.required).toBeCloseTo(137.6256, 4);
    expect(v.shortfall).toBeCloseTo(117.6256, 4);
  });

  /**
   * 🔴 **半份 detail 不许拼**：BE 若只给了 required 没给 shortfall，拼出来的组合最误导。
   * 变异：`parseInsufficientDetail` 改成缺字段就用 0 兜底 → 本条红（exact 会变 true）。
   */
  it("detail 形状不符（缺字段 / 非对象 / 字段非数字）→ 一律当没有，走回退", () => {
    expect(shortfallView("high", 20, { required_credits: 300 }).exact).toBe(false);
    expect(shortfallView("high", 20, { required_credits: "300", available_credits: 20, shortfall_credits: 280 }).exact).toBe(false);
    expect(shortfallView("high", 20, "nonsense").exact).toBe(false);
    expect(shortfallView("high", 20, null).exact).toBe(false);
  });

  /**
   * 🔴 钱包也未加载时**不填 0 冒充**：显示「当前可用 0 积分」而实际有余额，是在对用户撒谎。
   * 变异：把 `available` 的 undefined 兜底成 0 → 本条红。
   */
  it("钱包也未加载 → available/shortfall 均 undefined（不拿 0 冒充「没钱」）", () => {
    const v = shortfallView("high", undefined);
    expect(v.available).toBeUndefined();
    expect(v.shortfall).toBeUndefined();
  });

  /**
   * 🔴 回退态下余额**高于**下界却仍被 402（缺口来自提示词那一段，前端算不出）→ shortfall 必须
   * undefined，好让 UI 换一句话说。变异：改成 `Math.max(0, required - available)` → 本条红（得到 0），
   * UI 就会显示「至少还差 0 积分」这种荒谬值。
   */
  it("回退态余额高于下界仍被拒 → shortfall 为 undefined（绝不显示「还差 0 积分」）", () => {
    const v = shortfallView("high", 500);
    expect(v.available).toBe(500);
    expect(v.shortfall).toBeUndefined();
  });
});

describe("outstandingView · 欠费（402 AIBRAIN_OUTSTANDING_BALANCE）", () => {
  /** BE 真实形状（aibrain.py:564-567）：available 为负、outstanding 为其相反数。 */
  it("detail 在 → 取 BE 的 outstanding / available（available 是负数）", () => {
    const v = outstandingView({ available_credits: -42.5, outstanding_credits: 42.5 });
    expect(v?.outstanding).toBe(42.5);
    expect(v?.available).toBe(-42.5);
  });

  /**
   * 🔴 回退：钱包的 available 现在**可以为负**（BE 删掉了 `next_available < 0` 断言），
   * 负余额本身就是欠款额，是可靠的第二来源。
   * 变异：删掉 `outstandingView` 的钱包回退分支 → 本条红。
   */
  it("detail 缺失但钱包余额为负 → 用负余额反推欠款额", () => {
    const v = outstandingView(undefined, -30);
    expect(v?.outstanding).toBe(30);
    expect(v?.available).toBe(-30);
  });

  /**
   * 🔴 两个来源都没有 → **返回 undefined**，UI 只给定性文案，**不编数字**。
   * 变异：无来源时兜底成 `{ outstanding: 0 }` → 本条红（UI 会显示「需补齐 0 积分」）。
   */
  it("detail 与钱包都拿不到 → undefined（不编数字）", () => {
    expect(outstandingView(undefined, undefined)).toBeUndefined();
    expect(outstandingView(null, 0)).toBeUndefined(); // 余额 0 不是欠费
    expect(outstandingView({ nope: 1 }, 5)).toBeUndefined(); // 正余额也不是欠费
  });
});

// 原 `precheck.test.ts` 的两条用例并入此处（那边断言 `precheckSend(-3) → insufficient`，
// 契约变更后已经是错的）。合并到单点是为了避免同一行为在两个文件里各有一套断言 ——
// 那种重复迟早会一边改一边不改，剩下的那份就成了给旧契约站岗的门。
describe("precheckSend · 两种情形要分开（对齐 BE reserve 分支的判定次序）", () => {
  /**
   * 🔴 负余额 → outstanding（**不是** insufficient）。BE 把 `available < 0` 判在最前（aibrain.py:557），
   * 前端照抄次序，否则欠费用户会收到「这是临时预留、结束会退回」——他上次的钱早就退不回来了。
   * 变异：把 `availableCredits < 0` 那条去掉（回到只有 `<= 0` 的单一 insufficient）→ 本条红。
   */
  it("余额为负 → reason=outstanding（欠费，不是预留不足）", () => {
    expect(precheckSend(-1)).toEqual({ ok: false, reason: "outstanding" });
    expect(precheckSend(-137.6)).toEqual({ ok: false, reason: "outstanding" });
  });

  it("余额恰为 0 → reason=insufficient（任何正数预留都不满足，必 402）", () => {
    expect(precheckSend(0)).toEqual({ ok: false, reason: "insufficient" });
  });

  /** 正余额一律放行——前端算不出精确预留，拿下界去拦会错杀上下文短的用户。 */
  it("正余额 / 钱包未加载 → 放行，由 BE 裁决", () => {
    expect(precheckSend(1)).toEqual({ ok: true });
    expect(precheckSend(500)).toEqual({ ok: true });
    expect(precheckSend(undefined)).toEqual({ ok: true });
  });
});

// ══ FIX6 · P1-1：格式化按语义**分成两个函数**，各守各的性质 ════════════════════════════════
// 演进：① 固定 toFixed(1)（0.02→"0"，FIX5 修）→ ② 逐级提升精度（守住"非零"，**没守住方向**）
//      → ③ 现在：缺口类向上、实扣类如实。
//
// 🔴 FIX5 那条 `formatCredits(137.6256) === "137.6"` 是**第七次「测试给缺陷站岗」**：
//    它是我为了修"显示零"而**新写**的门，正确地钉住了"不显示零"，却在同一处放过了"方向"
//    —— 137.6256 的真实语义是"至少需要"，显示 137.6 会让用户充 137.6 之后**仍然发不出去**。
//    **一道门只能守它断言的那件事。** 写门时要问的是「我漏掉了这个值的哪个属性」。
//    下面因此拆成两组断言：**性质门（非零 / 符号）** 与 **方向门（≥ / ==）**，互不塌缩。

describe("展示格式 · 缺口类 formatCreditsUp（还得再拿出多少）", () => {
  /**
   * 🔴🔴 **方向门**（本轮新增，FIX5 缺的就是它）：显示值恒 **≥** 真实值。
   * 变异：把 `Math.ceil` 改成 `Math.round` 或 `Math.floor` → 本条红
   *       （137.6256→"137.6" / 27.52512→"27.5"，低报"至少需要"）。
   * 变异：把这里改回调用 `formatCreditsExact` → 本条仍绿（如实展示也 ≥ 真实值），
   *       但下一条"进位到 1 位"会红 —— 两条合起来才钉死实现。
   */
  it("🔴 方向：任意正缺口的显示值 ≥ 真实值（宁可多说，用户照着充一定够）", () => {
    const samples = [137.6256, 27.52512, 117.6256, 0.24192, 0.02, 0.001, 0.004, 1e-7, 27.5, 68.8, 20, 0.05];
    for (const value of samples) {
      expect(Number(formatCreditsUp(value))).toBeGreaterThanOrEqual(value);
    }
  });

  /**
   * 🔴 CB 点名的两个具体值（**这是被重写的那条断言**：`137.6256` 原来锁 `"137.6"`）。
   * 变异：任何非向上的舍入 → 本条红。
   */
  it("🔴 137.6256 → \"137.7\"（不是 \"137.6\"）· 27.52512 → \"27.6\"（不是 \"27.5\"）", () => {
    expect(formatCreditsUp(137.6256)).toBe("137.7");
    expect(formatCreditsUp(27.52512)).toBe("27.6");
    // 恰好落在 1 位小数上的值不被推高（否则每个整洁的数都会凭空 +0.1）
    expect(formatCreditsUp(27.5)).toBe("27.5");
    expect(formatCreditsUp(68.8)).toBe("68.8");
    expect(formatCreditsUp(20)).toBe("20");
  });

  /**
   * 🔴 **浮点噪声不许把显示值推高一档**。
   * ⚠️ 这条门第一版写错了：我拿 `68.8 / 137.6 / 1.1` 当样本，以为 `x * 10` 会有尾差 ——
   *    实测**任何 1 位小数 × 10 都精确**（0.1..200 全扫一遍，零命中），所以那一版删掉
   *    `toFixed(6)` 也**不会红**，等于给一段没人守的代码写了张假证明。
   *    真正的来路是**减法**：`0.4 - 0.1 === 0.30000000000000004` → `*10` 得 3.0000000000000004
   *    → `Math.ceil` 得 4 → 显示 `0.4`，凭空多报 0.1。
   * 🔴 而这条减法路径是**真实存在**的：`shortfallView` 回退态就是 `required - available`
   *    （见本文件「回退态余额高于下界」那组门）。
   * 变异：删掉 `Number((credits * 10).toFixed(6))` 里的 `toFixed(6)` → 本条红。
   */
  it("🔴 浮点噪声：减法产生的 0.30000000000000004 显示 0.3，不许被推成 0.4", () => {
    expect(0.4 - 0.1).not.toBe(0.3); // 先证明噪声真的在（否则这条门什么也没守）
    expect(formatCreditsUp(0.4 - 0.1)).toBe("0.3");
    expect(formatCreditsUp(0.30000000000000004)).toBe("0.3");
    // 常规值不受影响
    expect(formatCreditsUp(68.8)).toBe("68.8");
    expect(formatCreditsUp(137.6)).toBe("137.6");
    expect(formatCreditsUp(1.1)).toBe("1.1");
  });

  /** 🔴 FIX5 的性质门在这一类仍然有效（向上取整天然保证，但要钉住它不被绕过）。 */
  it("🔴 非零不许显示为零（0.02 / 0.001 / 0.004 / 1e-7 一律进位到 0.1）", () => {
    expect(formatCreditsUp(0.02)).toBe("0.1");
    expect(formatCreditsUp(0.001)).toBe("0.1");
    expect(formatCreditsUp(0.004)).toBe("0.1");
    expect(formatCreditsUp(1e-7)).toBe("0.1");
  });

  it("零仍然显示 0（判据只管非零值）", () => {
    expect(formatCreditsUp(0)).toBe("0");
  });
});

describe("展示格式 · 实扣/余额类 formatCreditsExact（实际发生 / 现在有多少）", () => {
  /**
   * 🔴🔴 **方向门**：显示值 **等于**真实值（BE 精度六位以内）。
   * 变异：把实现改成 `toFixed(1)` 或改成调用 `formatCreditsUp` → 本条红
   *       （0.24192 会变 "0.2"/"0.3"，都不等于真值）。
   */
  it("🔴 方向：六位以内的值原样还原（不许舍入 —— 这是已发生的事实，不是估计）", () => {
    const samples = [137.6256, 0.24192, 27.52512, 0.02, 0.001, 0.000001, 20, 27.5, -42.5, -0.02];
    for (const value of samples) {
      expect(Number(formatCreditsExact(value))).toBe(value);
    }
  });

  /** 🔴 CB 点名的两处：`message-bubble` 的实扣、`wallet-balance` 的余额。 */
  it("🔴 0.24192 → \"0.24192\"（不是 \"0.2\"）· 137.6256 → \"137.6256\"", () => {
    expect(formatCreditsExact(0.24192)).toBe("0.24192");
    expect(formatCreditsExact(137.6256)).toBe("137.6256");
  });

  /** 去尾零：六位精度不该让「20 积分」显示成「20.000000」。 */
  it("去尾零：20 / 27.5 / 0.02 不带多余的零", () => {
    expect(formatCreditsExact(20)).toBe("20");
    expect(formatCreditsExact(27.5)).toBe("27.5");
    expect(formatCreditsExact(0.02)).toBe("0.02");
    expect(formatCreditsExact(1000)).toBe("1000"); // 尾零正则不许吃掉整数部分的 0
  });

  /**
   * 🔴 FIX5 的性质门在这一类**仍然必要**：比 BE 量化精度（1e-6）还小的值去尾零后会变 "0"，
   * 必须兜到最小可表示单位。变异：删掉 `formatCreditsExact` 末行的兜底 → 本条红。
   */
  it("🔴 比 1e-6 还小的非零值不许显示为零（契约坏了/前端自算的极小值）", () => {
    expect(formatCreditsExact(1e-7)).toBe("0.000001");
    expect(formatCreditsExact(-1e-7)).toBe("-0.000001");
  });

  it("零仍然显示 0（判据只管非零值）", () => {
    expect(formatCreditsExact(0)).toBe("0");
  });

  /**
   * 🔴 符号不许在格式化时丢失：欠费路径展示的 `available_credits` 是负数，
   * 显示成正数会把「欠 42.5」说成「有 42.5」。
   */
  it("负数保留符号（欠费路径的余额是负的）", () => {
    expect(formatCreditsExact(-42.5)).toBe("-42.5");
    expect(formatCreditsExact(-0.02)).toBe("-0.02");
  });
});

describe("展示格式 · 两类之间的关系（这两条防止有人把分叉合并回去）", () => {
  /**
   * 🔴 **分叉必须真的有差别**：存在一个值，两个函数给出不同结果。
   * 变异：把 `formatCreditsUp` 实现成 `return formatCreditsExact(credits)`（合并回一个函数）→ 本条红。
   * 这是本轮 P1-1 的"分叉存在性"门 —— CB 判「分叉收益不足不成立」，这条钉住它不会被悄悄撤回。
   */
  it("🔴 同一个值两类给出不同结果（27.52512 → \"27.6\" vs \"27.52512\"）", () => {
    expect(formatCreditsUp(27.52512)).not.toBe(formatCreditsExact(27.52512));
    expect(formatCreditsUp(27.52512)).toBe("27.6");
    expect(formatCreditsExact(27.52512)).toBe("27.52512");
  });

  /**
   * 🔴 **共同判据**（两类都必须满足）：任意非零输入 → 输出不是 "0"。
   * 这条覆盖的是「**任意**非零输入」这个性质，将来有人换任一实现都逃不掉。
   */
  it("🔴 共同判据：任意非零输入 → 两类输出都不是 \"0\"（正负都测）", () => {
    const samples = [0.02, 0.001, 0.05, 0.004, 0.0001, 1e-6, 1e-7, 0.09, 0.049, 137.6256, 27.52, 20];
    for (const value of samples) {
      for (const shown of [formatCreditsUp(value), formatCreditsExact(value), formatCreditsUp(-value), formatCreditsExact(-value)]) {
        expect(shown).not.toBe("0");
        expect(Number(shown)).not.toBe(0);
      }
    }
  });
});

describe("费率的展示格式", () => {
  /** 费率固定 2 位：抹掉末位会把 1.12 显示成 1.1，那是**另一个价格**。 */
  it("费率固定 2 位小数（1.12 不许显示成 1.1）", () => {
    expect(formatRate(1.12)).toBe("1.12");
    expect(formatRate(6.72)).toBe("6.72");
    expect(formatRate(2.8)).toBe("2.80");
  });
});
