import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/hooks", () => ({
  useUploadImage: vi.fn(),
  useReverseFromAsset: vi.fn(),
  useRegenerateReversePrompt: vi.fn(),
  useSaveReversePrompt: vi.fn()
}));

import {
  useRegenerateReversePrompt,
  useReverseFromAsset,
  useSaveReversePrompt,
  useUploadImage
} from "@/lib/api/hooks";
import type { Mock } from "vitest";
import { copy } from "@/lib/copy";
import type { ReversePromptResult } from "@/lib/api/reverse-prompt";
import { ReversePromptForm } from "./reverse-prompt-form";

const RESULT: ReversePromptResult = {
  target_format: "seedance_2_0",
  subject: "保温杯",
  scene: "暖光桌面",
  composition: "居中",
  camera: "35mm",
  lighting: "柔光",
  style_tags: ["极简"],
  motion_hint: "环绕",
  prompt_zh: "中文提示词ZZZ",
  prompt_en: "en prompt",
  negative_prompt: "水印",
  confidence: 0.8,
  fill_targets: {
    avatar_talk: { topic: "保温杯种草", script: "大家好" },
    seedance_i2v: { topic: "卖点", scene_prompt: "暖光" },
    video_gen: { prompt: "运镜", topic: "杯" },
    ecom_image: { topic: "杯", extra_prompt: "白底" }
  }
};

function stubHooks(reverseImpl: Mock) {
  (useUploadImage as Mock).mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ asset_id: "aid-1" }), isPending: false });
  (useReverseFromAsset as Mock).mockReturnValue({ mutateAsync: reverseImpl, isPending: false });
  (useRegenerateReversePrompt as Mock).mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
  (useSaveReversePrompt as Mock).mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ saved: true }), isPending: false });
}

function selectFile(type = "image/png", name = "a.png") {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(["x"], name, { type });
  fireEvent.change(input, { target: { files: [file] } });
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:x"), configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
});

describe("ReversePromptForm 状态机（上传→反推→带入）", () => {
  it("承重·客户端红线：选非白名单类型 → 显友好中文，且不触发上传", () => {
    const reverse = vi.fn();
    stubHooks(reverse);
    const upload = { mutateAsync: vi.fn(), isPending: false };
    (useUploadImage as Mock).mockReturnValue(upload);
    render(<ReversePromptForm />);
    selectFile("image/gif", "a.gif");
    expect(screen.getByText(copy.errors.uploadType)).toBeInTheDocument();
    expect(upload.mutateAsync).not.toHaveBeenCalled();
  });

  it("合法图 → 上传拿 asset_id → 反推 → 出结果块 + 带入 4 模块", async () => {
    const reverse = vi.fn().mockResolvedValue({ jobId: "rp-1", status: "completed", result: RESULT });
    stubHooks(reverse);
    render(<ReversePromptForm />);
    selectFile();
    const analyzeBtn = screen.getByRole("button", { name: copy.reverse.analyze });
    await waitFor(() => expect(analyzeBtn).toBeEnabled());
    fireEvent.click(analyzeBtn);
    // 反推请求只带 source_asset_id + 语言 + 细节
    await waitFor(() =>
      expect(reverse).toHaveBeenCalledWith({ source_asset_id: "aid-1", output_language: "bilingual", detail_level: "standard" })
    );
    expect(await screen.findByText("中文提示词ZZZ")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: copy.reverse.applyAvatar })).toBeEnabled();
  });

  it("点「带入·数字人口播」→ 以正确落点冒泡 onApplyPrefill", async () => {
    const reverse = vi.fn().mockResolvedValue({ jobId: "rp-1", status: "completed", result: RESULT });
    stubHooks(reverse);
    const onApplyPrefill = vi.fn();
    render(<ReversePromptForm onApplyPrefill={onApplyPrefill} />);
    selectFile();
    const analyzeBtn = screen.getByRole("button", { name: copy.reverse.analyze });
    await waitFor(() => expect(analyzeBtn).toBeEnabled());
    fireEvent.click(analyzeBtn);
    await screen.findByText("中文提示词ZZZ");
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyAvatar }));
    expect(onApplyPrefill).toHaveBeenCalledWith({ target: "avatar_talk", topic: "保温杯种草", script: "大家好" });
  });

  it("反推返回 failed → 显友好中文兜底，不渲染结果", async () => {
    const reverse = vi.fn().mockResolvedValue({ jobId: "rp-1", status: "failed", error_code: "X", result: null });
    stubHooks(reverse);
    render(<ReversePromptForm />);
    selectFile();
    const analyzeBtn = screen.getByRole("button", { name: copy.reverse.analyze });
    await waitFor(() => expect(analyzeBtn).toBeEnabled());
    fireEvent.click(analyzeBtn);
    expect(await screen.findByText(copy.errors.reverseFailed)).toBeInTheDocument();
    expect(screen.queryByText("中文提示词ZZZ")).not.toBeInTheDocument();
  });
});
