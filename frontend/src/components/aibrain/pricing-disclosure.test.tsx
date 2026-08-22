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
import { outstandingView, shortfallView } from "@/lib/aibrain/types";

describe("门A · 强度选择器：金额不许裸奔", () => {
  /**
   * 🔴 变异：删掉 intensity-selector.tsx 里那段 `<p>{copy.aibrain.intensityRateHint(...)}</p>`
   *    （即回到本包修复前「只有三个裸整数」的状态）→ 本条红。
   */
  it("门A：显示「约 N 积分/次」的同时，同屏给出费率与估算口径", () => {
    const { container } = render(<IntensitySelector value="low" onChange={vi.fn()} />);
    expect(screen.getByText(copy.aibrain.intensityCost(4))).toBeInTheDocument();
    // 口径行：**低区间**真实费率 + 「500 输入 + 500 输出」的估算依据，都得在页面上。
    const text = container.textContent ?? "";
    expect(text).toContain("1.12");
    expect(text).toContain("6.72");
    expect(text).toContain("500");
  });

  /**
   * 🔴🔴 PRICING-UI-0002 §二.3：费率有两个区间之后，**不许让用户以为只有一个**。
   * 常驻行只写低区间（一行塞四个数没人看），但**必须常驻点明"还有一档"**；
   * 高区间的具体数放在折叠里（原生 `<details>`：键盘/读屏/触屏都可达，不是 hover tooltip）。
   * 变异A：删掉 `intensityRateTierNote` 那句 → 前半段红（用户会以为 1.12/6.72 是唯一费率）。
   * 变异B：删掉 `<details>` 整块 → 后半段红（高区间的数字无处可查）。
   */
  it("🔴 门A1b：常驻点明「还有更高费率」，且高区间数字可展开查到（2.24 / 10.08）", () => {
    const { container } = render(<IntensitySelector value="low" onChange={vi.fn()} />);
    const text = container.textContent ?? "";
    // 常驻：必须让用户知道存在第二档，且说清判据（超长上下文 / 27.2 万 token）。
    expect(text).toContain(copy.aibrain.intensityRateTierNote(27.2));
    // 🔴 独立产品字面量：不能让上面的生产 helper 同时生成实现与期望，否则两边一起把「输入 token」
    // 误写成「总 token」仍会自洽假绿。变异：copy 改成「总 token」→ 本条红。
    expect(text).toContain("输入超过 27.2 万 token");
    expect(text).not.toContain("总 token");
    // 折叠里：高区间的两个数确实在 DOM 里（<details> 收起时内容仍在，可被读屏/展开查到）。
    expect(text).toContain("2.24");
    expect(text).toContain("10.08");
    // 🔴 高区间是整次切档，不是只给 272K 以上的增量 token 加价。
    // 变异：文案写成「超过阈值的部分」→ 本条红；期望用产品字面量，不调用 copy helper 自我抵消。
    expect(text).toContain("本次输入与输出均按高区间费率计费");
    expect(text).not.toContain("超过 27.2 万 token 的部分");
    expect(screen.getByText(copy.aibrain.intensityRateTierToggle)).toBeInTheDocument();
  });

  /** 高区间明细跟随档位切换（high 档要给 high 的高区间 11.20 / 50.40，不能永远显示 low 的）。 */
  it("门A1c：切到 high 档 → 折叠里的高区间换成 11.20 / 50.40", () => {
    const { container } = render(<IntensitySelector value="high" onChange={vi.fn()} />);
    const text = container.textContent ?? "";
    expect(text).toContain("11.20");
    expect(text).toContain("50.40");
    expect(text).not.toContain("2.24"); // 不许串到 low 的高区间
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
  it("门A3：切到 high 档 → 口径行换成 high 的**低区间**费率 5.60 / 33.60", () => {
    const { container } = render(<IntensitySelector value="high" onChange={vi.fn()} />);
    const text = container.textContent ?? "";
    expect(text).toContain("5.60");
    expect(text).toContain("33.60");
  });
});

describe("门B · 402「预留不足」充值窗：把四件事说清楚", () => {
  const dialog = () => screen.getByRole("dialog");
  const insufficient = (shortfall: ReturnType<typeof shortfallView>) => (
    <RechargeDialog open onOpenChange={vi.fn()} reason={{ kind: "insufficient", shortfall }} />
  );
  /** BE `AIBRAIN_INSUFFICIENT_BALANCE` 的 detail 真实形状（aibrain.py:578-583）。 */
  const BE_DETAIL = {
    required_credits: 137.6256,
    available_credits: 20,
    shortfall_credits: 117.6256,
    temporary_reservation: true
  };

  /**
   * 🔴 §三 的四条要求逐条断言（**精确态**：BE 给了 detail → 措辞里没有「至少」）。
   * 其中第 4 条（临时预留 ≠ 扣费）是这条路径最要紧的一句话——用户会看到 137.7 被"扣住"（真值 137.6256，向上展示），
   * 不说清楚就会以为一次对话花 137 积分。
   * 变异：删掉 ShortfallNotice 里 `insufficientReserveNote` 那一行 → 本条红。
   */
  it("门B：精确态 402 → 「需临时预留 / 当前可用 / 还差 / 这是临时预留不是扣费」四件齐全", () => {
    render(insufficient(shortfallView("high", 20, BE_DETAIL)));
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.insufficientRequired("137.7"))).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.insufficientAvailable("20"))).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.insufficientShortfallExact("117.7"))).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.insufficientReserveNote)).toBeInTheDocument();
    // 🔴 精确态**不许**出现「至少」的回退措辞（把精确值说成下界，或反过来，都是错的口径）。
    expect(d.queryByText(copy.aibrain.insufficientMinRequired("137.7"))).not.toBeInTheDocument();
    expect(dialog().textContent ?? "").not.toContain("至少");
  });

  /**
   * 🔴 FIX1 点名要钉的**回退**：BE 不发 detail 时退回下界 + 「至少」措辞，而不是什么都不显示。
   * 变异：删掉 `shortfallView` 的回退分支、或让 UI 在 `exact=false` 时不渲染 → 本条红。
   * 这一条与上一条**同守一个组件、判据相反**，构成 exact 两态的完整覆盖。
   */
  it("门B1b：回退态（BE 未发 detail）→ 仍显示金额，但措辞带「至少」", () => {
    render(insufficient(shortfallView("high", 20)));
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.insufficientMinRequired("137.7"))).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.insufficientShortfall("117.7"))).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.insufficientReserveNote)).toBeInTheDocument();
    // 回退态**不许**用精确措辞（那等于告诉用户「充这么多就够」，而实际还要加提示词那一段）。
    expect(d.queryByText(copy.aibrain.insufficientRequired("137.7"))).not.toBeInTheDocument();
  });

  /**
   * 🔴 钱包未加载 → 只说下界，**不显示「当前可用 0 积分」**（那是谎话）。
   * 变异：把 available 的 undefined 兜底成 0 → 本条红。
   */
  it("门B2：detail 与钱包都没有 → 不显示「当前可用」「还差」，但「临时预留」的解释照给", () => {
    render(insufficient(shortfallView("low", undefined)));
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.insufficientMinRequired("27.6"))).toBeInTheDocument();
    expect(d.queryByText(copy.aibrain.insufficientAvailable("0"))).not.toBeInTheDocument();
    expect(d.getByText(copy.aibrain.insufficientReserveNote)).toBeInTheDocument();
  });

  /**
   * 🔴 回退态余额高于下界仍被拒 → 换一句话说清方向，**不显示「至少还差 0 积分」**。
   * 变异：`shortfallView` 回退分支改用 `Math.max(0, …)` → 本条红。
   */
  it("门B3：回退态余额高于下界仍被拒 → 给出「上下文较长」的解释，不出现「还差 0 积分」", () => {
    render(insufficient(shortfallView("high", 500)));
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.insufficientContextHint)).toBeInTheDocument();
    expect(d.queryByText(copy.aibrain.insufficientShortfall("0"))).not.toBeInTheDocument();
  });

  /**
   * 🔴🔴 FIX5 · P1-2 **在渲染层**的门（CB 报的缺陷是「弹窗上显示『还差 0 积分』」，
   * 所以除了 `formatCreditsUp` 的单元门，这一层也必须钉住 —— 两者互不塌缩：
   * 那边守"函数不返回 0"，这边守"用户真的看不到 0"）。
   * 变异：`formatCreditsUp` 改回固定 `toFixed(1)` → 本条红（会渲染出「还差 0 积分」）。
   */
  it("🔴 小额正缺口（0.02）→ 弹窗显示「还差 0.1 积分」（向上），**绝不出现「还差 0 积分」**", () => {
    render(
      insufficient(
        shortfallView("low", 27.5, {
          required_credits: 27.52,
          available_credits: 27.5,
          shortfall_credits: 0.02,
          temporary_reservation: true
        })
      )
    );
    const d = within(dialog());
    // 🔴 FIX6：缺口类**向上取整**到 1 位 → 0.02 显示成 0.1。数值上夸大了 5 倍，但方向安全：
    //    「还差」语义下宁可让用户多充 0.08，也不能让他以为不用充。**把非零说成零才是不可接受的那种错。**
    expect(d.getByText(copy.aibrain.insufficientShortfallExact("0.1"))).toBeInTheDocument();
    // 🔴 用户绝不能看到「还差 0 积分」——那会让他以为不用充。
    expect(d.queryByText(copy.aibrain.insufficientShortfallExact("0"))).not.toBeInTheDocument();
    expect(dialog().textContent ?? "").not.toMatch(/还差\s*0\s*积分/);
  });

  /** 🔴 门C：主动充值（非 402）→ 一个缺口数字都不出现。变异：无条件渲染说明块 → 本条红。 */
  it("门C：用户主动点「充值」（无 reason）→ 不出现任何缺口/欠费说明", () => {
    render(<RechargeDialog open onOpenChange={vi.fn()} />);
    const d = within(dialog());
    expect(d.queryByText(copy.aibrain.insufficientTitle)).not.toBeInTheDocument();
    expect(d.queryByText(copy.aibrain.insufficientReserveNote)).not.toBeInTheDocument();
    expect(d.queryByText(copy.aibrain.outstandingTitle)).not.toBeInTheDocument();
    // 充值本身的功能不受影响。
    expect(d.getByRole("button", { name: copy.aibrain.rechargeConfirm })).toBeInTheDocument();
  });
});

// ── FIX1 · 402 之二：欠费 AIBRAIN_OUTSTANDING_BALANCE ───────────────────────────────────────
// 🔴 这组门的核心是**两种 402 的话术不许串**。欠费用户的上一次对话是**成功交付**的，那笔钱
//    真花掉了；对他说「这是临时预留、结束后差额会退回」是彻头彻尾的错误信息。
describe("门D · 402「欠费」充值窗：与「预留不足」明确区分", () => {
  const dialog = () => screen.getByRole("dialog");
  const outstanding = (o?: ReturnType<typeof outstandingView>) => (
    <RechargeDialog open onOpenChange={vi.fn()} reason={{ kind: "outstanding", outstanding: o }} />
  );
  /** BE `AIBRAIN_OUTSTANDING_BALANCE` 的 detail 真实形状（aibrain.py:564-567）。 */
  const BE_DETAIL = { available_credits: -42.5, outstanding_credits: 42.5 };

  /**
   * 🔴 变异：把 `aibrain-chat.tsx` 的欠费分流删掉（让 OUTSTANDING 落到 `err.status === 402` 的
   *    insufficient 兜底）→ 本条红（欠费文案不出现、反而出现「临时预留」那句）。
   */
  it("门D：欠费弹窗给出「需补齐 X / 当前余额 −X / 上次已交付、差额记成欠款」", () => {
    render(outstanding(outstandingView(BE_DETAIL)));
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.outstandingTitle)).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.outstandingNote)).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.outstandingAmount("42.5"))).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.outstandingBalance("-42.5"))).toBeInTheDocument();
  });

  /**
   * 🔴🔴 **本组最要紧的一条**：欠费弹窗里**绝不许出现**「预留不足」那套话术。
   * 变异：把 ShortfallNotice 与 OutstandingNotice 合并成一个组件、共用 `insufficientReserveNote`
   *       → 本条红。（这正是我把两者拆成两个组件的原因。）
   */
  it("门D2：欠费弹窗**不出现**「临时预留」那套话术——那笔钱已经花掉了", () => {
    render(outstanding(outstandingView(BE_DETAIL)));
    const text = dialog().textContent ?? "";
    expect(text).not.toContain(copy.aibrain.insufficientReserveNote);
    expect(text).not.toContain(copy.aibrain.insufficientTitle);
    // ⚠️ 断言「临时预留」这四个字，**不是**笼统的「退回」——弹窗里本来就有一句必须保留的
    //    `rechargeIrreversible`「充值后不可退回余额」（D4 的硬要求），那是讲充值不可退，
    //    与「预留会退回」是两回事。按「退回」笼统排除会误杀它（初版这条就是这么写红的）。
    expect(text).not.toContain("临时预留");
  });

  /**
   * 🔴 detail 与钱包都拿不到数 → 只给定性文案，**不编数字**。
   * 变异：`outstandingView` 无来源时兜底 `{ outstanding: 0 }` → 本条红（会出现「需补齐 0 积分」）。
   */
  it("门D3：拿不到欠款额 → 只给定性说明，一个数字都不编", () => {
    render(outstanding(undefined));
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.outstandingUnknown)).toBeInTheDocument();
    expect(d.queryByText(copy.aibrain.outstandingAmount("0"))).not.toBeInTheDocument();
  });

  /** 回退：detail 缺失但钱包余额为负 → 用负余额反推，仍能给出准确数字。 */
  it("门D4：detail 缺失但钱包为负 → 用负余额反推欠款额", () => {
    render(outstanding(outstandingView(undefined, -30)));
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.outstandingAmount("30"))).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.outstandingBalance("-30"))).toBeInTheDocument();
  });

  /**
   * 🔴🔴 FIX5 · P1-2：**欠费路径同款**（CB 报的两处之一是「需补齐 0 积分」）。
   * 与上面那条 shortfall 的门分开写：两条路径走的是不同组件、不同 copy 键，
   * 只修一处、只测一处正是本项目「修一处、同类还在」的老教训。
   * 变异：`formatCreditsUp` 改回固定 `toFixed(1)` → 本条红（「需补齐」是缺口类，走向上那一支）。
   */
  it("🔴 小额欠款（0.02）→ 显示「需补齐 0.1 积分」（向上），余额仍如实显示 -0.02", () => {
    render(outstanding(outstandingView({ available_credits: -0.02, outstanding_credits: 0.02 })));
    const d = within(dialog());
    // 🔴🔴 **同一个弹窗里两类并存**，正好演示分叉的意义：
    //    「需补齐」是缺口类 → 向上（0.02 → 0.1，宁可多补）
    //    「当前余额」是余额类 → 如实（-0.02 原样，这是已经发生的事实）
    expect(d.getByText(copy.aibrain.outstandingAmount("0.1"))).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.outstandingBalance("-0.02"))).toBeInTheDocument();
    expect(d.queryByText(copy.aibrain.outstandingAmount("0"))).not.toBeInTheDocument();
    expect(dialog().textContent ?? "").not.toMatch(/需补齐\s*0\s*积分/);
  });
});
