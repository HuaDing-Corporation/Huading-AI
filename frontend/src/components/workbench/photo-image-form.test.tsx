import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const taskMocks = vi.hoisted(() => ({ createAndTrack: vi.fn() }));
const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const estimateMock = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("@/lib/api/hooks", () => ({
  useUploadProductImage: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: false }),
  // IMAGE-GEN-OPTIMIZE-UI-0001：参考图改多图后复用 ReferenceImagesPicker，其默认路径调 useUploadImage（本表单走
  // 自定义 uploadFile=useUploadProductImage，故此 mock 不实际被调用，仅满足 hook 调用契约不为 undefined）。
  useUploadImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useEstimateVideo: () => ({
    mutate: estimateMock.mutate,
    reset: vi.fn(),
    isPending: false,
    data: { estimated_credits: 10, unit: "credits" }
  })
}));
vi.mock("@/lib/videos/tasks-context", () => ({ useVideoTasks: () => taskMocks }));

import { PhotoImageForm } from "./photo-image-form";

let uploadSeq = 0;
beforeEach(() => {
  window.localStorage.clear(); // 每用例干净起点：AI 标识开关默认关
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadSeq = 0;
  uploadMock.mutateAsync.mockImplementation(() => Promise.resolve({ image_key: `uploads/ref-${++uploadSeq}.png` }));
});
afterEach(() => vi.clearAllMocks());

function uploadReferenceImages(n = 1) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const files = Array.from({ length: n }, (_, i) => new File(["x"], `r${i}.png`, { type: "image/png" }));
  fireEvent.change(input, { target: { files } });
}
const setPrompt = (v: string) => fireEvent.change(screen.getByPlaceholderText(/描述想要的图片/), { target: { value: v } });
// 等参考图上传落地：picker 上传按钮显示「（n/…）」= items/refKeys 已更新。photo 的 generate 不依赖参考图（可选），
// 不能靠 generate-enabled 等上传完成（否则并行时 submit 抢在 refKeys 落地前 → flake）。
const waitRefUploaded = (n: number) =>
  waitFor(() => expect(screen.getByRole("button", { name: new RegExp(`上传参考图（${n}/`) })).toBeInTheDocument());

describe("PhotoImageForm (图片生成 / 修改 · IMAGE-GEN-OPTIMIZE-UI-0001)", () => {
  it("提示词必填：空则禁用 + 提示；填后启用", () => {
    render(<PhotoImageForm />);
    const generate = screen.getByRole("button", { name: /生成图片/ });
    expect(generate).toBeDisabled();
    expect(screen.getByText("请先输入提示词")).toBeInTheDocument();
    expect(screen.getByText("图片生成 / 修改")).toBeInTheDocument();
    setPrompt("一只橘猫");
    expect(generate).toBeEnabled();
  });

  it("纯文生图: 提交体带 aspect_ratio 默认 1:1；不带 image_key/image_keys、不带任何强度、不带质量/尺寸", async () => {
    render(<PhotoImageForm />);
    expect(screen.queryByText("质量")).not.toBeInTheDocument();
    expect(screen.getByText("画面比例")).toBeInTheDocument();
    setPrompt("一只橘猫");
    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    const [request] = taskMocks.createAndTrack.mock.calls[0];
    expect(request).toEqual({
      topic: "一只橘猫",
      video_mode: "photo",
      aspect_ratio: "1:1",
      apply_visible_label: false
    });
    expect(request).not.toHaveProperty("image_key");
    expect(request).not.toHaveProperty("image_keys"); // 无参考图 → 不带
    expect(request).not.toHaveProperty("similarity_strength");
    expect(request).not.toHaveProperty("image_quality");
    expect(request).not.toHaveProperty("image_size");
  });

  it("选画面比例 16:9 → 提交体 aspect_ratio:16:9（零回归·选择接线）", async () => {
    render(<PhotoImageForm />);
    setPrompt("赛博城市");
    fireEvent.click(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: /16:9/ }));
    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].aspect_ratio).toBe("16:9");
  });

  it("开启 AI 标识开关 → 提交体 apply_visible_label:true（承重）", async () => {
    render(<PhotoImageForm />);
    setPrompt("一只橘猫");
    fireEvent.click(screen.getByRole("switch", { name: "AI 生成标识" })); // 消歧：现有 4 个强度开关
    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].apply_visible_label).toBe(true);
  });

  // req1 承重：参考图单张→多图 image_keys。修图: 1 张 → image_keys:[key]（不再是标量 image_key）。
  it("修图: 上传 1 张参考图 → 提交体 image_keys:[key]，不带标量 image_key", async () => {
    render(<PhotoImageForm />);
    setPrompt("把背景换成沙滩");
    uploadReferenceImages(1);
    await waitRefUploaded(1); // 等 refKeys 落地（generate 不依赖参考图，不能靠 enabled 等）
    const generate = screen.getByRole("button", { name: /生成图片/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    const [request] = taskMocks.createAndTrack.mock.calls[0];
    expect(request.image_keys).toEqual(["uploads/ref-1.png"]);
    expect(request).not.toHaveProperty("image_key");
  });

  // req1 承重（多图）：张数 3 + 上传 3 张 → image_keys 含 3 个。
  it("多图: 张数 3 + 上传 3 张 → image_keys 含 3 个 key", async () => {
    render(<PhotoImageForm />);
    setPrompt("产品图合成");
    fireEvent.click(screen.getByRole("button", { name: "3 张" }));
    uploadReferenceImages(3);
    await waitRefUploaded(3); // 等 3 张全落地
    const generate = screen.getByRole("button", { name: /生成图片/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].image_keys).toEqual([
      "uploads/ref-1.png",
      "uploads/ref-2.png",
      "uploads/ref-3.png"
    ]);
  });

  // req1 承重（不静默丢图）：张数 3 上传 3 张，再切张数 1 → 明确越限 + 生成禁用。
  it("切张数低于已传数 → 明确越限拦截 + 生成禁用（不静默丢图）", async () => {
    render(<PhotoImageForm />);
    setPrompt("x");
    fireEvent.click(screen.getByRole("button", { name: "3 张" }));
    uploadReferenceImages(3);
    await waitRefUploaded(3); // 等 3 张全落地再切档
    const generate = screen.getByRole("button", { name: /生成图片/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "1 张" }));
    await waitFor(() => expect(generate).toBeDisabled());
    expect(screen.getByText(/已上传 3 张，超过所选 1 张/)).toBeInTheDocument();
  });

  // 🔴 req2 承重（关键防假绿）：四个强度默认关闭 → 提交体**不含任何 *_strength**。
  it("强度默认关闭 → 提交体不含任何 *_strength", async () => {
    render(<PhotoImageForm />);
    setPrompt("一只橘猫");
    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    const [request] = taskMocks.createAndTrack.mock.calls[0];
    for (const k of ["similarity_strength", "creativity_strength", "subject_strength", "background_strength"]) {
      expect(request).not.toHaveProperty(k);
    }
  });

  // req2 承重：开启「图片相似度」并设 80% → 提交体 similarity_strength:80；未开启的其余强度仍不出现。
  it("开启图片相似度并设 80% → 提交体 similarity_strength:80，其余强度不出现", async () => {
    render(<PhotoImageForm />);
    setPrompt("一只橘猫");
    // 开启相似度开关 → 其滑块启用 → 设 80。
    fireEvent.click(screen.getByRole("switch", { name: "图片相似度开关" }));
    const slider = screen.getByLabelText("图片相似度") as HTMLInputElement;
    expect(slider).toBeEnabled();
    fireEvent.change(slider, { target: { value: "80" } });

    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    const [request] = taskMocks.createAndTrack.mock.calls[0];
    expect(request.similarity_strength).toBe(80);
    expect(request).not.toHaveProperty("creativity_strength");
    expect(request).not.toHaveProperty("subject_strength");
    expect(request).not.toHaveProperty("background_strength");
  });

  // req3 承重：四层提示词——图片提示词(topic) + 图片负面(negative_prompt) + 任务总控(master_prompt) + 统一负面(master_negative_prompt) 随请求传。
  it("四层提示词随请求传：topic + negative_prompt + master_prompt + master_negative_prompt", async () => {
    render(<PhotoImageForm />);
    setPrompt("主体：香水瓶");
    fireEvent.change(screen.getByPlaceholderText(/这张图想尽量避免/), { target: { value: "水印" } });
    // 展开任务总控组填两层。
    fireEvent.click(screen.getByText("任务总控（可选 · 全局风格）"));
    fireEvent.change(screen.getByPlaceholderText(/全局风格前缀/), { target: { value: "统一暖色胶片" } });
    fireEvent.change(screen.getByPlaceholderText(/本次统一想避免/), { target: { value: "低分辨率" } });

    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({
      topic: "主体：香水瓶",
      negative_prompt: "水印",
      master_prompt: "统一暖色胶片",
      master_negative_prompt: "低分辨率"
    });
  });
});
