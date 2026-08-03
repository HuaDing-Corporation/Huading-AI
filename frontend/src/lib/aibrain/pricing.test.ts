import { describe, expect, it } from "vitest";

import {
  MAX_COMPLETION_TOKENS,
  TIERS,
  TYPICAL_COMPLETION_TOKENS,
  TYPICAL_PROMPT_TOKENS,
  formatCredits,
  formatRate,
  minReservationCredits,
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

describe("shortfallView（402 要交代的三个数）", () => {
  /**
   * 🔴 钱包未加载时**不填 0 冒充**：显示「当前可用 0 积分」而实际有余额，是在对用户撒谎。
   * 变异：把 `available` 的 undefined 兜底成 0 → 本条红。
   */
  it("钱包未加载 → available/shortfall 均 undefined（不拿 0 冒充「没钱」）", () => {
    const v = shortfallView("high", undefined);
    expect(v.minRequired).toBeCloseTo(137.6256, 4);
    expect(v.available).toBeUndefined();
    expect(v.shortfall).toBeUndefined();
  });

  it("余额低于下界 → 给出差额（137.6256 − 20 = 117.6256）", () => {
    const v = shortfallView("high", 20);
    expect(v.available).toBe(20);
    expect(v.shortfall).toBeCloseTo(117.6256, 4);
  });

  /**
   * 🔴 余额**高于**下界却仍被 402（缺口来自提示词那一段，前端算不出）→ shortfall 必须是 undefined，
   * 好让 UI 换一句话说。变异：改成 `Math.max(0, minRequired - available)` → 本条红（会得到 0），
   * 而 UI 就会显示「至少还差 0 积分」这种荒谬值。
   */
  it("余额高于下界仍被拒 → shortfall 为 undefined（绝不显示「还差 0 积分」）", () => {
    const v = shortfallView("high", 500);
    expect(v.available).toBe(500);
    expect(v.shortfall).toBeUndefined();
  });
});

describe("积分 / 费率的展示格式", () => {
  it("积分最多 1 位小数、整数不带 .0", () => {
    expect(formatCredits(137.6256)).toBe("137.6");
    expect(formatCredits(27.52)).toBe("27.5");
    expect(formatCredits(20)).toBe("20");
  });

  /** 费率固定 2 位：抹掉末位会把 1.12 显示成 1.1，那是**另一个价格**。 */
  it("费率固定 2 位小数（1.12 不许显示成 1.1）", () => {
    expect(formatRate(1.12)).toBe("1.12");
    expect(formatRate(6.72)).toBe("6.72");
    expect(formatRate(2.8)).toBe("2.80");
  });
});
