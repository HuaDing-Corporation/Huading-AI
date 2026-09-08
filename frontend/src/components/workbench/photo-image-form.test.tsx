import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { render } from "@/lib/billing/test-utils";
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

// 🔴 PHOTO-FORM-FLAKE-FIX：必须 `await`。
// 上传是异步的，而且落到断言要走一整条**跨组件级联**：
//   picker 内 setItems（DOM 上「（n/…）」立刻变） → picker 的 useEffect([items]) → onChange = 父组件 setRefKeys
//   → 父组件重渲染 → 「生成图片」的 onClick 闭包里才有新的 refKeys。
// 而 onGenerate 在**点击那一刻**就把 payload（含/不含 image_keys）冻结进 requestConfirm——之后再怎么
// await 都救不回来。原来只等 `waitRefUploaded`（= 观测 picker 的 items）**只覆盖了这条级联的第一环**，
// 满负载/高争抢下后面几环会拖过它的放行点 → 点击时 refKeys 仍是 [] → 提交体整个不带 image_keys（undefined）。
// **不是加延时、不是调大 timeout、不是 retry。**
//
// 🔴 措辞更正（CB#3 在 #230 提、REF-VIDEOS-PICKER-FLAKE-FIX 一并收）：原文写的是「promise 全部落定……
// 结构上不可能只跑一半」，**说过头了**。act 排空的是**微任务链**，对任意延迟 promise 并不成立。
// 它在这里成立，是因为上传 mock 用的是**立刻落定的 `Promise.resolve`**（见上方 mockImplementation），
// 且 picker 虽用 `void onFiles(...)` 丢掉了 promise，其后续仍都在微任务上，act 等得到。
//   ❌ 不覆盖：真实计时器 / 宏任务（setTimeout、rAF）、要等真网络的 promise、以及在 act 返回**之后**
//      才落定的工作。若哪天把上传 mock 改成延迟落定，这道门会失效，要换显式同步点而不是再包一层 act。
async function uploadReferenceImages(n = 1) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const files = Array.from({ length: n }, (_, i) => new File(["x"], `r${i}.png`, { type: "image/png" }));
  await act(async () => {
    fireEvent.change(input, { target: { files } });
  });
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
      image_resolution: "1k", // §3之二：清晰度档位默认 1k，总随请求传
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

  // §3之二 承重：清晰度档位默认 1k 总随请求传；选 4K → image_resolution:"4k"（界面选择是硬条件）。
  it("清晰度档位: 默认 1k 总带；选 4K → 提交体 image_resolution:4k", async () => {
    render(<PhotoImageForm />);
    setPrompt("香水瓶特写");
    // 默认 1K 选中。
    expect(screen.getByRole("button", { name: "1K" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "4K" }));
    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].image_resolution).toBe("4k");
  });

  it("图片修改固定 1K：上传参考图后锁定档位并显式提交 image_resolution:1k", async () => {
    render(<PhotoImageForm />);
    setPrompt("把背景换成沙滩");
    fireEvent.click(screen.getByRole("button", { name: "4K" }));
    expect(screen.getByRole("button", { name: "4K" })).toHaveAttribute("aria-pressed", "true");

    await uploadReferenceImages(1);
    await waitFor(() => expect(screen.getByRole("button", { name: "4K" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "1K" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({
      image_keys: ["uploads/ref-1.png"],
      image_resolution: "1k"
    });
  });

  it("开启 AI 标识开关 → 提交体 apply_visible_label:true（承重）", async () => {
    render(<PhotoImageForm />);
    setPrompt("一只橘猫");
    fireEvent.click(screen.getByRole("switch", { name: "AI 生成标识" })); // 精确全名，与三个强度开关不撞
    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].apply_visible_label).toBe(true);
  });

  // req1 承重：参考图单张→多图 image_keys。修图: 1 张 → image_keys:[key]（不再是标量 image_key）。
  it("修图: 上传 1 张参考图 → 提交体 image_keys:[key]，不带标量 image_key", async () => {
    render(<PhotoImageForm />);
    setPrompt("把背景换成沙滩");
    await uploadReferenceImages(1);
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
    await uploadReferenceImages(3);
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
    await uploadReferenceImages(3);
    await waitRefUploaded(3); // 等 3 张全落地再切档
    const generate = screen.getByRole("button", { name: /生成图片/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "1 张" }));
    await waitFor(() => expect(generate).toBeDisabled());
    expect(screen.getByText(/已上传 3 张，超过所选 1 张/)).toBeInTheDocument();
  });

  // 承重（Code Review medium · 文案诚实）：photo 张数上限=6，自定义输入 placeholder 须随 per-call max 显「1–6」，
  // 不得回归静态「1–9」（否则占位提示比校验范围更宽，误导用户 7/8/9 合法）。
  it("参考图张数自定义输入 placeholder 随 max=6 显「1–6」（非静态 1–9）", async () => {
    render(<PhotoImageForm />);
    fireEvent.click(screen.getByRole("button", { name: "自定义" }));
    const customInput = screen.getByLabelText("自定义张数");
    expect(customInput).toHaveAttribute("placeholder", "1–6");
  });

  // 🔴 req2 承重（关键防假绿）：三个强度默认关闭 → 提交体**不含任何 *_strength**。
  it("强度默认关闭 → 提交体不含任何 *_strength", async () => {
    render(<PhotoImageForm />);
    setPrompt("一只橘猫");
    fireEvent.click(screen.getByRole("button", { name: /生成图片/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    const [request] = taskMocks.createAndTrack.mock.calls[0];
    for (const k of ["similarity_strength", "creativity_strength", "subject_strength"]) {
      expect(request).not.toHaveProperty(k);
    }
  });

  // 承重（2026-07-19 需求变更）：背景参考强度已砍除 → 生成强度组只剩三个强度开关，且「背景参考强度」不再渲染。
  // 变异：把 background_strength 加回 STRENGTH_KEYS → 本条红（开关数 4 / 出现「背景参考强度」）。
  it("背景参考强度已移除：只剩三个强度开关（图片相似度/AI创意程度/主体保持强度），无背景参考强度", () => {
    render(<PhotoImageForm />);
    // 三个保留强度开关在册。
    expect(screen.getByRole("switch", { name: "图片相似度开关" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "AI 创意程度开关" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "主体保持强度开关" })).toBeInTheDocument();
    // 强度开关恰好三个——结构隔离到「生成强度」分组内计数（AI 标识开关在组外，即便改名带「开关」后缀也不会误计；
    // 不再用 /开关$/ 过滤全页 switch，避免评审指出的假红：无关文案改名把标识开关计进强度数）。
    const strengthGroup = screen.getByText("生成强度（可选）").closest("details") as HTMLElement;
    expect(within(strengthGroup).getAllByRole("switch")).toHaveLength(3);
    // 背景参考强度彻底消失（label 与开关均无）。
    expect(screen.queryByText("背景参考强度")).not.toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "背景参考强度开关" })).not.toBeInTheDocument();
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
