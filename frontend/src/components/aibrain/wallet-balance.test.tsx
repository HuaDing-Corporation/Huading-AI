import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ⚠️ 全仓 `sequence.shuffle: true` —— 模块级可变 mock 必须在 beforeEach 复位，
//    否则本文件的用例顺序一变就互相污染。范式抄自 aibrain-chat.402.test.tsx:72。
const hooks = vi.hoisted(() => ({ wallet: undefined as unknown }));
vi.mock("@/lib/aibrain/hooks", () => ({
  useWallet: () => ({ data: hooks.wallet })
}));

import { WalletBalance } from "./wallet-balance";
import { copy } from "@/lib/copy";
import { minReservationCredits } from "@/lib/aibrain/types";

const w = (available: number) => ({
  available_credits: available,
  reserved_credits: 0,
  total_topup_credits: 0,
  total_spent_credits: 0,
  topup_options: [100, 500],
  single_request_limit: 200
});

beforeEach(() => {
  hooks.wallet = undefined;
});
afterEach(() => vi.clearAllMocks());

// ══ PRICING-UI-0003 · CB P2-3 ·「余额六位如实」的**组件级**门 ══════════════════════════════
// 本文件是**新建的** —— FIX6 交付时 `wallet-balance.tsx` 一条组件测试都没有。
// 唯一渲染过它的是 aibrain-chat.402.test.tsx，而那里只喂过 500 / 0 / -30 三个整数余额，
// 🔴 **这三个值在 formatCreditsUp 和 formatCreditsExact 下输出完全相同**（"500"/"0"/"-30"）——
//    所以那些测试对"用了哪个格式化函数"是**完全不敏感**的，把余额改成向上取整也不会红。
//    一道门只能守它断言的那件事：那边守的是 402 路由，不是金额格式。
//
// ⚠️ 选样本时的坑：`0.24192` 会同时点亮「余额偏低」徽标（阈值 = minReservationCredits("low")
//    = **27.52512**），把两件事混在一条门里。要测"六位如实"又不牵扯低余额分支，用 137.6256。
describe("WalletBalance · 余额六位如实（FIX6 · formatCreditsExact）", () => {
  /**
   * 🔴 变异捕捉：把 `wallet-balance.tsx` 里的 `formatCreditsExact` 换成 `formatCreditsUp` → 本条红
   * （137.6256 会变成 "137.7"）。余额是**已经发生的事实**，不是"你还得再拿出多少"，不许向上。
   * ⚠️ 断言用字面量，不调 formatCreditsExact —— 循环断言会让实现怎么改都绿。
   */
  it("🔴 余额 137.6256 → 原样显示六位，不是向上的 137.7", () => {
    hooks.wallet = w(137.6256);
    render(<WalletBalance onRecharge={vi.fn()} />);
    expect(screen.getByText("137.6256")).toBeInTheDocument();
    expect(screen.queryByText("137.7")).not.toBeInTheDocument();
    // 137.6256 > 27.52512 → 不该有低余额徽标（证明本条只在测格式，没混进阈值分支）
    expect(screen.queryByText(copy.aibrain.balanceLow)).not.toBeInTheDocument();
  });

  it("🔴 小额余额 0.24192 → 六位如实（不是 0.2 也不是 0.3）", () => {
    hooks.wallet = w(0.24192);
    render(<WalletBalance onRecharge={vi.fn()} />);
    expect(screen.getByText("0.24192")).toBeInTheDocument();
    expect(screen.queryByText("0.2")).not.toBeInTheDocument();
    expect(screen.queryByText("0.3")).not.toBeInTheDocument();
  });

  /** 欠费路径的余额是负的 —— 显示成正数会把「欠 42.5」说成「有 42.5」。 */
  it("负余额保留符号", () => {
    hooks.wallet = w(-42.5);
    render(<WalletBalance onRecharge={vi.fn()} />);
    expect(screen.getByText("-42.5")).toBeInTheDocument();
  });

  it("整数余额去尾零（500 不显示成 500.000000）", () => {
    hooks.wallet = w(500);
    render(<WalletBalance onRecharge={vi.fn()} />);
    expect(screen.getByText("500")).toBeInTheDocument();
  });

  /**
   * 🔴 钱包未加载显示破折号，**不是 0** —— 与 402 弹窗「不拿 0 冒充没钱」是同一条判据
   * （pricing-disclosure.test.tsx 门B2）。显示「0」而用户实际有余额，是在对他撒谎。
   * 变异：`{wallet ? formatCreditsExact(balance) : "—"}` 改成 `formatCreditsExact(balance)`
   *      （balance 已经 `?? 0` 兜底过）→ 本条红。
   */
  it("🔴 钱包未加载 → 显示「—」而不是「0」", () => {
    hooks.wallet = undefined;
    render(<WalletBalance onRecharge={vi.fn()} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    // 未加载时也不该判成低余额（`wallet != null` 那半个条件守的就是这个）
    expect(screen.queryByText(copy.aibrain.balanceLow)).not.toBeInTheDocument();
  });
});

// ══ 低余额阈值：**真值 27.52512**，不是被舍入的 27.5 ═══════════════════════════════════════
describe("WalletBalance · 低余额阈值边界（阈值 = 最低档预留下界 27.52512）", () => {
  /**
   * 🔴 这条门顺带钉死 P2-4 那件事：阈值是 `minReservationCredits("low")` = **27.52512**。
   * 余额 27.5 **低于**阈值 → 必须告警。若有人把阈值写回舍入后的 27.5，`27.5 < 27.5` 为假，
   * 这个用户就看不到告警了 —— 而他确实凑不齐一次预留。
   * 变异：`LOW_BALANCE` 改成字面量 27.5 → 本条红。
   */
  it("🔴 余额 27.5（低于真值下界 27.52512）→ 告警必须出现", () => {
    expect(minReservationCredits("low")).toBeCloseTo(27.52512, 5); // 先证明阈值是真值
    hooks.wallet = w(27.5);
    render(<WalletBalance onRecharge={vi.fn()} />);
    expect(screen.getByText(copy.aibrain.balanceLow)).toBeInTheDocument();
  });

  it("余额 27.6（高于下界）→ 不告警", () => {
    hooks.wallet = w(27.6);
    render(<WalletBalance onRecharge={vi.fn()} />);
    expect(screen.queryByText(copy.aibrain.balanceLow)).not.toBeInTheDocument();
  });
});
