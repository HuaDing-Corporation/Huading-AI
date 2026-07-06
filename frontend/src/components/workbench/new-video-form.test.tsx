import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Deferred upload promise so we can resolve it AFTER a remove.
const h = vi.hoisted(() => {
  let resolve!: (v: unknown) => void;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
});

vi.mock("@/lib/api/hooks", () => ({
  useScriptGenerate: () => ({ mutateAsync: vi.fn().mockResolvedValue({ script: "s" }), isPending: false }),
  useUploadImage: () => ({ mutateAsync: vi.fn(() => h.promise), isPending: false }),
  useUploadAvatarVideo: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useVoices: () => ({
    data: [
      { id: "v1", provider: "edge_tts", voice_code: "c", display_name: "声", gender: null, language: null }
    ]
  }),
  useAvatarPresets: () => ({ data: [] }),
  useSubtitleTemplates: () => ({ data: [] }),
  useEstimateVideo: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, data: undefined })
}));
vi.mock("@/lib/videos/tasks-context", () => ({ useVideoTasks: () => ({ createAndTrack: vi.fn() }) }));

import { NewVideoForm } from "./new-video-form";

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => vi.restoreAllMocks());

describe("NewVideoForm upload race (P1 async edge)", () => {
  it("does not repopulate avatarAssetId when the upload resolves AFTER remove", async () => {
    render(<NewVideoForm />);
    // Topic present + voice auto-defaults to v1, so avatarAssetId is the only
    // thing that would (wrongly) enable 生成 if the late upload repopulated it.
    fireEvent.change(screen.getByPlaceholderText(/输入一句话主题/), { target: { value: "咖啡" } });

    // Start an upload (preview appears, onUpload runs, mutateAsync pending).
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "a.png", { type: "image/png" })] } });

    // Remove BEFORE the upload resolves → supersedes the in-flight upload.
    fireEvent.click(screen.getByLabelText("移除图片"));

    // Now let the stale upload resolve — it must NOT repopulate the asset id.
    h.resolve({ asset_id: "upload-1", type: "avatar_image", status: "ready" });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /生成视频/ })).toBeDisabled();
    });
  });
});
