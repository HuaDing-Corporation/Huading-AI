import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// ── PRICING-UI-0001 §二/§三 · **界面上**的价格披露承重 ────────────────────────────────────────
// pricing.test.ts 钉的是"数算得对不对"；本文件钉的是"用户到底看不看得见"——两件事分开，互不塌缩。
//   门A 强度选择器：金额旁**必须同屏有口径说明**（不是 hover tooltip——触屏上等于没有）
//   门B 402 充值窗：§三 要求的四件事逐条在场，**「临时预留不是扣费」那句是硬门**
//   门C 主动点「充值」时**不**显示缺口（没有被拒的操作，凭空甩数字只会吓人）

vi.mock("@/lib/aibrain/hooks", () => ({
  useTopup: () => ({ mutateAsync: vi.fn(), isPending: false })
}));

import { copy } from "@/lib/copy";
import { IntensitySelector } from "./intensity-selector";
import { RechargeDialog } from "./recharge-dialog";
import { shortfallView } from "@/lib/aibrain/types";

describe("门A · 强度选择器：金额不许裸奔", () => {
  /**
   * 🔴 变异：删掉 intensity-selector.tsx 里那段 `<p>{copy.aibrain.intensityRateHint(...)}</p>`
   *    （即回到本包修复前「只有三个裸整数」的状态）→ 本条红。
   */
  it("门A：显示「约 N 积分/次」的同时，同屏给出费率与估算口径", () => {
    render(<IntensitySelector value="low" onChange={vi.fn()} />);
    expect(screen.getByText(copy.aibrain.intensityCost(4))).toBeInTheDocument();
    // 口径行：真实费率 + 「500 输入 + 500 输出」的估算依据，都得在页面上。
    const hint = screen.getByText(/积分每千 token/);
    expect(hint).toBeInTheDocument();
    expect(hint.textContent ?? "").toContain("1.12");
    expect(hint.textContent ?? "").toContain("6.72");
    expect(hint.textContent ?? "").toContain("500");
  });

  /**
   * 🔴 展示的是**新费率**下的值。变异：把 `typicalCredits` 换回硬编码 6/15/30 → 本条红。
   * （与 pricing.test.ts 门2 的区别：那条守"算出来是 4"，这条守"渲染出来的确实是它"。）
   */
  it("门A2：三档显示 4 / 10 / 20，旧的 6 / 15 / 30 一个都不出现", () => {
    const { container } = render(<IntensitySelector value="mid" onChange={vi.fn()} />);
    expect(screen.getByText(copy.aibrain.intensityCost(4))).toBeInTheDocument();
    expect(screen.getByText(copy.aibrain.intensityCost(10))).toBeInTheDocument();
    expect(screen.getByText(copy.aibrain.intensityCost(20))).toBeInTheDocument();
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/约 6 积分/);
    expect(text).not.toMatch(/约 15 积分/);
    expect(text).not.toMatch(/约 30 积分/);
  });

  /** 口径跟随档位切换（high 档要显示 high 的费率，不能永远显示 low 的）。 */
  it("门A3：切到 high 档 → 口径行换成 high 的费率 5.60 / 33.60", () => {
    render(<IntensitySelector value="high" onChange={vi.fn()} />);
    const hint = screen.getByText(/积分每千 token/);
    expect(hint.textContent ?? "").toContain("5.60");
    expect(hint.textContent ?? "").toContain("33.60");
  });
});

describe("门B · 402 充值窗：把四件事说清楚", () => {
  const dialog = () => screen.getByRole("dialog");

  /**
   * 🔴 §三 的四条要求逐条断言。其中第 4 条（临时预留 ≠ 扣费）是**本包最要紧的一句话**——
   * 用户会看到 137.6 被"扣住"，不说清楚就会以为一次对话花 137 积分。
   * 变异：删掉 ShortfallNotice 里 `insufficientReserveNote` 那一行 → 本条红。
   */
  it("门B：402 弹窗同时给出「至少需要 / 当前可用 / 至少还差 / 这是临时预留不是扣费」", () => {
    render(
      <RechargeDialog open onOpenChange={vi.fn()} shortfall={shortfallView("high", 20)} />
    );
    const d = within(dialog());
    // ① 需要多少（下界，配「至少」二字）
    expect(d.getByText(copy.aibrain.insufficientMinRequired("137.6"))).toBeInTheDocument();
    // ② 当前多少
    expect(d.getByText(copy.aibrain.insufficientAvailable("20"))).toBeInTheDocument();
    // ③ 还差多少
    expect(d.getByText(copy.aibrain.insufficientShortfall("117.6"))).toBeInTheDocument();
    // ④ 🔴 这是临时预留、不是扣费，结束即退回
    expect(d.getByText(copy.aibrain.insufficientReserveNote)).toBeInTheDocument();
  });

  /**
   * 🔴 钱包未加载 → 只说下界，**不显示「当前可用 0 积分」**（那是谎话）。
   * 变异：把 available 的 undefined 兜底成 0 → 本条红。
   */
  it("门B2：钱包未加载 → 不显示「当前可用」「还差」，但「临时预留」的解释照给", () => {
    render(
      <RechargeDialog open onOpenChange={vi.fn()} shortfall={shortfallView("low", undefined)} />
    );
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.insufficientMinRequired("27.5"))).toBeInTheDocument();
    expect(d.queryByText(copy.aibrain.insufficientAvailable("0"))).not.toBeInTheDocument();
    expect(d.getByText(copy.aibrain.insufficientReserveNote)).toBeInTheDocument();
  });

  /**
   * 🔴 余额高于下界仍被拒 → 换一句话说清方向，**不显示「至少还差 0 积分」**。
   * 变异：`shortfallView` 改用 `Math.max(0, …)` → 本条红。
   */
  it("门B3：余额高于下界仍被拒 → 给出「上下文较长」的解释，不出现「还差 0 积分」", () => {
    render(
      <RechargeDialog open onOpenChange={vi.fn()} shortfall={shortfallView("high", 500)} />
    );
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.insufficientContextHint)).toBeInTheDocument();
    expect(d.queryByText(copy.aibrain.insufficientShortfall("0"))).not.toBeInTheDocument();
  });

  /** 🔴 门C：主动充值（非 402）→ 一个缺口数字都不出现。变异：无条件渲染 ShortfallNotice → 本条红。 */
  it("门C：用户主动点「充值」（无 shortfall）→ 不出现任何缺口说明", () => {
    render(<RechargeDialog open onOpenChange={vi.fn()} />);
    const d = within(dialog());
    expect(d.queryByText(copy.aibrain.insufficientTitle)).not.toBeInTheDocument();
    expect(d.queryByText(copy.aibrain.insufficientReserveNote)).not.toBeInTheDocument();
    // 充值本身的功能不受影响。
    expect(d.getByRole("button", { name: copy.aibrain.rechargeConfirm })).toBeInTheDocument();
  });
});
