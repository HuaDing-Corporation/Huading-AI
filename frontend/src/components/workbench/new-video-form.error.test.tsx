import { fireEvent, screen, waitFor } from "@testing-library/react";
import { render } from "@/lib/billing/test-utils";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import { server } from "@/mocks/server";

const taskMocks = vi.hoisted(() => ({ createAndTrack: vi.fn() }));

vi.mock("@/lib/api/hooks", () => ({
  useScriptGenerate: () => ({ mutateAsync: vi.fn().mockResolvedValue({ script: "s" }), isPending: false }),
  useUploadImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadAvatarVideo: () => ({ mutateAsync: vi.fn(), isPending: false }),
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
vi.mock("@/lib/auth/auth-context", () => ({ useAuth: () => ({ session: { role: "admin", user: { permissions: ["voice_clone_vip"] } }, ready: true }) }));

import { NewVideoForm } from "./new-video-form";

const API = "http://localhost:8000";

afterEach(() => vi.clearAllMocks());

// 生成视频 now opens the 确定生成 dialog; the real submit happens on 确定.
async function fillAndSubmit() {
  fireEvent.change(screen.getByPlaceholderText(/输入一句话主题/), { target: { value: "咖啡" } });
  fireEvent.click(screen.getByText("默认主播")); // select preset avatar; voice auto-defaults
  fireEvent.click(screen.getByRole("button", { name: /生成视频/ }));
  const confirm = await screen.findByRole("button", { name: "确定" });
  await waitFor(() => expect(confirm).toBeEnabled());
  fireEvent.click(confirm);
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

  // 🔴 PRICING-UI-0001：本条**此前在给 bug 站岗** —— 它用小写 `tenant_quota_exceeded` 造 ApiError，
  //    恰好喂中了 error-text.ts 里同样写成小写的判断，于是"绿"了。而 BE 实际发的是**大写**
  //    `TENANT_QUOTA_EXCEEDED`（services/quota.py:174/:530），真实链路上这条友好文案从未出现过，
  //    用户看到的一直是英文 "Insufficient tenant quota."。
  //    现在按 BE 的真实大写断言（并保留小写一条，因为映射改成了大小写不敏感）。
  //    变异：把 error-text.ts 改回 `err.code === "tenant_quota_exceeded"` → 大写这条红。
  it("keeps the friendly localized copy for TENANT_QUOTA_EXCEEDED (BE 真实大写)", async () => {
    taskMocks.createAndTrack.mockRejectedValueOnce(
      new ApiError("Insufficient tenant quota.", "TENANT_QUOTA_EXCEEDED", 403)
    );
    render(<NewVideoForm />);
    await fillAndSubmit();
    await waitFor(() =>
      expect(screen.getByText("额度不足，无法生成，请充值或精简任务")).toBeInTheDocument()
    );
  });

  it("小写 tenant_quota_exceeded 同样命中（映射大小写不敏感，防 BE 改回来又失效）", async () => {
    taskMocks.createAndTrack.mockRejectedValueOnce(
      new ApiError("Insufficient tenant quota.", "tenant_quota_exceeded", 403)
    );
    render(<NewVideoForm />);
    await fillAndSubmit();
    await waitFor(() =>
      expect(screen.getByText("额度不足，无法生成，请充值或精简任务")).toBeInTheDocument()
    );
  });

  it("BILLABLE_TEXT_REQUIRED closes pricing, focuses the script editor, and never submits", async () => {
    server.use(
      http.post(`${API}/api/v1/videos/estimate`, () =>
        HttpResponse.json(
          {
            data: null,
            error: { code: "BILLABLE_TEXT_REQUIRED", message: "品牌音色视频需要文案。" },
            request_id: "missing-billable-text"
          },
          { status: 422 }
        )
      )
    );
    render(<NewVideoForm />);
    fireEvent.change(screen.getByPlaceholderText(/输入一句话主题/), { target: { value: "咖啡" } });
    fireEvent.click(screen.getByText("默认主播"));

    fireEvent.click(screen.getByRole("button", { name: /生成视频/ }));

    const scriptEditor = document.getElementById("video-script");
    expect(scriptEditor).not.toBeNull();
    await waitFor(() => expect(scriptEditor).toHaveFocus());
    expect(screen.getByRole("alert")).toHaveTextContent("品牌音色视频需要文案");
    expect(screen.queryByRole("dialog", { name: "确定生成" })).not.toBeInTheDocument();
    expect(taskMocks.createAndTrack).not.toHaveBeenCalled();
  });
});
