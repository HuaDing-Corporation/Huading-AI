import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { WorkbenchPrefill } from "@/lib/api/reverse-prompt";
import { PrefillConfirmDialog } from "./prefill-confirm-dialog";

/**
 * REVERSE-DEEP-UI-0001 · 范围3 带入确认弹窗单测 —— 钉住四条规则：
 *  ① 默认全部勾选（傻瓜式）；② 取消勾选 = 该键**不下发**（不是空串）；
 *  ③ 长文本可就地编辑，编辑结果即实际带入值；④ 目标模块接不了的项**根本不渲染**（不造死开关）。
 * clamp 提示与页面级链路在 page.reverse-deep.test.tsx 里端到端钉。
 */
const VIDEO_GEN: WorkbenchPrefill = {
  target: "video_gen",
  prompt: "Subject: bottle.\nStyle: product ad",
  negativePrompt: "水印, 低分辨率",
  aspectRatio: "9:16",
  durationSec: 15,
  durationClamped: true,
  generateAudio: false
};

const renderDialog = (prefill: WorkbenchPrefill, onConfirm = vi.fn(), sourceDurationSec?: number) => {
  render(
    <PrefillConfirmDialog
      open
      prefill={prefill}
      moduleLabel="带入 · 视频生成"
      sourceDurationSec={sourceDurationSec}
      onConfirm={onConfirm}
      onCancel={vi.fn()}
    />
  );
  return onConfirm;
};

describe("PrefillConfirmDialog（带入前确认）", () => {
  it("🔴 默认全部勾选 → 直接确认即原样带入全部字段", () => {
    const onConfirm = renderDialog(VIDEO_GEN);
    for (const cb of screen.getAllByRole("checkbox")) expect(cb).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyConfirmSubmit }));
    expect(onConfirm).toHaveBeenCalledWith({
      target: "video_gen",
      prompt: VIDEO_GEN.prompt,
      negativePrompt: "水印, 低分辨率",
      aspectRatio: "9:16",
      durationSec: 15,
      generateAudio: false
    });
  });

  it("🔴 取消勾选 → 该键**整体不出现**在载荷里（不是给空串；表单据此跳过、控件保持原样）", () => {
    const onConfirm = renderDialog(VIDEO_GEN);
    fireEvent.click(screen.getByRole("checkbox", { name: copy.reverse.applyItemNegative }));
    fireEvent.click(screen.getByRole("checkbox", { name: copy.reverse.applyItemAspect }));
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyConfirmSubmit }));
    const payload = onConfirm.mock.calls[0][0] as Record<string, unknown>;
    expect("negativePrompt" in payload).toBe(false); // 🔴 不是 ""，是压根没有这个键
    expect("aspectRatio" in payload).toBe(false);
    expect(payload.prompt).toBe(VIDEO_GEN.prompt); // 其余项照常带入
  });

  it("🔴 长文本就地编辑 → 编辑后的文字才是实际带入值", () => {
    const onConfirm = renderDialog(VIDEO_GEN);
    fireEvent.change(screen.getByLabelText(copy.reverse.applyItemEditAria(copy.reverse.applyItemNegative)), {
      target: { value: "我改过的负面词" }
    });
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyConfirmSubmit }));
    expect((onConfirm.mock.calls[0][0] as Record<string, unknown>).negativePrompt).toBe("我改过的负面词");
  });

  it("🔴 接不了的项不渲染：口播（无画面类字段）只出主题/文案，不出比例/时长/音频开关", () => {
    renderDialog({ target: "avatar_talk", topic: "保温杯种草", script: "大家好" });
    expect(screen.getByRole("checkbox", { name: copy.reverse.applyItemTopic })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: copy.reverse.applyItemScript })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: copy.reverse.applyItemAspect })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: copy.reverse.applyItemDuration })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: copy.reverse.applyItemGenerateAudio })).not.toBeInTheDocument();
  });

  it("clamp 提示：原时长已知 → 逐字给出「原视频 N 秒 / 上限 M 秒 / 已按上限带入」", () => {
    renderDialog(VIDEO_GEN, vi.fn(), 180);
    expect(screen.getByText(copy.reverse.applyClampNote(180, "带入 · 视频生成", 15))).toBeInTheDocument();
  });

  it("clamp 提示：原时长未知 → 退化文案，仍明确告知已按上限带入（不静默）", () => {
    renderDialog(VIDEO_GEN, vi.fn(), undefined);
    expect(screen.getByText(copy.reverse.applyClampNoteNoOrigin("带入 · 视频生成", 15))).toBeInTheDocument();
  });

  it("未 clamp → 不显示提示（不制造假警告）", () => {
    renderDialog({ ...VIDEO_GEN, durationClamped: false }, vi.fn(), 12);
    expect(screen.queryByText(/已按上限带入/)).not.toBeInTheDocument();
  });

  // ── 分镜表（§4.3 Shots 段）──────────────────────────────────────────────
  it("🔴 提示词末尾带 Shots 段 → 拆成「主提示词 + 分镜表」两项；默认勾选时原样拼回", () => {
    const onConfirm = renderDialog({
      target: "video_gen",
      prompt: "Subject: bottle.\nStyle: ad\nShots: 0-4s 特写；4-10s 场景。"
    });
    expect(screen.getByRole("checkbox", { name: copy.reverse.applyItemShots })).toBeInTheDocument();
    // 主提示词项里已不含分镜段（避免与分镜项重复）
    expect(
      (screen.getByLabelText(copy.reverse.applyItemEditAria(copy.reverse.applyItemPrompt)) as HTMLTextAreaElement).value
    ).toBe(
      "Subject: bottle.\nStyle: ad"
    );
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyConfirmSubmit }));
    expect((onConfirm.mock.calls[0][0] as Record<string, unknown>).prompt).toBe(
      "Subject: bottle.\nStyle: ad\n\nShots: 0-4s 特写；4-10s 场景。"
    );
  });

  it("🔴 取消「分镜表」→ 带入的提示词里不含分镜段（开关真的有用，不是摆设）", () => {
    const onConfirm = renderDialog({
      target: "video_gen",
      prompt: "Subject: bottle.\nStyle: ad\nShots: 0-4s 特写；4-10s 场景。"
    });
    fireEvent.click(screen.getByRole("checkbox", { name: copy.reverse.applyItemShots }));
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyConfirmSubmit }));
    expect((onConfirm.mock.calls[0][0] as Record<string, unknown>).prompt).toBe("Subject: bottle.\nStyle: ad");
  });

  it("无 Shots 段（图片反推/老结构）→ 不产生分镜项（不造点了没用的开关）", () => {
    renderDialog({ target: "video_gen", prompt: "Subject: bottle." });
    expect(screen.queryByRole("checkbox", { name: copy.reverse.applyItemShots })).not.toBeInTheDocument();
  });
});
