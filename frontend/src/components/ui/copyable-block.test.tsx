import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CopyableBlock } from "@/components/ui/copyable-block";

// HISTORY-FULL-PROMPT-UI-0001 · 共享复制块承重。
// 🔴 本文件钉的是「**剪贴板里真的有内容**」，不是「按钮存在」——
// 本仓已有的三份 clipboard 实现里有**两份是坏的**（copywriting-form:90 / publish-draft-card:48：
// `await navigator.clipboard?.writeText(x)` 的 `?.` 在 API 缺失时短路成 undefined、await 不抛异常 →
// 紧跟的 setCopied(true) 照跑 → **界面说「已复制」，剪贴板空的**）。
// 一个只测「按钮在」或「点完出现『已复制』」的测试，对那两份**照样全绿** —— 那种测试正是这个 bug 活到今天的原因。
// 期望值手写，不 import 被测代码的常量。
const LONG = "把图片背景换成浅蓝色带线条波纹浅反光的纯净水，".repeat(20); // ≈ 460 字，模拟用户报的长提示词

const setClipboard = (impl: unknown) =>
  Object.defineProperty(navigator, "clipboard", { value: impl, configurable: true, writable: true });

afterEach(() => {
  vi.clearAllMocks();
  setClipboard(undefined);
});

describe("CopyableBlock", () => {
  it("🔴 点复制 → writeText 收到**完整原文**（不是截断版、不是 innerText）", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText });
    render(<CopyableBlock label="提示词" text={LONG} />);

    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText).toHaveBeenCalledWith(LONG); // 逐字相同 → 用户粘出来的就是原文
  });

  it("复制成功 → 反馈不只靠颜色：文案由「复制」变「已复制」", async () => {
    setClipboard({ writeText: vi.fn().mockResolvedValue(undefined) });
    render(<CopyableBlock label="提示词" text="abc" />);

    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    expect(await screen.findByText("已复制")).toBeInTheDocument();
  });

  // 🔴 这条就是那两份坏实现会红的地方（它们的 `?.` 短路后 await 不抛 → 照样置「已复制」）。
  it("🔴 非安全上下文（navigator.clipboard 缺失）→ **不谎报**「已复制」", () => {
    setClipboard(undefined);
    render(<CopyableBlock label="提示词" text="abc" />);

    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    expect(screen.queryByText("已复制")).not.toBeInTheDocument();
  });

  it("🔴 writeText 抛错（用户拒权 / 浏览器拦截）→ 同样不谎报", async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error("denied")) });
    render(<CopyableBlock label="提示词" text="abc" />);

    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    await new Promise((r) => setTimeout(r, 0)); // 放行那个 rejected promise
    expect(screen.queryByText("已复制")).not.toBeInTheDocument();
  });

  it("长提示词全文渲染、不截断（用户报的正是「长了就看不全」）", () => {
    setClipboard({ writeText: vi.fn() });
    const { container } = render(<CopyableBlock label="提示词" text={LONG} />);

    const p = container.querySelector("p");
    expect(p?.textContent).toBe(LONG); // 全文在 DOM 里，不是 "…"
    // whitespace-pre-wrap 保留换行；break-words 让超长无空格串折行而不是横向撑破弹窗
    expect(p?.className).toContain("whitespace-pre-wrap");
    expect(p?.className).toContain("break-words");
    expect(p?.className).not.toContain("truncate");
    expect(p?.className).not.toContain("line-clamp");
  });

  it("空文本 → 点复制不打剪贴板（没内容可复制）", () => {
    const writeText = vi.fn();
    setClipboard({ writeText });
    render(<CopyableBlock label="提示词" text="" />);

    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    expect(writeText).not.toHaveBeenCalled();
  });
});
