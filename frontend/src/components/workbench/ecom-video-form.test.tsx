import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const taskMocks = vi.hoisted(() => ({ createAndTrack: vi.fn() }));
const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const estimateMock = vi.hoisted(() => ({ mutate: vi.fn() }));
const scenePromptMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const scriptMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));

vi.mock("@/lib/api/hooks", () => ({
  useScriptGenerate: () => ({ mutateAsync: scriptMock.mutateAsync, isPending: false }),
  useScenePromptGenerate: () => ({ mutateAsync: scenePromptMock.mutateAsync, isPending: false }),
  useUploadProductImage: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: false }),
  useEstimateVideo: () => ({
    mutate: estimateMock.mutate,
    reset: vi.fn(),
    isPending: false,
    data: { estimated_credits: 12, unit: "credits" }
  }),
  useVoices: () => ({
    data: [
      { id: "v1", provider: "doubao", voice_code: "c", display_name: "豆包女声", gender: null, language: null }
    ]
  })
}));
vi.mock("@/lib/videos/tasks-context", () => ({ useVideoTasks: () => taskMocks }));

import { EcomVideoForm } from "./ecom-video-form";

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadMock.mutateAsync.mockResolvedValue({ image_key: "uploads/abc123.png" });
  scenePromptMock.mutateAsync.mockResolvedValue({ scene_prompt: "明亮影棚，产品特写旋转" });
  scriptMock.mutateAsync.mockResolvedValue({ script: "s" });
});
afterEach(() => vi.clearAllMocks());

function selectProductImage() {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], "p.png", { type: "image/png" })] } });
}

describe("EcomVideoForm (电商带货 i2v)", () => {
  it("renders 视频时长 above the 主题/卖点 input (duration-first layout)", () => {
    render(<EcomVideoForm />);
    const durationLegend = screen.getByText("视频时长（与文案、字幕一致）");
    const topicInput = screen.getByPlaceholderText(/输入产品卖点/);
    // DurationPicker must come before the topic input in document order.
    expect(
      durationLegend.compareDocumentPosition(topicInput) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });

  it("disables 生成 with a hint until both 卖点 and 产品图 are provided", async () => {
    render(<EcomVideoForm />);
    const generate = screen.getByRole("button", { name: /生成视频/ });

    // Pristine: missing 卖点 → disabled + topic hint.
    expect(generate).toBeDisabled();
    expect(screen.getByText("请先输入产品卖点")).toBeInTheDocument();

    // 卖点 only: still disabled, now hints to upload the product image.
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "保温杯" } });
    expect(generate).toBeDisabled();
    expect(screen.getByText("请上传产品图后再生成")).toBeInTheDocument();

    // + 产品图 → enabled, hint cleared.
    selectProductImage();
    await waitFor(() => expect(generate).toBeEnabled());
    expect(screen.queryByText("请上传产品图后再生成")).not.toBeInTheDocument();
  });

  it("uploads the product image (→image_key) then submits the seedance_i2v body", async () => {
    render(<EcomVideoForm />);
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "316 不锈钢保温杯" } });
    selectProductImage();

    // Upload went through the PRODUCT-image hook (POST /uploads → image_key),
    // not the avatar one.
    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(1));

    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    const [request, topic] = taskMocks.createAndTrack.mock.calls[0];
    expect(request).toEqual({
      topic: "316 不锈钢保温杯",
      script: undefined,
      video_mode: "seedance_i2v",
      image_key: "uploads/abc123.png",
      voice_id: "v1",
      duration_sec: 30,
      speed: 1,
      aspect_ratio: "9:16",
      subtitle_enabled: true,
      apply_visible_label: false
    });
    expect(topic).toBe("316 不锈钢保温杯");
    // i2v must NOT carry the avatar field.
    expect(request).not.toHaveProperty("avatar_asset_id");
  });

  it("submits the selected duration gear in duration_sec", async () => {
    render(<EcomVideoForm />);
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "保温杯" } });
    selectProductImage();
    fireEvent.click(screen.getByRole("button", { name: "45 秒" }));

    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({
      video_mode: "seedance_i2v",
      duration_sec: 45
    });
  });

  it("submits a custom duration and blocks out-of-range values", async () => {
    render(<EcomVideoForm />);
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "保温杯" } });
    selectProductImage();
    fireEvent.click(screen.getByRole("button", { name: "自定义" }));

    const generate = screen.getByRole("button", { name: /生成视频/ });
    const durationInput = screen.getByLabelText("自定义时长（秒）");

    // Out of range → disabled + error, no submit.
    fireEvent.change(durationInput, { target: { value: "200" } });
    await waitFor(() => expect(generate).toBeDisabled());
    expect(screen.getByText("请输入 5–120 秒")).toBeInTheDocument();

    // Valid custom → enabled, submits the custom duration_sec.
    fireEvent.change(durationInput, { target: { value: "90" } });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({ duration_sec: 90 });
  });

  it("点生成→弹确认窗（请求 estimate），取消则不提交", async () => {
    render(<EcomVideoForm />);
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "保温杯" } });
    selectProductImage();

    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);

    // Confirm dialog appears and the estimate was requested with the i2v body.
    expect(await screen.findByText("确定生成")).toBeInTheDocument();
    await waitFor(() =>
      expect(estimateMock.mutate).toHaveBeenCalledWith(
        expect.objectContaining({ video_mode: "seedance_i2v", duration_sec: 30 })
      )
    );
    expect(screen.getByText("12")).toBeInTheDocument(); // 预计消耗 12 积分
    expect(
      screen.getByText("确定生成即会消耗积分，生成过程中无法取消！")
    ).toBeInTheDocument();

    // 取消 → no submit (nothing charged).
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(taskMocks.createAndTrack).not.toHaveBeenCalled();
  });

  it("AI 生成画面 calls /videos/scene-prompt, fills the box, and submits scene_prompt", async () => {
    render(<EcomVideoForm />);
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "保温杯" } });

    // 画面提示词 AI generate — decoupled endpoint, fills its own textarea.
    fireEvent.click(screen.getByRole("button", { name: /AI 生成画面/ }));
    await waitFor(() => expect(scenePromptMock.mutateAsync).toHaveBeenCalledWith("保温杯"));
    expect(await screen.findByDisplayValue("明亮影棚，产品特写旋转")).toBeInTheDocument();

    // Submit carries scene_prompt alongside the i2v body.
    selectProductImage();
    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({
      video_mode: "seedance_i2v",
      scene_prompt: "明亮影棚，产品特写旋转"
    });
  });

  it("重写文案 sends video_mode + duration_sec; one duration drives 文案 + submit (10秒档)", async () => {
    render(<EcomVideoForm />);
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "保温杯" } });

    // Default duration (30) flows into 重写文案.
    fireEvent.click(screen.getByRole("button", { name: /重写文案/ }));
    await waitFor(() =>
      expect(scriptMock.mutateAsync).toHaveBeenCalledWith({
        topic: "保温杯",
        video_mode: "seedance_i2v",
        duration_sec: 30
      })
    );

    // Pick the 10s gear → the SAME duration_sec drives both 重写文案 and the submit body.
    fireEvent.click(screen.getByRole("button", { name: "10 秒" }));
    fireEvent.click(screen.getByRole("button", { name: /重写文案/ }));
    await waitFor(() =>
      expect(scriptMock.mutateAsync).toHaveBeenLastCalledWith({
        topic: "保温杯",
        video_mode: "seedance_i2v",
        duration_sec: 10
      })
    );

    selectProductImage();
    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({ duration_sec: 10 });
  });
});
