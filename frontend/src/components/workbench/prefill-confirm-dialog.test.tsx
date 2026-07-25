import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import { fillTargetToPrefill, type WorkbenchPrefill } from "@/lib/api/reverse-prompt";
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

const renderDialog = (prefill: WorkbenchPrefill, sourceDurationSec?: number) => {
  const onConfirm = vi.fn();
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
/** 取确认时下发的载荷；`in` 判据（键在不在）正是承重门2 要区分「缺席 ≠ 空串」的地方，故保留原样断言。 */
const payloadOf = (m: ReturnType<typeof vi.fn>) => m.mock.calls[0][0] as Record<string, unknown>;

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
    renderDialog(VIDEO_GEN, 180);
    expect(screen.getByText(copy.reverse.applyClampNote(180, "视频生成", 15))).toBeInTheDocument();
  });

  it("clamp 提示：原时长未知 → 退化文案，仍明确告知已按上限带入（不静默）", () => {
    renderDialog(VIDEO_GEN, undefined);
    expect(screen.getByText(copy.reverse.applyClampNoteNoOrigin("视频生成", 15))).toBeInTheDocument();
  });

  it("未 clamp → 不显示提示（不制造假警告）", () => {
    renderDialog({ ...VIDEO_GEN, durationClamped: false }, 12);
    expect(screen.queryByText(/已按上限带入/)).not.toBeInTheDocument();
  });

  // ── 分镜表（§八 M2：shot_section 独立键，前端**只拼不拆**）─────────────────────────
  // 承重门12。🔴 变异点 = prefill-confirm-dialog.tsx composePrefill 里
  //   `out[item.key] = on("shotSection") ? joinShotSection(...) : textOf(item)` 这一支：
  //   把它删成 `out[item.key] = textOf(item)`（即不拼），本条与下面 seedance 那条同时红。
  it("🔴 承重门12 · shot_section 只拼不拆：主提示词原样、分镜单列一项，勾选时接在末尾", () => {
    const onConfirm = renderDialog({
      target: "video_gen",
      prompt: "Subject: bottle.\nStyle: ad",
      shotSection: "Shots: 0-4s 特写；4-10s 场景。"
    });
    expect(screen.getByRole("checkbox", { name: copy.reverse.applyItemShots })).toBeInTheDocument();
    // 🔴 主提示词项 = BE 原串**一字未动**（上一版这里要靠正则把段头切掉，现在契约保证它本就不含分镜段）
    expect(
      (screen.getByLabelText(copy.reverse.applyItemEditAria(copy.reverse.applyItemPrompt)) as HTMLTextAreaElement).value
    ).toBe("Subject: bottle.\nStyle: ad");
    // 分镜项 = BE 给的完整段（**含段头**，前端不再自造 "Shots: " 前缀）
    expect(
      (screen.getByLabelText(copy.reverse.applyItemEditAria(copy.reverse.applyItemShots)) as HTMLTextAreaElement).value
    ).toBe("Shots: 0-4s 特写；4-10s 场景。");
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyConfirmSubmit }));
    const payload = payloadOf(onConfirm);
    expect(payload.prompt).toBe("Subject: bottle.\nStyle: ad\n\nShots: 0-4s 特写；4-10s 场景。");
    // 伪项不许当独立键下发（目标表单没有 shotSection 控件，发过去只会被静默丢弃）
    expect("shotSection" in payload).toBe(false);
  });

  // 🔴 seedance_i2v 的主提示词是 **scenePrompt** 而非 prompt —— 若 composePrefill 把宿主写死成 "prompt"，
  //    本条会红（分镜勾了却没进任何字段）。这是「两个模块字段名不同」这一真实差异的护栏。
  it("🔴 承重门12 · 电商带货：分镜拼进 scenePrompt（宿主字段按模块取，不写死 prompt）", () => {
    const onConfirm = renderDialog({
      target: "seedance_i2v",
      topic: "保温杯",
      scenePrompt: "暖光特写",
      shotSection: "Shots: 0-4s 特写。"
    });
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyConfirmSubmit }));
    const payload = payloadOf(onConfirm);
    expect(payload.scenePrompt).toBe("暖光特写\n\nShots: 0-4s 特写。");
    expect("shotSection" in payload).toBe(false);
  });

  it("🔴 取消「分镜表」→ 带入的提示词里不含分镜段（开关真的有用，不是摆设）", () => {
    const onConfirm = renderDialog({
      target: "video_gen",
      prompt: "Subject: bottle.\nStyle: ad",
      shotSection: "Shots: 0-4s 特写；4-10s 场景。"
    });
    fireEvent.click(screen.getByRole("checkbox", { name: copy.reverse.applyItemShots }));
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyConfirmSubmit }));
    expect((onConfirm.mock.calls[0][0] as Record<string, unknown>).prompt).toBe("Subject: bottle.\nStyle: ad");
  });

  it("BE 未给 shot_section（图片反推/老结构/photo 模块）→ 不产生分镜项（不造点了没用的开关）", () => {
    renderDialog({ target: "video_gen", prompt: "Subject: bottle." });
    expect(screen.queryByRole("checkbox", { name: copy.reverse.applyItemShots })).not.toBeInTheDocument();
  });

  // 🔴 Code Review P1：取消主提示词后，分镜段无处可去（它是拼进主提示词才带走的）→ 分镜表必须一并置灰，
  //    否则就是一个「勾着、可编辑、确认后却什么都没发生」的死开关。
  it("🔴 取消「主提示词」→ 「分镜表」随之置灰且不参与带入（不留死开关）", () => {
    const onConfirm = renderDialog({
      target: "video_gen",
      prompt: "Subject: bottle.\nStyle: ad",
      shotSection: "Shots: 0-4s 特写。",
      negativePrompt: "水印"
    });
    fireEvent.click(screen.getByRole("checkbox", { name: copy.reverse.applyItemPrompt }));
    const shots = screen.getByRole("checkbox", { name: copy.reverse.applyItemShots });
    expect(shots).toBeDisabled();
    expect(shots).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyConfirmSubmit }));
    const payload = payloadOf(onConfirm);
    expect("prompt" in payload).toBe(false); // 主提示词没带 → 分镜也无处可去
    expect(payload.negativePrompt).toBe("水印"); // 其余项照常
  });

  it("🔴 一项都不勾 → 「确认带入」禁用（不给点了没反应的按钮）", () => {
    renderDialog({ target: "avatar_talk", topic: "保温杯种草", script: "大家好" });
    fireEvent.click(screen.getByRole("checkbox", { name: copy.reverse.applyItemTopic }));
    fireEvent.click(screen.getByRole("checkbox", { name: copy.reverse.applyItemScript }));
    expect(screen.getByRole("button", { name: copy.reverse.applyConfirmSubmit })).toBeDisabled();
  });

  it("取消勾选的文本仍可读（readOnly 而非 disabled——不带入 ≠ 不让看）", () => {
    renderDialog(VIDEO_GEN);
    fireEvent.click(screen.getByRole("checkbox", { name: copy.reverse.applyItemNegative }));
    const ta = screen.getByLabelText(copy.reverse.applyItemEditAria(copy.reverse.applyItemNegative));
    expect(ta).toHaveAttribute("readonly");
    expect(ta).not.toBeDisabled(); // disabled 会让读屏/键盘彻底读不到内容
  });
});

// 🔴 Code Review P1：BE 这套 schema 的惯例是「缺省空串」而非省略键。若把 "" 当作「给了」，
//    「原素材没有负面提示词」就会变成「把用户已写的负面词清空」——承重门2 的同一根线，另一种触发方式。
describe("fillTargetToPrefill · 空串按「没给」处理（不清空用户已填）", () => {
  it("🔴 negative_prompt/master_prompt 为空串 → 载荷里**没有**这两个键", () => {
    const prefill = fillTargetToPrefill("photo", {
      photo: { topic: "主提示词", master_prompt: "", negative_prompt: "", aspect_ratio: null }
    } as never) as Record<string, unknown>;
    expect(prefill.prompt).toBe("主提示词");
    expect("negativePrompt" in prefill).toBe(false);
    expect("masterPrompt" in prefill).toBe(false);
    expect("aspectRatio" in prefill).toBe(false); // null 同理
  });

  it("有内容时照常带入（证明上一条不是把功能整个关掉）", () => {
    const prefill = fillTargetToPrefill("photo", {
      photo: { topic: "主提示词", master_prompt: "总控", negative_prompt: "水印", aspect_ratio: "9:16" }
    } as never) as Record<string, unknown>;
    expect(prefill.masterPrompt).toBe("总控");
    expect(prefill.negativePrompt).toBe("水印");
    expect(prefill.aspectRatio).toBe("9:16");
  });
});
