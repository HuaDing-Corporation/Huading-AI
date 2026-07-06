import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";

const taskMocks = vi.hoisted(() => ({ createAndTrack: vi.fn() }));

vi.mock("@/lib/api/hooks", () => ({
  useScriptGenerate: () => ({ mutateAsync: vi.fn().mockResolvedValue({ script: "s" }), isPending: false }),
  useUploadImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useBrandVoices: () => ({ data: [], isLoading: false }),
  useVoices: () => ({
    data: [
      { id: "v1", provider: "edge_tts", voice_code: "c", display_name: "声", gender: null, language: null }
    ]
  }),
  useAvatarPresets: () => ({
    data: [{ asset_id: "p1", display_name: "默认主播", thumbnail_url: "https://x/p1.jpg" }]
  }),
  useSubtitleTemplates: () => ({ data: [] }),
  useEstimateVideo: () => ({
    mutate: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    data: { estimated_credits: 10, unit: "credits" }
  })
}));
vi.mock("@/lib/videos/tasks-context", () => ({ useVideoTasks: () => taskMocks }));

import { NewVideoForm } from "./new-video-form";

afterEach(() => vi.clearAllMocks());

// 生成视频 now opens the 确定生成 dialog; the real submit happens on 确定.
async function fillAndSubmit() {
  fireEvent.change(screen.getByPlaceholderText(/输入一句话主题/), { target: { value: "咖啡" } });
  fireEvent.click(screen.getByText("默认主播")); // select preset avatar; voice auto-defaults
  fireEvent.click(screen.getByRole("button", { name: /生成视频/ }));
  fireEvent.click(await screen.findByRole("button", { name: "确定" }));
}

describe("NewVideoForm error display (P2)", () => {
  it("surfaces the backend error.message when 下单 fails (no more generic-only)", async () => {
    taskMocks.createAndTrack.mockRejectedValueOnce(
      new ApiError("Active subscription not found.", "subscription_not_found", 404)
    );
    render(<NewVideoForm />);
    await fillAndSubmit();
    await waitFor(() =>
      expect(screen.getByText("Active subscription not found.")).toBeInTheDocument()
    );
  });

  it("keeps the friendly localized copy for tenant_quota_exceeded", async () => {
    taskMocks.createAndTrack.mockRejectedValueOnce(
      new ApiError("Insufficient tenant quota.", "tenant_quota_exceeded", 403)
    );
    render(<NewVideoForm />);
    await fillAndSubmit();
    await waitFor(() =>
      expect(screen.getByText("额度不足，无法生成，请充值或精简任务")).toBeInTheDocument()
    );
  });
});
