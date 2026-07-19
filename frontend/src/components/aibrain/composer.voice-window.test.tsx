import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { copy } from "@/lib/copy";

// FIX5 语音支线：jsdom 无 SpeechRecognition → 真 useVoiceInput 会 supported=false、按钮隐藏，无法驱动。
// 这里 mock 成「支持」并捕获识别回调，以便复现「发送等待窗口内仍在听写」这条真实路径。
// 单独文件放，避免污染 composer.test.tsx 里「语音不支持→降级」的用例。
const voiceHarness = vi.hoisted(() => ({ onText: null as ((t: string) => void) | null }));
vi.mock("@/components/aibrain/use-voice-input", () => ({
  useVoiceInput: (onText: (t: string) => void) => {
    voiceHarness.onText = onText; // 捕获最新回调（读 voiceBase.current 这个 live ref，调哪个都一样）
    return { supported: true, listening: false, transcript: "", start: vi.fn(), stop: vi.fn() };
  }
}));

import { Composer } from "./composer";

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
const props = () => ({ onTierChange: vi.fn(), onSend: vi.fn().mockResolvedValue(false), onInsufficient: vi.fn() });
/** onSend 挂起，交回 resolve 由用例决定何时返回——模拟「等待响应」窗口。 */
function deferredSend() {
  let resolve!: (v: boolean) => void;
  const onSend = vi.fn(() => new Promise<boolean>((r) => (resolve = r)));
  return { onSend, resolve: (v: boolean) => resolve(v) };
}
/** onText 传的是「本次话语的累计转写」（use-voice-input onresult 从 resultIndex 累加），组件做 setText(voiceBase + t)。 */
const dictate = (t: string) => act(() => voiceHarness.onText!(t));
afterEach(() => vi.clearAllMocks());

describe("Composer · P1（FIX5 语音跨发送窗口不截断前缀）", () => {
  it("🔴 边听写边发送、成功后文字被保留 → voiceBase 前缀**不被重置**（后续识别不丢开录前的文字）", async () => {
    const { onSend, resolve } = deferredSend();
    wrap(<Composer tier="low" balance={100} sending={false} {...props()} onSend={onSend} />);
    const ta = screen.getByLabelText(/输入问题/) as HTMLTextAreaElement;

    // 先手打前缀，再开语音：voiceBase 记下「开录前已有文字」= "帮我看看 "。
    fireEvent.change(ta, { target: { value: "帮我看看" } });
    fireEvent.click(screen.getByRole("button", { name: copy.aibrain.voiceStart }));
    dictate("这个");
    expect(ta).toHaveValue("帮我看看 这个");

    // 边听写边发送（sentText = "帮我看看 这个"）。
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "发送" }));
    });
    // 窗口期继续说 → 文字变了。
    dictate("这个 多少钱");
    expect(ta).toHaveValue("帮我看看 这个 多少钱");

    // 第一条成功返回：文字 ≠ 快照 → 保留（不清）。
    await act(async () => {
      resolve(true);
      await Promise.resolve();
    });
    expect(ta).toHaveValue("帮我看看 这个 多少钱");

    // 成功之后再来一帧识别：前缀 "帮我看看 " 必须仍在（voiceBase 没被重置）。
    dictate("这个 多少钱 谢谢");
    // 🔴 前缀保留。变异「成功后无条件重置 voiceBase」→ 此处塌成 "这个 多少钱 谢谢"（前缀丢失）。
    expect(ta).toHaveValue("帮我看看 这个 多少钱 谢谢");
  });

  it("对照：发送时没动（成功清空文字）→ voiceBase 被重置，后续识别从空前缀开始（正常路径别误伤）", async () => {
    const { onSend, resolve } = deferredSend();
    wrap(<Composer tier="low" balance={100} sending={false} {...props()} onSend={onSend} />);
    const ta = screen.getByLabelText(/输入问题/) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "帮我看看" } });
    fireEvent.click(screen.getByRole("button", { name: copy.aibrain.voiceStart }));
    dictate("这个");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "发送" }));
    });
    // 窗口期**不动** → 成功后正常清空。
    await act(async () => {
      resolve(true);
      await Promise.resolve();
    });
    expect(ta).toHaveValue(""); // 未动 → 清空
    dictate("谢谢"); // 之后再识别一帧：从空前缀开始（voiceBase 已随清空一并重置）
    expect(ta).toHaveValue("谢谢");
  });
});
