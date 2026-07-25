import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/hooks", () => ({
  useUploadImage: vi.fn(),
  useUploadReverseVideo: vi.fn(),
  useReverseFromAsset: vi.fn(),
  useRegenerateReversePrompt: vi.fn(),
  useSaveReversePrompt: vi.fn(),
  // §八 M4：本文件走**图片**路径（图片无计费门，estimate 不会被调）→ 常态桩即可；
  // 计费门本身的承重在 reverse-prompt-form.video.test.tsx。
  useEstimateReversePrompt: () => ({ mutate: vi.fn(), reset: vi.fn(), data: undefined, isPending: false, isError: false })
}));

import {
  useRegenerateReversePrompt,
  useReverseFromAsset,
  useSaveReversePrompt,
  useUploadImage,
  useUploadReverseVideo
} from "@/lib/api/hooks";
import type { Mock } from "vitest";
import { ApiError } from "@/lib/api/client";
import { copy } from "@/lib/copy";
import type { ReversePromptJobRead, ReversePromptResult } from "@/lib/api/reverse-prompt";
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
  selling_points: [],
  text_in_media: [],
  disclaimer: "",
  confidence: 0.8,
  fill_targets: {
    avatar_talk: { topic: "保温杯种草", script: "大家好" },
    seedance_i2v: { topic: "卖点", scene_prompt: "暖光" },
    video_gen: { topic: "杯", prompt: "运镜" },
    photo: { topic: "白底杯" },
    ecom_model: { extra_prompt: "白底" },
    ecom_poster: { title: "大促", subtitle: "5 折" }
  }
};

// 镜像 BE ReversePromptJobRead：成功 status="succeeded" + result。
const JOB: ReversePromptJobRead = {
  id: "rp-1",
  status: "succeeded",
  source_kind: "image",
  target_format: "seedance_2_0",
  result: RESULT,
  error_code: null,
  error_message: null,
  prompt_tokens: 0,
  completion_tokens: 0,
  credits: 0,
  cost_cents: 0,
  created_at: "1970-01-01T00:00:00Z",
  updated_at: "1970-01-01T00:00:00Z",
  saved_at: null
};

function stubHooks(reverseImpl: Mock) {
  (useUploadImage as Mock).mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ asset_id: "aid-1" }), isPending: false });
  (useUploadReverseVideo as Mock).mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ asset_id: "video-asset-1" }), isPending: false });
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

  it("合法图 → 上传拿 asset_id → 反推(请求体仅 source_asset_id) → 出结果块 + 带入", async () => {
    const reverse = vi.fn().mockResolvedValue(JOB);
    stubHooks(reverse);
    render(<ReversePromptForm />);
    selectFile();
    const analyzeBtn = screen.getByRole("button", { name: copy.reverse.analyze });
    await waitFor(() => expect(analyzeBtn).toBeEnabled());
    fireEvent.click(analyzeBtn);
    // FIX1：请求体只发 source_asset_id（无 output_language/detail_level，BE forbid 否则 422）
    await waitFor(() => expect(reverse).toHaveBeenCalledWith({ source_asset_id: "aid-1" }));
    expect(await screen.findByText("中文提示词ZZZ")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: copy.reverse.applyAvatar })).toBeEnabled();
    expect(screen.getByRole("button", { name: copy.reverse.applyEcomModel })).toBeEnabled();
  });

  it("点「带入·数字人口播」→ 以正确落点冒泡 onApplyPrefill", async () => {
    const reverse = vi.fn().mockResolvedValue(JOB);
    stubHooks(reverse);
    const onApplyPrefill = vi.fn();
    render(<ReversePromptForm onApplyPrefill={onApplyPrefill} />);
    selectFile();
    const analyzeBtn = screen.getByRole("button", { name: copy.reverse.analyze });
    await waitFor(() => expect(analyzeBtn).toBeEnabled());
    fireEvent.click(analyzeBtn);
    await screen.findByText("中文提示词ZZZ");
    // REVERSE-DEEP-UI-0001 · D3-④：带入前先弹确认窗（可逐项取消/编辑）→ 点「确认带入」才真正落值。
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyAvatar }));
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyConfirmSubmit }));
    expect(onApplyPrefill).toHaveBeenCalledWith({ target: "avatar_talk", topic: "保温杯种草", script: "大家好" });
  });

  it("反推失败(BE AppError→apiFetch throw)→ 显友好中文兜底，不泄英文/裸串，不渲染结果", async () => {
    const reverse = vi.fn().mockRejectedValue(new ApiError("Reverse prompt failed.", "REVERSE_PROMPT_FAILED", 502));
    stubHooks(reverse);
    render(<ReversePromptForm />);
    selectFile();
    const analyzeBtn = screen.getByRole("button", { name: copy.reverse.analyze });
    await waitFor(() => expect(analyzeBtn).toBeEnabled());
    fireEvent.click(analyzeBtn);
    expect(await screen.findByText(copy.errors.reverseFailed)).toBeInTheDocument();
    expect(screen.queryByText("Reverse prompt failed.")).not.toBeInTheDocument();
    expect(screen.queryByText("中文提示词ZZZ")).not.toBeInTheDocument();
  });
});
