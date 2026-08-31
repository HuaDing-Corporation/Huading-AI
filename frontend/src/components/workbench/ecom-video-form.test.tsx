import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/mocks/server";

const taskMocks = vi.hoisted(() => ({ createAndTrack: vi.fn() }));
const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));

vi.mock("@/lib/api/hooks", () => ({
  useUploadProductImage: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: false }),
  // ECOM-VIDEO-OPTIMIZE-UI-0001：产品图改多图后复用 ReferenceImagesPicker，其默认路径调 useUploadImage（本表单
  // 走自定义 uploadFile=useUploadProductImage，故此 mock 实际不被调用，仅满足组件 hook 调用契约不为 undefined）。
  useUploadImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  // ConfirmGenerateDialog 同时保留非权威旧流程的 fallback hook；本表单走 authoritativePricing，不会调用它。
  useEstimateVideo: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, data: undefined }),
  useBrandVoices: () => ({ data: [], isLoading: false }),
  useVoices: () => ({
    data: [
      { id: "v1", provider: "doubao", voice_code: "c", display_name: "豆包女声", gender: null, language: null }
    ]
  })
}));
vi.mock("@/lib/videos/tasks-context", () => ({ useVideoTasks: () => taskMocks }));
// VIP 门禁（§二之二）：默认 admin（doubao 品牌音色可用，零回归）。
vi.mock("@/lib/auth/auth-context", () => ({ useAuth: () => ({ session: { role: "admin", user: { permissions: ["voice_clone_vip"] } }, ready: true }) }));

import { EcomVideoForm } from "./ecom-video-form";

const API = "http://localhost:8000";
type RecordedSubmit = {
  body: unknown;
  quote: string | null;
  key: string | null;
};

const scriptEstimates: unknown[] = [];
const scriptSubmits: RecordedSubmit[] = [];
const sceneEstimates: unknown[] = [];
const sceneSubmits: RecordedSubmit[] = [];
const videoEstimates: unknown[] = [];

function simpleQuote(operation: "script_generate" | "scene_prompt", credits: number, token: string) {
  return {
    pricing_contract: "billing_quote",
    pricing_shape: "simple",
    operation,
    unit: "request",
    quantity: "1",
    unit_credits: String(credits),
    subtotal_credits: String(credits),
    payable_credits: credits,
    rate_scope: "platform_fixed",
    rate_source: "fixed_policy",
    breakdown: [],
    disclosures: [],
    quote_token: token,
    expires_at: new Date(Date.now() + 60_000).toISOString()
  };
}

let uploadSeq = 0;
beforeEach(() => {
  window.localStorage.clear(); // 每用例干净起点：AI 标识开关默认关
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadSeq = 0;
  scriptEstimates.length = 0;
  scriptSubmits.length = 0;
  sceneEstimates.length = 0;
  sceneSubmits.length = 0;
  videoEstimates.length = 0;
  // 多图承重：每次上传返回**不同** image_key（默认单图用例只上传 1 张，取 key-1）。
  uploadMock.mutateAsync.mockImplementation(() => Promise.resolve({ image_key: `uploads/key-${++uploadSeq}.png` }));
  server.use(
    http.post(`${API}/api/v1/scripts/estimate`, async ({ request }) => {
      scriptEstimates.push(await request.json());
      return HttpResponse.json({
        data: simpleQuote("script_generate", 1, "script-quote-token"),
        error: null,
        request_id: "script-estimate"
      });
    }),
    http.post(`${API}/api/v1/scripts/generate`, async ({ request }) => {
      const key = request.headers.get("Idempotency-Key");
      scriptSubmits.push({
        body: await request.json(),
        quote: request.headers.get("X-Huading-Quote"),
        key
      });
      return HttpResponse.json({
        data: {
          script: "服务端生成的电商文案",
          billing: {
            operation_id: `script-operation-${scriptSubmits.length}`,
            idempotency_key: key,
            status: "settled",
            requested_credits: 1,
            held_credits: 0,
            settled_credits: 1,
            released_credits: 0
          }
        },
        error: null,
        request_id: "script-submit"
      });
    }),
    http.post(`${API}/api/v1/videos/scene-prompt/estimate`, async ({ request }) => {
      sceneEstimates.push(await request.json());
      return HttpResponse.json({
        data: simpleQuote("scene_prompt", 30, "scene-quote-token"),
        error: null,
        request_id: "scene-estimate"
      });
    }),
    http.post(`${API}/api/v1/videos/scene-prompt`, async ({ request }) => {
      const key = request.headers.get("Idempotency-Key");
      sceneSubmits.push({
        body: await request.json(),
        quote: request.headers.get("X-Huading-Quote"),
        key
      });
      return HttpResponse.json({
        data: {
          scene_prompt: "明亮影棚，产品特写旋转",
          negative_prompt: "水印, 杂乱背景, 变形",
          billing: {
            operation_id: `scene-operation-${sceneSubmits.length}`,
            idempotency_key: key,
            status: "settled",
            requested_credits: 30,
            held_credits: 0,
            settled_credits: 30,
            released_credits: 0
          }
        },
        error: null,
        request_id: "scene-submit"
      });
    }),
    http.post(`${API}/api/v1/videos/estimate`, async ({ request }) => {
      videoEstimates.push(await request.json());
      return HttpResponse.json({
        data: {
          pricing_contract: "legacy_estimate",
          estimated_credits: 12,
          unit: "credits"
        },
        error: null,
        request_id: "video-estimate"
      });
    })
  );
});
afterEach(() => vi.clearAllMocks());

// 产品图多图 picker：上传 n 张（一次 change 带 n 个文件）。等待产品图 key 落地由各用例的 waitFor 承担。
function uploadProductImages(n = 1) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const files = Array.from({ length: n }, (_, i) => new File(["x"], `p${i}.png`, { type: "image/png" }));
  fireEvent.change(input, { target: { files } });
}

async function confirmVideoGeneration() {
  const confirm = await screen.findByRole("button", { name: "确定" });
  await waitFor(() => expect(confirm).toBeEnabled());
  fireEvent.click(confirm);
}

describe("EcomVideoForm (电商带货 i2v · ECOM-VIDEO-OPTIMIZE-UI-0001)", () => {
  it("renders 视频时长 above the 主题/卖点 input (duration-first layout)", () => {
    render(<EcomVideoForm />);
    const durationLegend = screen.getByText("视频时长（与文案、字幕一致）");
    const topicInput = screen.getByPlaceholderText(/输入产品卖点/);
    expect(
      durationLegend.compareDocumentPosition(topicInput) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });

  // req1/决策2 承重：主题去必填，新下限=产品图 ≥1（+音色）。仅产品图即可生成，且提交体**不带 topic**（空则不带）。
  it("主题可空：仅上传产品图即可生成，提交体不带 topic（req1/决策2 承重）", async () => {
    render(<EcomVideoForm />);
    const generate = screen.getByRole("button", { name: /生成视频/ });

    // 纯净态：无产品图 → 禁用 + 「请上传产品图」提示（不再有主题必填提示）。
    expect(generate).toBeDisabled();
    expect(screen.getByText("请上传产品图后再生成")).toBeInTheDocument();
    expect(screen.queryByText("请先输入产品卖点")).not.toBeInTheDocument();

    // 只上传产品图（主题留空）→ 可生成。
    uploadProductImages(1);
    await waitFor(() => expect(generate).toBeEnabled());
    expect(screen.queryByText("请上传产品图后再生成")).not.toBeInTheDocument();

    fireEvent.click(generate);
    await confirmVideoGeneration();
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    const [request] = taskMocks.createAndTrack.mock.calls[0];
    expect(request.topic).toBeUndefined(); // 空则不带：topic:undefined 被 createVideo 的 JSON.stringify 丢弃 → 请求体不含 topic
    expect(request.product_image_keys).toEqual(["uploads/key-1.png"]);
    expect(request.video_mode).toBe("seedance_i2v");
  });

  it("上传产品图（→product_image_keys）后提交 seedance_i2v 请求体（req2：单图 image_key → 多图 keys）", async () => {
    render(<EcomVideoForm />);
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "316 不锈钢保温杯" } });
    uploadProductImages(1);

    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(1));

    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    await confirmVideoGeneration();

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    const [request, topic] = taskMocks.createAndTrack.mock.calls[0];
    expect(request).toEqual({
      topic: "316 不锈钢保温杯",
      script: undefined,
      video_mode: "seedance_i2v",
      product_image_keys: ["uploads/key-1.png"],
      voice_id: "v1",
      scene_prompt: undefined,
      negative_prompt: undefined,
      duration_sec: 30,
      resolution: "720p",
      speed: 1,
      aspect_ratio: "9:16",
      subtitle_enabled: true,
      apply_visible_label: false
    });
    expect(topic).toBe("316 不锈钢保温杯");
    // 单图旧字段已去；i2v 不带数字人字段。
    expect(request).not.toHaveProperty("image_key");
    expect(request).not.toHaveProperty("avatar_asset_id");
  });

  // req2 承重：多图 —— 张数选择器选 2，上传 2 张 → 提交体 product_image_keys 带 2 个 key（不静默丢/不截断）。
  it("多图：选张数 2 + 上传 2 张 → 提交体 product_image_keys 含 2 个 key（req2 承重）", async () => {
    render(<EcomVideoForm />);
    fireEvent.click(screen.getByRole("button", { name: "2 张" })); // 张数=2
    uploadProductImages(2);

    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(2));
    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    await confirmVideoGeneration();

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].product_image_keys).toEqual([
      "uploads/key-1.png",
      "uploads/key-2.png"
    ]);
  });

  // req2 承重（不静默丢图）：先传 2 张，再把张数切到 1 → 越限明确拦截（禁用 + 提示），绝不无声丢图。
  it("切张数低于已传数 → 明确越限拦截 + 不放行（不静默丢图，req2）", async () => {
    render(<EcomVideoForm />);
    fireEvent.click(screen.getByRole("button", { name: "2 张" }));
    uploadProductImages(2);
    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());

    // 切到 1 张（已传 2 > 1）→ 越限：禁用 + 明确提示，图未被删。
    fireEvent.click(screen.getByRole("button", { name: "1 张" }));
    await waitFor(() => expect(generate).toBeDisabled());
    expect(screen.getByText(/已上传 2 张，超过所选 1 张/)).toBeInTheDocument();
  });

  it("分辨率三档出现且默认 720P；切 1080P → 提交体 resolution:1080p（承重）", async () => {
    render(<EcomVideoForm />);
    uploadProductImages(1);
    expect(screen.getByRole("button", { name: "480P" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "720P" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "1080P" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "1080P" }));
    expect(screen.getByRole("button", { name: "1080P" })).toHaveAttribute("aria-pressed", "true");
    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    await confirmVideoGeneration();
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].resolution).toBe("1080p");
  });

  it("开启 AI 标识开关 → 提交体 apply_visible_label:true（承重）", async () => {
    render(<EcomVideoForm />);
    uploadProductImages(1);
    fireEvent.click(screen.getByRole("switch"));
    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    await confirmVideoGeneration();
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].apply_visible_label).toBe(true);
  });

  it("submits the selected duration gear in duration_sec（保留档 30）", async () => {
    render(<EcomVideoForm />);
    uploadProductImages(1);
    fireEvent.click(screen.getByRole("button", { name: "10 秒" }));
    fireEvent.click(screen.getByRole("button", { name: "30 秒" }));

    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    await confirmVideoGeneration();

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({
      video_mode: "seedance_i2v",
      duration_sec: 30
    });
  });

  it("submits a custom duration and blocks out-of-range values", async () => {
    render(<EcomVideoForm />);
    uploadProductImages(1);
    // 「自定义」在时长与产品图张数两个选择器都有 → 按 fieldset(legend) 限定到视频时长那份再点。
    const durationFieldset = screen.getByText("视频时长（与文案、字幕一致）").closest("fieldset") as HTMLElement;
    fireEvent.click(within(durationFieldset).getByRole("button", { name: "自定义" }));

    const generate = screen.getByRole("button", { name: /生成视频/ });
    const durationInput = screen.getByLabelText("自定义时长（秒）");

    fireEvent.change(durationInput, { target: { value: "200" } });
    await waitFor(() => expect(generate).toBeDisabled());
    expect(screen.getByText("请输入 5–120 的整数秒")).toBeInTheDocument();

    fireEvent.change(durationInput, { target: { value: "90" } });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    await confirmVideoGeneration();

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({ duration_sec: 90 });
  });

  it("点生成→弹确认窗（请求 estimate），取消则不提交", async () => {
    render(<EcomVideoForm />);
    uploadProductImages(1);

    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);

    expect(await screen.findByText("确定生成")).toBeInTheDocument();
    await waitFor(() => expect(videoEstimates).toHaveLength(1));
    expect(videoEstimates[0]).toMatchObject({ video_mode: "seedance_i2v", duration_sec: 30 });
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("确定生成即会消耗积分，生成过程中无法取消！")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    // 🔴 NEGATIVE-ASSERT-SWEEP-UI-0001：推进到静止点再断言（资金路径）。裸同步断言只看得见点击当下那一帧 ——
    //    「取消时仍在微任务后把生成提交出去」这种实现会溜过去（实测：变异后本条照样绿）。
    await act(async () => {});
    expect(taskMocks.createAndTrack).not.toHaveBeenCalled();
  });

  it("BILLABLE_TEXT_REQUIRED closes pricing, focuses the e-commerce script, and never submits", async () => {
    server.use(
      http.post(`${API}/api/v1/videos/estimate`, () =>
        HttpResponse.json(
          {
            data: null,
            error: { code: "BILLABLE_TEXT_REQUIRED", message: "品牌音色视频需要文案。" },
            request_id: "ecom-missing-billable-text"
          },
          { status: 422 }
        )
      )
    );
    render(<EcomVideoForm />);
    uploadProductImages(1);
    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());

    fireEvent.click(generate);

    const scriptEditor = document.getElementById("ecom-script");
    expect(scriptEditor).not.toBeNull();
    await waitFor(() => expect(scriptEditor).toHaveFocus());
    expect(screen.getByRole("alert")).toHaveTextContent("品牌音色视频需要文案");
    expect(screen.queryByRole("dialog", { name: "确定生成" })).not.toBeInTheDocument();
    expect(taskMocks.createAndTrack).not.toHaveBeenCalled();
  });

  // req7/§4.2 承重：AI 生成画面需产品图（无图禁点）；发 {topic, product_image_keys}；返回 scene_prompt + negative_prompt 各自填入。
  it("AI 生成画面：无产品图禁点；上传后发产品图 keys，scene_prompt + negative_prompt 各自填入并随提交", async () => {
    render(<EcomVideoForm />);
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "保温杯" } });

    // 无产品图 → 「AI 生成画面」禁用 + 提示。
    const sceneBtn = screen.getByRole("button", { name: /AI 生成画面/ });
    expect(sceneBtn).toBeDisabled();
    expect(screen.getByText("请先上传产品图，再生成画面")).toBeInTheDocument();

    // 上传产品图 → 可点。
    uploadProductImages(1);
    await waitFor(() => expect(sceneBtn).toBeEnabled());
    fireEvent.click(sceneBtn);

    // 报价与提交都绑定产品图 keys + topic（非只 topic 字符串）+ 当前时长。
    const dialog = await screen.findByRole("dialog", { name: "确认价格并继续" });
    await waitFor(() => expect(sceneEstimates).toHaveLength(1));
    expect(sceneEstimates[0]).toEqual({
      topic: "保温杯",
      product_image_keys: ["uploads/key-1.png"],
      duration_sec: 30
    });
    expect(sceneSubmits).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "确认并继续" }));
    // 画面 + 负面各自填入。
    expect(await screen.findByDisplayValue("明亮影棚，产品特写旋转")).toBeInTheDocument();
    expect(screen.getByDisplayValue("水印, 杂乱背景, 变形")).toBeInTheDocument();
    expect(sceneSubmits[0]).toMatchObject({
      body: sceneEstimates[0],
      quote: "scene-quote-token",
      key: expect.any(String)
    });

    // 提交体带 scene_prompt + negative_prompt。
    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    await confirmVideoGeneration();
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({
      video_mode: "seedance_i2v",
      scene_prompt: "明亮影棚，产品特写旋转",
      negative_prompt: "水印, 杂乱背景, 变形"
    });
  });

  // SCENE-DURATION-FIX 承重：「AI生成画面」请求体带当前选中时长——预设「10 秒」→ 10；「自定义 5」→ 5（截图里自定义 5 秒没生效即本 bug）。
  it("AI 生成画面带当前时长：选「10 秒」→ duration_sec:10；「自定义 5」→ duration_sec:5", async () => {
    render(<EcomVideoForm />);
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "保温杯" } });
    uploadProductImages(1);
    const sceneBtn = screen.getByRole("button", { name: /AI 生成画面/ });
    await waitFor(() => expect(sceneBtn).toBeEnabled());

    // 预设 10 秒 → duration_sec:10。
    fireEvent.click(screen.getByRole("button", { name: "10 秒" }));
    fireEvent.click(sceneBtn);
    let dialog = await screen.findByRole("dialog", { name: "确认价格并继续" });
    await waitFor(() => expect(sceneEstimates).toHaveLength(1));
    expect(sceneEstimates[0]).toMatchObject({ duration_sec: 10 });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认并继续" }));
    await waitFor(() => expect(sceneSubmits).toHaveLength(1));

    // 自定义 5 秒（下限）→ duration_sec:5（必须含自定义输入的值）。
    const durationFieldset = screen.getByText("视频时长（与文案、字幕一致）").closest("fieldset") as HTMLElement;
    fireEvent.click(within(durationFieldset).getByRole("button", { name: "自定义" }));
    fireEvent.change(screen.getByLabelText("自定义时长（秒）"), { target: { value: "5" } });
    fireEvent.click(sceneBtn);
    dialog = await screen.findByRole("dialog", { name: "确认价格并继续" });
    await waitFor(() => expect(sceneEstimates).toHaveLength(2));
    expect(sceneEstimates[1]).toMatchObject({ duration_sec: 5 });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认并继续" }));
    await waitFor(() => expect(sceneSubmits).toHaveLength(2));
  });

  // FIX2（CB P1）承重：小数时长 5.5 在电商**每条发送路径**分别不发请求（真 BE ScriptGenerateRequest /
  // ScenePromptRequest / VideoGenerateRequest.duration_sec 皆 int，小数 → 422，前端从源头拦、不假绿）。每路径一条。
  async function setup55() {
    render(<EcomVideoForm />);
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "保温杯" } });
    uploadProductImages(1);
    await waitFor(() => expect(screen.getByRole("button", { name: /AI 生成画面/ })).toBeEnabled());
    const durationFieldset = screen.getByText("视频时长（与文案、字幕一致）").closest("fieldset") as HTMLElement;
    fireEvent.click(within(durationFieldset).getByRole("button", { name: "自定义" }));
    fireEvent.change(screen.getByLabelText("自定义时长（秒）"), { target: { value: "5.5" } });
    // FIX3（P2-2）：文案断言**移出**共享 setup（否则变异下三条测试都在这里提前失败、掩盖各自路径断言）。
    // 5.5 错误文案的显示由 duration-picker.test.tsx 与本文件「submits a custom duration」用例覆盖。
  }

  it("小数时长 5.5 · 路径1「AI生成文案」→ 禁点 + 不发（scripts；FIX2 此前漏门真发 {duration_sec:5.5}）", async () => {
    await setup55();
    const btn = screen.getByRole("button", { name: /AI生成文案/ });
    await waitFor(() => expect(btn).toBeDisabled());
    fireEvent.click(btn);
    expect(scriptEstimates).toHaveLength(0);
    expect(scriptSubmits).toHaveLength(0);
  });

  it("小数时长 5.5 · 路径2「AI生成画面」→ 禁点 + 不发（scene-prompt）", async () => {
    await setup55();
    const btn = screen.getByRole("button", { name: /AI 生成画面/ });
    await waitFor(() => expect(btn).toBeDisabled());
    fireEvent.click(btn);
    expect(sceneEstimates).toHaveLength(0);
    expect(sceneSubmits).toHaveLength(0);
  });

  it("小数时长 5.5 · 路径3「生成视频」→ 禁用 + 不提交（videos + estimate）", async () => {
    await setup55();
    await waitFor(() => expect(screen.getByRole("button", { name: /生成视频/ })).toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: /生成视频/ }));
    expect(taskMocks.createAndTrack).not.toHaveBeenCalled();
    expect(videoEstimates).toHaveLength(0);
  });

  // req3 承重：「重写文案」→「AI生成文案」重命名；字数档位随 scripts/generate 传 length_tier（默认 medium，切「长」→ long）。
  it("AI生成文案 + 字数档位：默认 medium；切「长」→ scripts/generate 带 length_tier:long（req3 承重）", async () => {
    render(<EcomVideoForm />);
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "保温杯" } });

    // 旧「重写文案」按钮已不在电商表单（口播共享组件仍是「重写文案」，此处应为「AI生成文案」）。
    expect(screen.queryByRole("button", { name: "重写文案" })).not.toBeInTheDocument();
    const genScript = screen.getByRole("button", { name: /AI生成文案/ });

    // 默认档位 medium。
    fireEvent.click(genScript);
    let dialog = await screen.findByRole("dialog", { name: "确认价格并继续" });
    await waitFor(() => expect(scriptEstimates).toHaveLength(1));
    expect(scriptEstimates[0]).toEqual({
      topic: "保温杯",
      video_mode: "seedance_i2v",
      duration_sec: 30,
      length_tier: "medium"
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认并继续" }));
    await waitFor(() => expect(scriptSubmits).toHaveLength(1));
    expect(scriptSubmits[0]).toMatchObject({
      body: scriptEstimates[0],
      quote: "script-quote-token",
      key: expect.any(String)
    });

    // 切「长」→ length_tier:long。
    fireEvent.click(screen.getByRole("button", { name: "长" }));
    fireEvent.click(genScript);
    dialog = await screen.findByRole("dialog", { name: "确认价格并继续" });
    await waitFor(() => expect(scriptEstimates).toHaveLength(2));
    expect(scriptEstimates[1]).toEqual({
      topic: "保温杯",
      video_mode: "seedance_i2v",
      duration_sec: 30,
      length_tier: "long"
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认并继续" }));
    await waitFor(() => expect(scriptSubmits).toHaveLength(2));
    expect(scriptSubmits[1]).toMatchObject({
      body: scriptEstimates[1],
      quote: "script-quote-token",
      key: expect.any(String)
    });
  });

  // req6 承重：手填负面提示词（不经 AI）→ 随提交传 negative_prompt。
  it("手动填负面提示词 → 提交体带 negative_prompt（req6 承重）", async () => {
    render(<EcomVideoForm />);
    uploadProductImages(1);
    const negBox = screen.getByPlaceholderText(/不希望出现的元素/);
    fireEvent.change(negBox, { target: { value: "禁止文字水印" } });

    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    await confirmVideoGeneration();
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].negative_prompt).toBe("禁止文字水印");
  });

  // 评审修复承重（CONFIRMED 死点击）：主题去必填后「AI生成文案」空主题时禁用（文案生成仍需主题；与「AI生成画面」缺图禁用对称，消静默 no-op）。
  it("空主题 → 「AI生成文案」禁用，填主题后启用（消死点击）", () => {
    render(<EcomVideoForm />);
    const genScript = screen.getByRole("button", { name: /AI生成文案/ });
    expect(genScript).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText(/输入产品卖点/), { target: { value: "保温杯" } });
    expect(genScript).toBeEnabled();
  });

  // 评审修复承重（CONFIRMED 张数 clear→0）：清空自定义张数 Number("")===0，不得误报「超过所选 0 张」；生成禁用 + picker 就地范围提示。
  it("清空自定义张数 → 不误报「超过所选 0 张」，生成禁用 + 张数范围提示", async () => {
    render(<EcomVideoForm />);
    uploadProductImages(1);
    const generate = screen.getByRole("button", { name: /生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());

    // 张数切自定义并清空 → 0（越限判据已守 isValidImageCount，不拿 0 报越限）。
    const countFieldset = screen.getByText("产品图张数").closest("fieldset") as HTMLElement;
    fireEvent.click(within(countFieldset).getByRole("button", { name: "自定义" }));
    fireEvent.change(screen.getByLabelText("自定义张数"), { target: { value: "" } });

    expect(screen.queryByText(/超过所选 0 张/)).not.toBeInTheDocument();
    await waitFor(() => expect(generate).toBeDisabled());
    expect(screen.getByText("请输入 1–9 张")).toBeInTheDocument();
  });
});
