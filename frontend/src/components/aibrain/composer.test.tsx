import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { Composer } from "./composer";

// 🔴 本文件是任务包两个变异的承重：① 去掉余额预检 → 「余额不足不发请求」必红；② 去掉档位传参 → 必红。
// 语音在 jsdom 下天然不支持（无 SpeechRecognition）→ 顺带钉「不支持时降级、不报错」。

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
const props = () => ({ onTierChange: vi.fn(), onSend: vi.fn(), onInsufficient: vi.fn() });
afterEach(() => vi.clearAllMocks());

describe("Composer 承重", () => {
  it("🔴 余额不足 → **不发请求** + 弹充值窗（onInsufficient）", () => {
    const p = props();
    // mid 档 reserve=100，余额 5 < 100 → 预检 insufficient。
    wrap(<Composer tier="mid" balance={5} sending={false} {...p} />);
    fireEvent.change(screen.getByLabelText(/输入问题/), { target: { value: "你好" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(p.onSend).not.toHaveBeenCalled(); // 不发请求
    expect(p.onInsufficient).toHaveBeenCalledTimes(1); // 弹充值窗（不是普通报错）
  });

  it("🔴 余额充足 → 发请求，且 body **带上所选档位**", () => {
    const p = props();
    wrap(<Composer tier="low" balance={500} sending={false} {...p} />);
    fireEvent.change(screen.getByLabelText(/输入问题/), { target: { value: "你好" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(p.onSend).toHaveBeenCalledTimes(1);
    expect(p.onSend.mock.calls[0][0]).toMatchObject({ content: "你好", tier: "low" }); // 档位随请求传
    expect(p.onInsufficient).not.toHaveBeenCalled();
  });

  it("语音不支持（jsdom 无 SpeechRecognition）→ 降级：不渲染录音按钮、给说明、不报错", () => {
    const p = props();
    wrap(<Composer tier="mid" balance={500} sending={false} {...p} />);
    expect(screen.queryByRole("button", { name: "语音输入" })).not.toBeInTheDocument();
    expect(screen.getByText(/不支持语音输入/)).toBeInTheDocument();
  });
});
