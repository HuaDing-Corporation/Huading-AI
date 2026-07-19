import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { Composer } from "./composer";
import { copy } from "@/lib/copy";

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

// FIX5：发送在 await 期间开了一个异步窗口——期间用户可能已边等边打下一条 / 加了新附件。
// 「成功后无条件清空」会把窗口期的新状态一起抹掉（FIX4 把同步清空改成 await 后清空引入的二阶竞态）。
// 修法是「比较后再清空」：只清仍等于发送快照的部分，绝不 revoke 新附件的 URL。
describe("Composer · P1（FIX5 发送等待窗口不清空新状态）", () => {
  let revokeSpy: ReturnType<typeof vi.fn>;
  const origCreate = URL.createObjectURL;
  const origRevoke = URL.revokeObjectURL;
  beforeEach(() => {
    let uid = 0;
    URL.createObjectURL = vi.fn(() => `blob:mock-${++uid}`); // 每次不同 → 能区分「本批」与「新加」
    revokeSpy = vi.fn();
    URL.revokeObjectURL = revokeSpy;
  });
  afterEach(() => {
    URL.createObjectURL = origCreate ?? (() => "blob:noop");
    URL.revokeObjectURL = origRevoke ?? (() => undefined);
  });

  /** onSend 挂起，交回 resolve 由用例决定何时/以何结果返回——精确模拟「等待响应」窗口。 */
  function deferredSend() {
    let resolve!: (v: boolean) => void;
    const onSend = vi.fn(() => new Promise<boolean>((r) => (resolve = r)));
    return { onSend, resolve: (v: boolean) => resolve(v) };
  }
  const uploadFile = async (input: HTMLInputElement, name: string) => {
    await act(async () => {
      fireEvent.change(input, { target: { files: [new File([new Uint8Array([1])], name, { type: "image/png" })] } });
    });
  };

  it("🔴 承重1：发送中打下一条草稿 → 第一条成功后**新草稿仍在**（不被上一条成功清空）", async () => {
    const { onSend, resolve } = deferredSend();
    wrap(<Composer tier="low" balance={100} sending={false} {...props()} onSend={onSend} />);
    const ta = screen.getByLabelText(/输入问题/);
    fireEvent.change(ta, { target: { value: "第一条" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "发送" }));
    });
    expect(onSend).toHaveBeenCalledTimes(1);
    fireEvent.change(ta, { target: { value: "下一条草稿" } }); // 等待期间接着打下一条
    await act(async () => {
      resolve(true); // 第一条成功返回
      await Promise.resolve();
    });
    expect(ta).toHaveValue("下一条草稿"); // 🔴 新草稿没被抹
  });

  it("🔴 承重2：发送中新增附件 → 成功后**新附件仍在且预览可显示**，新附件 URL **未被 revoke**", async () => {
    const { onSend, resolve } = deferredSend();
    const { container } = wrap(<Composer tier="low" balance={100} sending={false} {...props()} onSend={onSend} />);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    // 附件 A（blob:mock-1）随文字一起发送。
    await uploadFile(fileInput, "a.png");
    await waitFor(() => expect(container.querySelector('img[src="blob:mock-1"]')).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/输入问题/), { target: { value: "看" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "发送" }));
    });
    expect(onSend).toHaveBeenCalledTimes(1);
    // 等待期间新增附件 B（blob:mock-2）。
    await uploadFile(fileInput, "b.png");
    await waitFor(() => expect(container.querySelector('img[src="blob:mock-2"]')).toBeTruthy());
    await act(async () => {
      resolve(true); // 第一条成功返回
      await Promise.resolve();
    });
    expect(container.querySelector('img[src="blob:mock-2"]')).toBeTruthy(); // 🔴 新附件 B 仍在、预览未碎
    expect(container.querySelector('img[src="blob:mock-1"]')).toBeTruthy(); // 本批 A 也仍在（触碰后全保留）
    expect(revokeSpy).not.toHaveBeenCalled(); // 🔴 触碰后**不 revoke 任何 URL**（含本批 A 的 mock-1）
  });

  it("🔴 承重2b：发送中**换附件**（删A加B，长度不变、asset_id 变）→ 成功后 B 仍在、不被误清（钉住 .every 判据）", async () => {
    const { onSend, resolve } = deferredSend();
    const { container } = wrap(<Composer tier="low" balance={100} sending={false} {...props()} onSend={onSend} />);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    // 附件 A（blob:mock-1）随文字一起发送。
    await uploadFile(fileInput, "a.png");
    await waitFor(() => expect(container.querySelector('img[src="blob:mock-1"]')).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/输入问题/), { target: { value: "看" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "发送" }));
    });
    expect(onSend).toHaveBeenCalledTimes(1);
    // 等待期间「换」附件：先删 A（移除按钮此刻不 disabled，handler 自己 revoke mock-1），再加 B（blob:mock-2）。
    // 结果集长度仍为 1，但 asset_id 与发送快照 [A] 不同 → 必须靠 .every(asset_id) 判为 touched。
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: copy.aibrain.attachRemove }));
    });
    await uploadFile(fileInput, "b.png");
    await waitFor(() => expect(container.querySelector('img[src="blob:mock-2"]')).toBeTruthy());
    await act(async () => {
      resolve(true); // 第一条成功返回
      await Promise.resolve();
    });
    expect(container.querySelector('img[src="blob:mock-2"]')).toBeTruthy(); // 🔴 换上的 B 仍在（未被误清）
    expect(revokeSpy).not.toHaveBeenCalledWith("blob:mock-2"); // 决不 revoke 换上的 B
  });

  it("承重3：发送中**没动** → 成功后文字清空、附件清空并 revoke 本批（正常路径别误伤）", async () => {
    const { onSend, resolve } = deferredSend();
    const { container } = wrap(<Composer tier="low" balance={100} sending={false} {...props()} onSend={onSend} />);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    await uploadFile(fileInput, "a.png");
    await waitFor(() => expect(container.querySelector('img[src="blob:mock-1"]')).toBeTruthy());
    const ta = screen.getByLabelText(/输入问题/);
    fireEvent.change(ta, { target: { value: "看" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "发送" }));
    });
    await act(async () => {
      resolve(true); // 不做任何改动，直接成功
      await Promise.resolve();
    });
    expect(ta).toHaveValue(""); // 文字清
    expect(container.querySelector('img[src="blob:mock-1"]')).toBeFalsy(); // 附件清
    expect(revokeSpy).toHaveBeenCalledWith("blob:mock-1"); // 本批 revoke
  });
});
