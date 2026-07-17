import { afterEach, describe, expect, it, vi } from "vitest";

import { copyToClipboard } from "@/lib/clipboard";

// CLIPBOARD-TRUTH-0001 · 收口后的唯一剪贴板逻辑的承重。
// 🔴 这里钉的是「返回值 = 是否真的写进去了」——三处消费者的「已复制」全靠这个布尔。
// 若它在 API 缺失/抛错时返回 true，三处会一起谎报（这正是收口前两处坏拷贝干的事）。

const setClipboard = (impl: unknown) =>
  Object.defineProperty(navigator, "clipboard", { value: impl, configurable: true, writable: true });

afterEach(() => {
  vi.clearAllMocks();
  setClipboard(undefined);
});

describe("copyToClipboard", () => {
  it("写入成功 → true，且 writeText 收到完整原文", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText });
    await expect(copyToClipboard("完整原文ABC")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("完整原文ABC");
  });

  // 🔴 收口前的 bug 核心：非安全上下文（navigator.clipboard 缺失）。
  // 坏写法 `await navigator.clipboard?.writeText(x)` 会 resolve undefined 而不抛 → 谎报成功。
  // 本函数判存在性、缺失即 false，writeText 根本不被调。
  it("🔴 Clipboard API 缺失（非安全上下文）→ false，且不调 writeText（不谎报）", async () => {
    setClipboard(undefined);
    await expect(copyToClipboard("abc")).resolves.toBe(false);
  });

  it("🔴 writeText 抛错（用户拒权 / 浏览器拦截）→ false（不谎报）", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    setClipboard({ writeText });
    await expect(copyToClipboard("abc")).resolves.toBe(false);
    expect(writeText).toHaveBeenCalledTimes(1);
  });

  it("空文本 → false，且不打剪贴板（没内容可复制）", async () => {
    const writeText = vi.fn();
    setClipboard({ writeText });
    await expect(copyToClipboard("")).resolves.toBe(false);
    expect(writeText).not.toHaveBeenCalled();
  });

  // 半残的 clipboard 对象（有 clipboard 但无 writeText）也算缺失 —— 存在性守卫用 `?.writeText`。
  it("clipboard 存在但无 writeText → false（不当它可用）", async () => {
    setClipboard({});
    await expect(copyToClipboard("abc")).resolves.toBe(false);
  });
});
