import { describe, expect, it } from "vitest";

import {
  MAX_COMPLETION_TOKENS,
  TIERS,
  TYPICAL_COMPLETION_TOKENS,
  TYPICAL_PROMPT_TOKENS,
  formatCredits,
  formatRate,
  minReservationCredits,
  outstandingView,
  precheckSend,
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
//   门3 预留下界 = 4096 × 输出费率（§三 表格 27.5/68.8/137.6 的出处）
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
  it("门1：三档费率逐个等于 BE config 的值（1.12/6.72 · 2.80/16.80 · 5.60/33.60）", () => {
    expect(TIERS.low.rate).toEqual({ inputPer1k: 1.12, outputPer1k: 6.72 });
    expect(TIERS.mid.rate).toEqual({ inputPer1k: 2.8, outputPer1k: 16.8 });
    expect(TIERS.high.rate).toEqual({ inputPer1k: 5.6, outputPer1k: 33.6 });
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
   * 🔴 门3：预留下界 = `max_completion_tokens × 输出费率`，即 §三 表格里的 27.5 / 68.8 / 137.6。
   * 这个数是 402 提示的核心（用户会看到它被"扣住"），算错就等于对用户报错价。
   * 变异：把 `MAX_COMPLETION_TOKENS` 改成别的、或下界改用输入费率 → 本条红。
   */
  it("门3：预留下界 = 4096 × 输出费率 → low 27.5 · mid 68.8 · high 137.6", () => {
    expect(MAX_COMPLETION_TOKENS).toBe(4096);
    expect(formatCredits(minReservationCredits("low"))).toBe("27.5");
    expect(formatCredits(minReservationCredits("mid"))).toBe("68.8");
    expect(formatCredits(minReservationCredits("high"))).toBe("137.6");
  });

  /** 🔴 §三 的用户体验前提：被扣住的数**远大于**实际花费——这正是必须解释「临时预留」的原因。 */
  it("门3b：预留下界 ≫ 典型消耗（high 档 137.6 vs 20，约 7 倍）——不解释清楚会被当成一次对话的花费", () => {
    expect(minReservationCredits("high")).toBeGreaterThan(typicalCredits("high") * 5);
    expect(minReservationCredits("low")).toBeGreaterThan(typicalCredits("low") * 5);
  });
});

/**
 * 「余额偏低」的阈值（wallet-balance.tsx `LOW_BALANCE`）此前是 `TIERS.high.typical`（30）——
 * 一个估算值；本包换成 `minReservationCredits("low")`（27.5），对应一条**硬边界**：低于它，
 * 连最低档都凑不齐一次预留、发送必被 402 拒。数值相近（视觉几乎不变），含义从"估算"变"事实"。
 * 变异：改回按 high 档取（137.6）→ 本条红（只用低档的用户会长期看到告警）。
 */
describe("低余额阈值", () => {
  it("低余额阈值 = 最低档的预留下界（27.5），不是高档的（137.6）也不是旧的典型值（30）", () => {
    expect(formatCredits(minReservationCredits("low"))).toBe("27.5");
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

describe("积分 / 费率的展示格式", () => {
  it("积分最多 1 位小数、整数不带 .0", () => {
    expect(formatCredits(137.6256)).toBe("137.6");
    expect(formatCredits(27.52)).toBe("27.5");
    expect(formatCredits(20)).toBe("20");
  });

  // ══ FIX5 · P1-2：**通用判据 —— 格式化不得把一个非零的金额显示为零** ═══════════════════════
  // 原缺陷：固定 `toFixed(1)` → `0.02` 变成 `"0"` → 界面上出现「还差 0 积分」「需补齐 0 积分」，
  // 用户据此认为**不差**，充值时就会充不够。这是**方向性错误**，比数字不精确严重得多。
  // 这条判据比"保留几位小数"更本质：小数位是表现，"非零不能变零"是语义。

  /**
   * 🔴🔴 本包的核心门。变异：把 `formatCredits` 改回 `credits.toFixed(1).replace(/\.0$/,"")`
   *      → 前四个断言全红（0.02/0.001/0.004/1e-7 都会变 "0"）。
   */
  it("🔴 任何非零金额都不许显示为零（0.02 / 0.001 / 0.05 / 1e-7）", () => {
    // 任务包点名的三个小额值
    expect(formatCredits(0.02)).toBe("0.02");
    expect(formatCredits(0.001)).toBe("0.001");
    // 四舍五入边界：0.05 → "0.1"。数值上夸大一倍，但方向安全（「还差」语义下宁可多充）。
    expect(formatCredits(0.05)).toBe("0.1");
    // 比 1 位小数的进位点还小 → 自动提升精度，而不是回落到 "0"
    expect(formatCredits(0.004)).toBe("0.004");
    // BE 把积分量化到 1e-6；比它还小的值（契约坏了/前端自算）也**不许**显示为零
    expect(formatCredits(1e-7)).toBe("0.000001");
  });

  /**
   * 🔴 判据本身用一组值批量守住 —— 逐个 `toBe` 只能覆盖写下的那几个，
   * 这条覆盖的是「**任意**非零输入」这个性质，将来有人换实现也逃不掉。
   */
  it("🔴 判据：任意非零输入 → 输出不是 \"0\"（正负都测）", () => {
    const samples = [0.02, 0.001, 0.05, 0.004, 0.0001, 1e-6, 1e-7, 0.09, 0.049, 137.6256, 27.52, 20];
    for (const value of samples) {
      expect(formatCredits(value)).not.toBe("0");
      expect(Number(formatCredits(value))).not.toBe(0);
      // 负数同理（欠费路径的 `available_credits` 是负的）
      expect(formatCredits(-value)).not.toBe("0");
      expect(Number(formatCredits(-value))).not.toBe(0);
    }
  });

  /** 真正的零仍显示 "0" —— 判据是"非零不许变零"，不是"什么都不许是零"。 */
  it("零仍然显示为 0（判据只管非零值）", () => {
    expect(formatCredits(0)).toBe("0");
  });

  /**
   * 🔴 符号不许在格式化时丢失：欠费路径展示的 `available_credits` 是负数，
   * 显示成正数会把「欠 42.5」说成「有 42.5」。
   */
  it("负数保留符号（欠费路径的余额是负的）", () => {
    expect(formatCredits(-42.5)).toBe("-42.5");
    expect(formatCredits(-0.02)).toBe("-0.02");
  });

  /** 费率固定 2 位：抹掉末位会把 1.12 显示成 1.1，那是**另一个价格**。 */
  it("费率固定 2 位小数（1.12 不许显示成 1.1）", () => {
    expect(formatRate(1.12)).toBe("1.12");
    expect(formatRate(6.72)).toBe("6.72");
    expect(formatRate(2.8)).toBe("2.80");
  });
});
