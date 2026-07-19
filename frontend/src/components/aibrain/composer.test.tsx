import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { Composer } from "./composer";

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
// onSend 现返回 Promise<boolean>（true=成功）。默认给 false（失败），成功用例各自 override。
const props = () => ({ onTierChange: vi.fn(), onSend: vi.fn().mockResolvedValue(false), onInsufficient: vi.fn() });
afterEach(() => vi.clearAllMocks());

describe("Composer 承重", () => {
  it("🔴 余额为 0 → **不发请求** + 弹充值窗（onInsufficient）", () => {
    const p = props();
    wrap(<Composer tier="mid" balance={0} sending={false} {...p} />);
    fireEvent.change(screen.getByLabelText(/输入问题/), { target: { value: "你好" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(p.onSend).not.toHaveBeenCalled();
    expect(p.onInsufficient).toHaveBeenCalledTimes(1);
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

  it("🔴 钱包未加载（balance=undefined）→ **不预拦**、照发（CR#2）", () => {
    const p = props();
    wrap(<Composer tier="mid" balance={undefined} sending={false} {...p} />);
    fireEvent.change(screen.getByLabelText(/输入问题/), { target: { value: "你好" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(p.onSend).toHaveBeenCalledTimes(1);
    expect(p.onInsufficient).not.toHaveBeenCalled();
  });

  it("语音不支持（jsdom 无 SpeechRecognition）→ 降级：不渲染录音按钮、给说明、不报错", () => {
    const p = props();
    wrap(<Composer tier="mid" balance={100} sending={false} {...p} />);
    expect(screen.queryByRole("button", { name: "语音输入" })).not.toBeInTheDocument();
    expect(screen.getByText(/不支持语音输入/)).toBeInTheDocument();
  });
});

describe("Composer · P1-1（发送失败保留输入/附件，不提前 revoke）", () => {
  it("🔴 发送失败 → **文字仍在**输入框（不必重打）", async () => {
    const p = { ...props(), onSend: vi.fn().mockResolvedValue(false) };
    wrap(<Composer tier="low" balance={100} sending={false} {...p} />);
    fireEvent.change(screen.getByLabelText(/输入问题/), { target: { value: "你好" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "发送" }));
    });
    expect(p.onSend).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText(/输入问题/)).toHaveValue("你好"); // 失败 → 文字保留
  });

  it("发送成功 → 文字清空（成功分支的对照）", async () => {
    const p = { ...props(), onSend: vi.fn().mockResolvedValue(true) };
    wrap(<Composer tier="low" balance={100} sending={false} {...p} />);
    fireEvent.change(screen.getByLabelText(/输入问题/), { target: { value: "你好" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "发送" }));
    });
    expect(screen.getByLabelText(/输入问题/)).toHaveValue(""); // 成功 → 清空
  });
});

describe("Composer · P1-1 附件（发送失败保留附件 + objectURL 未 revoke）", () => {
  let revokeSpy: ReturnType<typeof vi.fn>;
  const origCreate = URL.createObjectURL;
  const origRevoke = URL.revokeObjectURL;
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:mock-preview");
    revokeSpy = vi.fn();
    URL.revokeObjectURL = revokeSpy;
  });
  afterEach(() => {
    // jsdom 无 createObjectURL/revokeObjectURL → 原值可能 undefined。恢复成 noop（非 undefined），
    // 否则组件卸载清理调用 revokeObjectURL 会「not a function」。
    URL.createObjectURL = origCreate ?? (() => "blob:noop");
    URL.revokeObjectURL = origRevoke ?? (() => undefined);
  });

  it("🔴 发送失败 → **附件仍在** 且预览 objectURL **未被 revoke**（不必重传图片）", async () => {
    const p = { ...props(), onSend: vi.fn().mockResolvedValue(false) };
    const { container } = wrap(<Composer tier="low" balance={100} sending={false} {...p} />);

    // 真上传一张图片（走 MSW /uploads/images → asset_id）。
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([new Uint8Array([1, 2, 3])], "p.png", { type: "image/png" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    // 附件预览出现（本地 objectURL 图）。
    await waitFor(() => expect(container.querySelector('img[src="blob:mock-preview"]')).toBeTruthy());

    // 发送失败。
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "发送" }));
    });

    expect(p.onSend).toHaveBeenCalledTimes(1);
    expect(container.querySelector('img[src="blob:mock-preview"]')).toBeTruthy(); // 附件仍在
    expect(revokeSpy).not.toHaveBeenCalled(); // 预览未被提前 revoke → 图不碎
  });
});
