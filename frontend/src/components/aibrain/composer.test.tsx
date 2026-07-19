import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { Composer } from "./composer";

// 🔴 任务包两个变异的承重：① 去掉余额预检 → 「余额不足不发请求」必红；② 去掉档位传参 → 必红。
// 语音在 jsdom 天然不支持 → 顺带钉「不支持时降级、不报错」。

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
const props = () => ({ onTierChange: vi.fn(), onSend: vi.fn(), onInsufficient: vi.fn() });
afterEach(() => vi.clearAllMocks());

describe("Composer 承重", () => {
  it("🔴 余额为 0 → **不发请求** + 弹充值窗（onInsufficient）", () => {
    const p = props();
    wrap(<Composer tier="mid" balance={0} sending={false} {...p} />);
    fireEvent.change(screen.getByLabelText(/输入问题/), { target: { value: "你好" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(p.onSend).not.toHaveBeenCalled(); // 不发请求
    expect(p.onInsufficient).toHaveBeenCalledTimes(1); // 弹充值窗
  });

  it("🔴 余额充足 → 发请求，body 带 **tier + attachment_asset_ids**", () => {
    const p = props();
    wrap(<Composer tier="low" balance={100} sending={false} {...p} />);
    fireEvent.change(screen.getByLabelText(/输入问题/), { target: { value: "你好" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(p.onSend).toHaveBeenCalledTimes(1);
    expect(p.onSend.mock.calls[0][0]).toEqual({ content: "你好", tier: "low", attachment_asset_ids: [] });
    expect(p.onInsufficient).not.toHaveBeenCalled();
  });

  it("语音不支持（jsdom 无 SpeechRecognition）→ 降级：不渲染录音按钮、给说明、不报错", () => {
    const p = props();
    wrap(<Composer tier="mid" balance={100} sending={false} {...p} />);
    expect(screen.queryByRole("button", { name: "语音输入" })).not.toBeInTheDocument();
    expect(screen.getByText(/不支持语音输入/)).toBeInTheDocument();
  });
});
