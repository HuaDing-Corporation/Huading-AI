import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { ReversePromptResult } from "@/lib/api/reverse-prompt";
import { ReversePromptResultView } from "./reverse-prompt-result-view";

const FULL: ReversePromptResult = {
  target_format: "seedance_2_0",
  subject: "白色大理石上的保温杯",
  scene: "暖光桌面",
  composition: "居中特写",
  camera: "35mm 微俯拍",
  lighting: "柔和暖光",
  style_tags: ["产品广告", "极简"],
  motion_hint: "缓慢环绕",
  prompt_zh: "中文提示词内容ZH",
  prompt_en: "english prompt EN",
  negative_prompt: "低分辨率, 水印",
  selling_points: ["24 小时保温"],
  text_in_media: ["24H"],
  confidence: 0.82,
  disclaimer: "AI 近似重建，不保证完全复刻原素材。",
  fill_targets: {
    avatar_talk: { topic: "保温杯种草", script: "大家好……" },
    seedance_i2v: { topic: "保温杯卖点", scene_prompt: "暖光特写" },
    video_gen: { topic: "保温杯", prompt: "保温杯广告运镜" },
    photo: { topic: "白底保温杯特写" },
    ecom_model: { extra_prompt: "白底柔光" },
    ecom_poster: { title: "大促", subtitle: "5 折" }
  }
};

afterEach(() => vi.unstubAllGlobals());

describe("ReversePromptResultView（反推结果 + 带入 4 模块）", () => {
  const noop = () => undefined;

  it("渲染扁平结果块：中/英/反向提示词 + 主体等", () => {
    render(<ReversePromptResultView result={FULL} onApply={noop} onRegenerate={noop} onSave={noop} />);
    expect(screen.getByText("中文提示词内容ZH")).toBeInTheDocument();
    expect(screen.getByText("english prompt EN")).toBeInTheDocument();
    expect(screen.getByText("低分辨率, 水印")).toBeInTheDocument();
    expect(screen.getByText("白色大理石上的保温杯")).toBeInTheDocument();
  });

  it("近似重建红线 disclaimer 醒目呈现（BE 给则用其文案）", () => {
    render(<ReversePromptResultView result={FULL} onApply={noop} onRegenerate={noop} onSave={noop} />);
    expect(screen.getByText(FULL.disclaimer)).toBeInTheDocument();
  });

  it("BE 空 disclaimer（默认空串）→ 用前端兜底红线文案（近似重建不可少）", () => {
    render(
      <ReversePromptResultView
        result={{ ...FULL, disclaimer: "" }}
        onApply={noop}
        onRegenerate={noop}
        onSave={noop}
      />
    );
    expect(screen.getByText(copy.reverse.disclaimer)).toBeInTheDocument();
  });

  it("置信度以百分比呈现", () => {
    render(<ReversePromptResultView result={FULL} onApply={noop} onRegenerate={noop} onSave={noop} />);
    expect(screen.getByText(/82\s*%/)).toBeInTheDocument();
  });

  it("带入 5 模块按钮齐备（营销海报已下线）；全 fill_targets 在 → 均可点，点「数字人口播」以正确落点 apply", () => {
    const onApply = vi.fn();
    render(<ReversePromptResultView result={FULL} onApply={onApply} onRegenerate={noop} onSave={noop} />);
    for (const label of [
      copy.reverse.applyAvatar,
      copy.reverse.applyEcomVideo,
      copy.reverse.applyVideoGen,
      copy.reverse.applyPhoto,
      copy.reverse.applyEcomModel
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeEnabled();
    }
    // 营销海报入口已移除 → 无「带入·营销海报」按钮
    expect(screen.queryByRole("button", { name: copy.reverse.applyEcomPoster })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyAvatar }));
    expect(onApply).toHaveBeenCalledWith({ target: "avatar_talk", topic: "保温杯种草", script: "大家好……" });
  });

  it("点「带入·电商带货」→ scene_prompt 落 scenePrompt", () => {
    const onApply = vi.fn();
    render(<ReversePromptResultView result={FULL} onApply={onApply} onRegenerate={noop} onSave={noop} />);
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyEcomVideo }));
    expect(onApply).toHaveBeenCalledWith({ target: "seedance_i2v", topic: "保温杯卖点", scenePrompt: "暖光特写" });
  });

  it("点「带入·图片生成」→ photo.topic 落 prompt", () => {
    const onApply = vi.fn();
    render(<ReversePromptResultView result={FULL} onApply={onApply} onRegenerate={noop} onSave={noop} />);
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyPhoto }));
    expect(onApply).toHaveBeenCalledWith({ target: "photo", prompt: "白底保温杯特写" });
  });

  it("点「带入·AI 模特」→ ecom_model.extra_prompt 落 custom（tool=model）", () => {
    const onApply = vi.fn();
    render(<ReversePromptResultView result={FULL} onApply={onApply} onRegenerate={noop} onSave={noop} />);
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyEcomModel }));
    expect(onApply).toHaveBeenCalledWith({ target: "ecom_image", tool: "model", custom: "白底柔光" });
  });

  it("承重：缺某 fill_target 键 → 该模块「带入」置灰不可点，且不触发 apply", () => {
    const onApply = vi.fn();
    const onlyAvatar = {
      ...FULL,
      fill_targets: { avatar_talk: { topic: "x", script: "y" } }
    } as unknown as ReversePromptResult;
    render(<ReversePromptResultView result={onlyAvatar} onApply={onApply} onRegenerate={noop} onSave={noop} />);
    const ecomBtn = screen.getByRole("button", { name: copy.reverse.applyEcomModel });
    expect(ecomBtn).toBeDisabled();
    fireEvent.click(ecomBtn);
    expect(onApply).not.toHaveBeenCalled();
    // 而 avatar_talk 在 → 可点
    expect(screen.getByRole("button", { name: copy.reverse.applyAvatar })).toBeEnabled();
  });

  it("复制中文提示词 → 写入剪贴板", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<ReversePromptResultView result={FULL} onApply={noop} onRegenerate={noop} onSave={noop} />);
    // 中文提示词块的复制按钮（首个 copy 按钮）
    fireEvent.click(screen.getAllByRole("button", { name: copy.common.copy })[0]);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("中文提示词内容ZH"));
    // 🔴 await 状态落定（成功后 CopyableBlock setCopied(true) 在微任务里）→ 消 act(...) 警告。
    // 顺带正向锁死：真写进去了才显示「已复制」。
    expect(await screen.findByText(copy.common.copied)).toBeInTheDocument();
  });

  // 🔴 CLIPBOARD-TRUTH-0001：修这条**假守卫**。它叫「承重·非安全上下文（Review P3 修正）」，看起来像被守着，
  // 但它同步做负断言 —— 而坏实现的 setCopied(true) 落在 await 之后的**微任务**里，同步断言先跑完 →
  // 好实现坏实现**都绿**，那个守卫从写下那天起就没被真正测过。修法照 copyable-block.test.tsx:54：
  // **放行一次微任务再断言**（坏实现正是在这里置「已复制」的）。变异门：把 copyToClipboard 退化成
  // `await navigator.clipboard?.writeText(x); return true` → 本条转红。
  it("🔴 承重·非安全上下文：navigator.clipboard 缺失 → 点复制不谎报「已复制」（Review P3 修正）", async () => {
    vi.stubGlobal("navigator", {});
    render(<ReversePromptResultView result={FULL} onApply={noop} onRegenerate={noop} onSave={noop} />);
    fireEvent.click(screen.getAllByRole("button", { name: copy.common.copy })[0]);
    await new Promise((r) => setTimeout(r, 0)); // 放行微任务：坏实现在这里置「已复制」
    expect(screen.queryByText(copy.common.copied)).not.toBeInTheDocument();
  });

  it("重新反推 / 保存 回调可触发", () => {
    const onRegenerate = vi.fn();
    const onSave = vi.fn();
    render(<ReversePromptResultView result={FULL} onApply={noop} onRegenerate={onRegenerate} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.regenerate }));
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.save }));
    expect(onRegenerate).toHaveBeenCalled();
    expect(onSave).toHaveBeenCalled();
  });
});
