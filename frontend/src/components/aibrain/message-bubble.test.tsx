import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageBubble } from "./message-bubble";
import type { ChatMessage } from "@/lib/aibrain/types";

const base: ChatMessage = {
  id: "m1",
  conversation_id: "c1",
  role: "user",
  content: "看这张图",
  attachments: [],
  status: "completed",
  created_at: "2026-07-19T10:00:00Z"
};

describe("MessageBubble · 附件缩略图（FIX2）", () => {
  it("🔴 附件带 download_url → 显真缩略图 <img>", () => {
    render(
      <MessageBubble
        message={{
          ...base,
          attachments: [{ asset_id: "a1", asset_type: "generated_image", mime_type: "image/png", download_url: "https://mock.local/a1.png" }]
        }}
      />
    );
    expect(screen.getByRole("img")).toHaveAttribute("src", "https://mock.local/a1.png");
  });

  it("download_url 为 null → 降级占位片（不显 img）", () => {
    render(
      <MessageBubble
        message={{
          ...base,
          attachments: [{ asset_id: "a1", asset_type: "document", mime_type: "application/pdf", download_url: null }]
        }}
      />
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("图片")).toBeInTheDocument();
  });
});

// ══ PRICING-UI-0003 · CB P2-3 ·「六位如实」的**组件级**门 ══════════════════════════════════
// FIX6 把积分格式化拆成两个函数（缺口类向上 / 实扣类如实），纯函数门在 pricing.test.ts 里已经很全，
// 那边的注释甚至点名了「`message-bubble` 的实扣、`wallet-balance` 的余额」——
// 🔴 但**没有任何一条门真的 render 过这两个组件**。也就是说把这里的 `formatCreditsExact`
//    整体换成 `formatCreditsUp`，FIX6 交付时全仓测试仍然全绿。这正是 FIX1 里 M5 那次教训的同型：
//    纯函数门绿着、组件根本没调用它，照样全绿。**函数有门 ≠ 用法有门。**
//
// ⚠️ 写这组门时踩到的三个坑，写下来免得下一个人再踩：
//   ① **不许循环断言**：`getByText(formatCreditsExact(0.24192))` 把实现搬进了预期，
//      那个函数怎么改都绿。必须用**字面量** "0.24192"。
//   ② 实扣行藏在 `!isUser && message.content` 里 —— 本文件的 `base` 是 `role: "user"`，
//      直接 `{...base, charged_credits}` 会让整个 footer 不渲染，得到一条**恒绿的空测**。
//      所以下面每条都先断言 footer 真的在（复制按钮），再断言金额。
//   ③ 负断言不能用 `toContain("0.2")` —— "0.24192" 本身就包含子串 "0.2"。要断整串。
describe("MessageBubble · 实扣金额六位如实（FIX6 · formatCreditsExact）", () => {
  const assistant: ChatMessage = { ...base, role: "assistant", content: "这是回答" };

  it("🔴 charged_credits = 0.24192 → 原样显示六位，**不是** 0.2（向下）也不是 0.3（向上）", () => {
    render(<MessageBubble message={{ ...assistant, charged_credits: 0.24192 }} />);
    // ② 先证明 footer 真的渲染了 —— 否则下面的负断言会因为"整块都不在"而假绿。
    expect(screen.getByRole("button", { name: /复制/ })).toBeInTheDocument();
    // ① 字面量，不调 formatCreditsExact
    expect(screen.getByText("本次消耗 0.24192 积分")).toBeInTheDocument();
    // ③ 整串负断言：舍入过的两个方向都不许出现
    expect(screen.queryByText("本次消耗 0.2 积分")).not.toBeInTheDocument();
    expect(screen.queryByText("本次消耗 0.3 积分")).not.toBeInTheDocument();
  });

  /**
   * 🔴 变异捕捉：把 `message-bubble.tsx` 里的 `formatCreditsExact` 换成 `formatCreditsUp` → 本条红
   * （0.24192 会变成 "0.3"）。这是本条门存在的**唯一理由** —— FIX6 交付时这个变异抓不到。
   */
  it("🔴 137.6256（预留下界那个值）作为实扣 → 137.6256，不是向上的 137.7", () => {
    render(<MessageBubble message={{ ...assistant, charged_credits: 137.6256 }} />);
    expect(screen.getByText("本次消耗 137.6256 积分")).toBeInTheDocument();
    expect(screen.queryByText("本次消耗 137.7 积分")).not.toBeInTheDocument();
  });

  it("整数实扣去尾零（20 不显示成 20.000000）", () => {
    render(<MessageBubble message={{ ...assistant, charged_credits: 20 }} />);
    expect(screen.getByText("本次消耗 20 积分")).toBeInTheDocument();
  });

  /**
   * 🔴 守卫是 `!= null` 而不是 truthy：实扣确实为 0 时要显示「0 积分」（那是事实），
   * 字段缺席才不显示。变异：改成 `message.charged_credits ? ... : null` → 前半条红。
   */
  it("实扣为 0 → 显示「0 积分」；字段缺席 → 整行不出现", () => {
    const { unmount } = render(<MessageBubble message={{ ...assistant, charged_credits: 0 }} />);
    expect(screen.getByText("本次消耗 0 积分")).toBeInTheDocument();
    unmount();

    render(<MessageBubble message={assistant} />); // 不带 charged_credits
    expect(screen.getByRole("button", { name: /复制/ })).toBeInTheDocument(); // footer 在
    expect(screen.queryByText(/本次消耗/)).not.toBeInTheDocument(); // 但没有金额行
  });

  /** 用户消息没有 footer，也就没有实扣行 —— 顺带钉住上面②那个坑。 */
  it("用户消息不渲染 footer（所以实扣门必须用 assistant，否则是空测）", () => {
    render(<MessageBubble message={{ ...base, charged_credits: 0.24192 }} />);
    expect(screen.queryByRole("button", { name: /复制/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/本次消耗/)).not.toBeInTheDocument();
  });
});
