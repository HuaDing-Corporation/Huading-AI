import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type {
  EcomReplicateConfirmAccepted,
  EcomReplicateJob,
  EcomReplicatePlanOutput
} from "@/lib/api/ecom-replicate";

// ECOM-REPLICATE-UI-0001 · FIX1 · 向导编排 TDD（对齐真实 BE 契约）。隔离网络：mock adapter（真契约由
// ecom-replicate.test.ts MSW 覆盖），此处只锁 4-Step 状态机 + 资金安全（扣费恰一次/取消不扣/重试不二次扣）+
// 原图红线（下载原图 bytes、显示 actual_w/h、零 canvas）+ 禁分批。isEcomReplicateSettled/ecomReplicateActualDimensions/
// 常量 保留真实（组件依赖）。
const api = vi.hoisted(() => ({
  planEcomReplicate: vi.fn(),
  confirmEcomReplicate: vi.fn(),
  getEcomReplicateJob: vi.fn(),
  retryEcomReplicateOutput: vi.fn()
}));

vi.mock("@/lib/api/ecom-replicate", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/ecom-replicate")>();
  return { ...actual, ...api };
});

// 参考图/商品图上传占位：按 inputId 区分（隔离上传，reference-images-picker 有专测）。渲染 label（供上限文案断言）；
// set=注入 1 张、fill12=注入 12 张（供 ECOM-REF-LIMIT 超模式上限承重）。
vi.mock("@/components/workbench/reference-images-picker", () => ({
  ReferenceImagesPicker: ({
    onChange,
    inputId,
    label
  }: {
    onChange?: (ids: string[]) => void;
    inputId?: string;
    label?: string;
  }) => (
    <div>
      <span>{label}</span>
      <button type="button" onClick={() => onChange?.([`${inputId}-a1`])}>{`set-${inputId}`}</button>
      <button type="button" onClick={() => onChange?.(Array.from({ length: 12 }, (_, i) => `${inputId}-x${i}`))}>
        {`fill12-${inputId}`}
      </button>
    </div>
  )
}));

import { EcomDetailWizard } from "./ecom-detail-wizard";

// ── 契约夹具（镜像 BE EcomReplicateAccepted / PlanOutput / ConfirmAccepted） ──
function output(index: number, over?: Partial<EcomReplicatePlanOutput>): EcomReplicatePlanOutput {
  return {
    id: `o${index}`,
    index,
    theme: index === 4 ? "white_background" : "layout_match",
    reference_asset_id: "ref-1",
    product_asset_id: "prod-1",
    requested_size: "1024x1024",
    requested_aspect: "1:1",
    status: "planned",
    prompt: `复刻要点 ${index}`,
    asset_id: null,
    download_url: null,
    actual_width: null,
    actual_height: null,
    ...over
  };
}

function mainPlanJob(over?: Partial<EcomReplicateJob>): EcomReplicateJob {
  return {
    job_id: "job-1",
    status: "plan_ready",
    // GEN-HEARTBEAT-UI-0001 · FIX3：BE 详情图响应**键恒在**（schema `str | None` 默认 None）→
    // 夹具必须带上它，否则就是在构造一个真实 BE 发不出的形状。plan_ready 尚未开始生成 → null。
    heartbeat_at: null,
    output_mode: "main",
    output_count: 5,
    total_credits: 75,
    credit_rate: 15,
    requested_size: "1024x1024",
    requested_aspect: "1:1",
    plan: {
      outputs: Array.from({ length: 5 }, (_, i) => output(i)),
      reference_analysis_json: [],
      template_mapping_json: {},
      generation_plan_json: {}
    },
    ...over
  };
}

function detailPlanJob(): EcomReplicateJob {
  return {
    job_id: "job-2",
    status: "plan_ready",
    heartbeat_at: null, // 同上：键恒在
    output_mode: "detail",
    output_count: 12,
    total_credits: 180,
    credit_rate: 15,
    requested_size: "768x1024",
    requested_aspect: "3:4",
    plan: {
      outputs: Array.from({ length: 12 }, (_, i) => output(i, { requested_size: "768x1024", requested_aspect: "3:4", theme: "hero" })),
      reference_analysis_json: [],
      template_mapping_json: {},
      generation_plan_json: {}
    }
  };
}

function confirmMinimal(over?: Partial<EcomReplicateConfirmAccepted>): EcomReplicateConfirmAccepted {
  return { job_id: "job-1", status: "generating", output_count: 5, total_credits: 75, ...over };
}

function succeededOutput(index: number, over?: Partial<EcomReplicatePlanOutput>): EcomReplicatePlanOutput {
  return output(index, {
    status: "succeeded",
    asset_id: `asset-${index}`,
    download_url: `https://cdn/full-${index}.png`, // FIX2 唯一图片 URL（预览+下载共用）
    actual_width: 1254,
    actual_height: 1254,
    ...over
  });
}

function generatingJob(): EcomReplicateJob {
  return { ...mainPlanJob(), status: "generating" }; // outputs 仍 planned
}

function completedJob(over?: (o: EcomReplicatePlanOutput[]) => EcomReplicatePlanOutput[]): EcomReplicateJob {
  const outs = Array.from({ length: 5 }, (_, i) => succeededOutput(i));
  return {
    ...mainPlanJob(),
    status: "completed",
    plan: { outputs: over ? over(outs) : outs, reference_analysis_json: [], template_mapping_json: {}, generation_plan_json: {} }
  };
}

function partialFailedJob(...failedIdx: number[]): EcomReplicateJob {
  const idx = failedIdx.length ? failedIdx : [2];
  const job = completedJob((outs) => outs.map((o) => (idx.includes(o.index) ? output(o.index, { status: "failed" }) : o)));
  return { ...job, status: "partial_failed" };
}

// ── 编排辅助 ──
async function driveToPlan(mode: "main" | "detail" = "main") {
  render(<EcomDetailWizard />);
  fireEvent.click(
    screen.getByRole("button", {
      name: mode === "main" ? copy.workbench.ecomDetailModeMain : copy.workbench.ecomDetailModeDetail
    })
  );
  fireEvent.click(screen.getByText("set-ecom-detail-ref"));
  fireEvent.click(screen.getByText("set-ecom-detail-product"));
  fireEvent.change(screen.getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder), {
    target: { value: "保温杯" }
  });
  fireEvent.change(screen.getByPlaceholderText(copy.workbench.ecomDetailPointPlaceholder), {
    target: { value: "锁温" }
  });
  fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailPlan }));
  await screen.findByText(copy.workbench.ecomPlanTitle);
}

async function confirmCharge() {
  fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomPlanConfirm }));
  const chargeBtn = await screen.findByRole("button", { name: copy.workbench.ecomChargeConfirm });
  // 扣费点击触发 onConfirmCharge：其 confirmEcomReplicate().then 里的 5 个 setState（setJob/setPollError/
  // setChargeOpen/setStep/setConfirming）在同步 click 返回后、下一个 findBy 之前的**微任务间隙**落地——
  // 这正是 45 条 act warning 的唯一来源（fake timer 下轮询已由 shim 驱动、不再逃逸，独此一处在 act 外）。
  // 用 async act 包裹点击，把这波 confirm 后置状态更新收进 act 内。断言仍由各测试随后的 findBy/waitFor 承担。
  await act(async () => {
    fireEvent.click(chargeBtn);
  });
}

// ECOM-WIZARD-TEST-CLEANUP-0001 · fake timer 治 45 条 act warning + 19.5s（6 个真实 1500ms 轮询 timer）。
// 房规坑（tasks-context.test.tsx:143「waitFor would deadlock under fake timers」）：RTL 的
// jestFakeTimersAreEnabled() 只在 `typeof jest !== 'undefined'` 时才认得 fake timer——vitest 无全局 jest，
// 故默认 findBy/waitFor 在 fake timer 下死锁。补一个最小 shim（vitest fake setTimeout 已带 `clock` 特征，
// 只差 jest 这个门），让 RTL 走「fake timer 分支」：它每轮把 `jest.advanceTimersByTime(50)` 包在
// @testing-library/react 的 unstable_advanceTimersWrapper=act(...) 里推进——1500ms 轮询 setTimeout 因此在
// **act 内**触发，setJob 不再逃逸 act（warning 归零），且是 fake 时间（无真实等待，提速）。RTL 只调用
// advanceTimersByTime 这一个 jest 方法（interval=50 与 0），故 shim 仅需它。被测断言/findBy 全部原样保留。
const rtlFakeTimerShim = { advanceTimersByTime: (ms: number) => vi.advanceTimersByTime(ms) };
beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  (globalThis as unknown as { jest?: typeof rtlFakeTimerShim }).jest = rtlFakeTimerShim;
});
afterEach(() => {
  delete (globalThis as unknown as { jest?: typeof rtlFakeTimerShim }).jest;
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("EcomDetailWizard (电商详情图向导 · FIX1 真契约)", () => {
  it("上传校验：模式必选 → 缺参考图 → 缺商品图 → 缺信息 → 缺卖点，逐级拦截且不发起 plan", () => {
    render(<EcomDetailWizard />);
    const plan = () => fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailPlan }));

    plan();
    expect(screen.getByText(copy.errors.ecomDetailNeedMode)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailModeMain }));
    plan();
    expect(screen.getByText(copy.errors.ecomDetailNeedRef)).toBeInTheDocument();
    fireEvent.click(screen.getByText("set-ecom-detail-ref"));
    plan();
    expect(screen.getByText(copy.errors.ecomDetailNeedProduct)).toBeInTheDocument();
    fireEvent.click(screen.getByText("set-ecom-detail-product"));
    plan();
    expect(screen.getByText(copy.errors.ecomDetailNeedInfo)).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder), { target: { value: "保温杯" } });
    plan();
    expect(screen.getByText(copy.errors.ecomDetailNeedPoint)).toBeInTheDocument();

    expect(api.planEcomReplicate).not.toHaveBeenCalled();
  });

  it("齐全 → planEcomReplicate 请求体真契约（product_info dict + 参考图/商品图数组 + 卖点）", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    await driveToPlan("main");
    expect(api.planEcomReplicate).toHaveBeenCalledTimes(1);
    expect(api.planEcomReplicate).toHaveBeenCalledWith({
      output_mode: "main",
      reference_image_asset_ids: ["ecom-detail-ref-a1"],
      product_image_asset_ids: ["ecom-detail-product-a1"],
      product_info: { description: "保温杯" },
      selling_points: ["锁温"]
    });
    expect(api.confirmEcomReplicate).not.toHaveBeenCalled();
  });

  // ── ECOM-REF-LIMIT-UI-0001：参考图上限随模式动态（主图 5 / 详情 12）+ 修静默截断 ──
  it("参考图上限文案随模式：主图「1–5」/ 详情「1–12」；商品图恒「1–4」", () => {
    render(<EcomDetailWizard />);
    expect(screen.getByText(copy.workbench.ecomDetailProductLabel(4))).toBeInTheDocument(); // 商品图恒 4
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailModeMain }));
    expect(screen.getByText(copy.workbench.ecomDetailRefLabel(5))).toBeInTheDocument(); // 主图 5
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailModeDetail }));
    expect(screen.getByText(copy.workbench.ecomDetailRefLabel(12))).toBeInTheDocument(); // 详情 12
  });

  it("详情模式传满 12 张参考图 → planEcomReplicate 带 12 张（不 slice 静默截断）", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    render(<EcomDetailWizard />);
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailModeDetail }));
    fireEvent.click(screen.getByText("fill12-ecom-detail-ref"));
    fireEvent.click(screen.getByText("set-ecom-detail-product"));
    fireEvent.change(screen.getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder), { target: { value: "保温杯" } });
    fireEvent.change(screen.getByPlaceholderText(copy.workbench.ecomDetailPointPlaceholder), { target: { value: "锁温" } });
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailPlan }));
    await screen.findByText(copy.workbench.ecomPlanTitle);
    expect((api.planEcomReplicate.mock.calls[0][0] as { reference_image_asset_ids: string[] }).reference_image_asset_ids).toHaveLength(12);
  });

  it("🔴 详情传满 12 → 切主图（上限 5）→ 明确越限提示 + 不发 plan（绝不静默丢 7 张）", () => {
    render(<EcomDetailWizard />);
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailModeDetail }));
    fireEvent.click(screen.getByText("fill12-ecom-detail-ref"));
    fireEvent.click(screen.getByText("set-ecom-detail-product"));
    fireEvent.change(screen.getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder), { target: { value: "保温杯" } });
    fireEvent.change(screen.getByPlaceholderText(copy.workbench.ecomDetailPointPlaceholder), { target: { value: "锁温" } });
    // 切主图：已传 12 > 主图上限 5。
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailModeMain }));
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailPlan }));
    expect(screen.getByText(copy.errors.ecomDetailRefOverLimit(5))).toBeInTheDocument();
    expect(api.planEcomReplicate).not.toHaveBeenCalled();
  });

  it("规划表渲染：页码/主题(本地化)/尺寸/生成要点/原图不裁剪；总价取后端 total_credits（不硬编码）", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob({ total_credits: 999 }));
    await driveToPlan("main");
    expect(screen.getByText(copy.workbench.ecomPlanTitle)).toBeInTheDocument();
    // theme 机器键 layout_match → 本地化「版式复刻」
    expect(screen.getAllByText(copy.workbench.ecomReplicateTheme("layout_match")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("1024x1024")).toHaveLength(5);
    expect(screen.getAllByText(copy.workbench.ecomPlanNoCrop)).toHaveLength(5);
    expect(screen.getByText(copy.workbench.ecomPlanTotalPrice(999))).toBeInTheDocument();
  });

  it("资金安全：扣费门确认 → confirm 恰一次（防连点二次不重复扣费）", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(confirmMinimal());
    api.getEcomReplicateJob.mockResolvedValue(generatingJob());
    await driveToPlan("main");
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomPlanConfirm }));
    const chargeBtn = await screen.findByRole("button", { name: copy.workbench.ecomChargeConfirm });
    fireEvent.click(chargeBtn);
    fireEvent.click(chargeBtn); // 连点
    await waitFor(() => expect(api.confirmEcomReplicate).toHaveBeenCalledTimes(1));
    expect(api.confirmEcomReplicate).toHaveBeenCalledWith("job-1");
  });

  it("资金安全：取消扣费 → 不发起 confirm，停留规划步（不扣不生成）", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    await driveToPlan("main");
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomPlanConfirm }));
    fireEvent.click(await screen.findByRole("button", { name: copy.common.cancel }));
    expect(api.confirmEcomReplicate).not.toHaveBeenCalled();
    expect(screen.getByText(copy.workbench.ecomPlanTitle)).toBeInTheDocument();
  });

  it("生成中：confirm 只回 minimal（不覆盖 plan.outputs）→ 只显进度、禁分批（无下载/结果图）", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(confirmMinimal());
    api.getEcomReplicateJob.mockResolvedValue(generatingJob());
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomGenTitle)).toBeInTheDocument();
    expect(screen.getByText(copy.workbench.ecomGenProgress(0, 5))).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: copy.workbench.ecomResultDownload })).not.toBeInTheDocument();
  });

  it("轮询 GET 至完成 → 从「生成中」一次性切到结果（全程禁分批）", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(confirmMinimal());
    api.getEcomReplicateJob.mockResolvedValue(completedJob());
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomGenTitle)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: copy.workbench.ecomResultDownload })).not.toBeInTheDocument();
    expect(await screen.findByText(copy.workbench.ecomResultTitle, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: copy.workbench.ecomResultDownload })).toHaveLength(5);
  });

  it("原图红线：下载给原图 URL(<a download>) + 显示 AI 原始尺寸(actual_w/h) + 零 canvas", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(confirmMinimal());
    api.getEcomReplicateJob.mockResolvedValue(completedJob());
    await driveToPlan("main");
    await confirmCharge();
    const links = await screen.findAllByRole("link", { name: copy.workbench.ecomResultDownload }, { timeout: 3000 });
    expect(links).toHaveLength(5);
    expect(links[0]).toHaveAttribute("href", "https://cdn/full-0.png");
    expect(links[0]).toHaveAttribute("download");
    // FIX2：真 BE 无 preview_url，预览 <img> 也用 download_url（唯一图片 URL），不绑不存在的字段
    const imgs = document.querySelectorAll("img");
    expect(imgs).toHaveLength(5);
    expect(imgs[0]).toHaveAttribute("src", "https://cdn/full-0.png");
    expect(screen.getAllByText(copy.workbench.ecomResultSizeMain("1254x1254"))).toHaveLength(5);
    expect(document.querySelector("canvas")).toBeNull();
  });

  it("尺寸透明：actual_w/h 缺失 → 显示「尺寸以下载文件为准」，不冒充请求尺寸", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(confirmMinimal());
    api.getEcomReplicateJob.mockResolvedValue(
      completedJob((outs) => outs.map((o) => ({ ...o, actual_width: null, actual_height: null })))
    );
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomResultTitle, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getAllByText(copy.workbench.ecomResultSizeUnknown)).toHaveLength(5);
    expect(screen.queryByText(copy.workbench.ecomResultSizeMain("1024x1024"))).not.toBeInTheDocument();
  });

  it("下载防御：download_url 缺失 → 禁用态「原图暂不可用」，不渲染死链", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(confirmMinimal());
    api.getEcomReplicateJob.mockResolvedValue(
      completedJob((outs) => outs.map((o) => (o.index === 0 ? { ...o, download_url: null } : o)))
    );
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomResultTitle, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: copy.workbench.ecomResultDownload })).toHaveLength(4);
    expect(screen.getByText(copy.workbench.ecomResultDownloadUnavailable)).toBeInTheDocument();
  });

  it("单张重试：失败张走 retry(按 index) + 不二次扣费 + 回轮询完成后失败态消失", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(confirmMinimal());
    api.getEcomReplicateJob.mockResolvedValueOnce(partialFailedJob(2)).mockResolvedValue(completedJob());
    api.retryEcomReplicateOutput.mockResolvedValue(output(2, { status: "planned" }));
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomResultPartialHint, {}, { timeout: 3000 })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomResultRetry }));
    await waitFor(() => expect(api.retryEcomReplicateOutput).toHaveBeenCalledWith("job-1", 2));
    expect(api.confirmEcomReplicate).toHaveBeenCalledTimes(1); // 资金安全：不二次扣费

    // 重试 → 回生成中 → 轮询完成 → 失败态清除
    await waitFor(() => expect(screen.queryByText(copy.workbench.ecomResultFailed)).not.toBeInTheDocument(), { timeout: 3000 });
  });

  it("并发重试防丢动作：一张重试在途时，其余失败张的重试按钮同步禁用", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(confirmMinimal());
    api.getEcomReplicateJob.mockResolvedValue(partialFailedJob(2, 4));
    api.retryEcomReplicateOutput.mockReturnValue(new Promise(() => {})); // 首个重试保持在途
    await driveToPlan("main");
    await confirmCharge();
    const retryBtns = await screen.findAllByRole("button", { name: copy.workbench.ecomResultRetry }, { timeout: 3000 });
    expect(retryBtns).toHaveLength(2);
    fireEvent.click(retryBtns[0]);
    await waitFor(() => expect(screen.getByRole("button", { name: copy.workbench.ecomResultRetry })).toBeDisabled());
  });

  it("整单失败：status=failed / 空 outputs → 明确失败提示 + 返回入口，不渲染空网格坏图", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(confirmMinimal({ status: "failed" }));
    api.getEcomReplicateJob.mockResolvedValue({
      ...mainPlanJob(),
      status: "failed",
      plan: { outputs: [], reference_analysis_json: [], template_mapping_json: {}, generation_plan_json: {} }
    });
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomResultAllFailed, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: copy.workbench.ecomResultDownload })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: copy.workbench.ecomPlanBack })).toBeInTheDocument();
  });

  it("轮询瞬时失败：不跳结果、保持生成中 + 软提示，下一拍自愈后一次性展示", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(confirmMinimal());
    api.getEcomReplicateJob.mockRejectedValueOnce(new Error("boom")).mockResolvedValue(completedJob());
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomGenRetrying, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText(copy.workbench.ecomGenTitle)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: copy.workbench.ecomResultDownload })).not.toBeInTheDocument();
    expect(await screen.findByText(copy.workbench.ecomResultTitle, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: copy.workbench.ecomResultDownload })).toHaveLength(5);
  });

  it("详情模式：12 张规划 + total_credits 由后端(180) + 尺寸 768x1024", async () => {
    api.planEcomReplicate.mockResolvedValue(detailPlanJob());
    await driveToPlan("detail");
    expect(api.planEcomReplicate).toHaveBeenCalledWith(expect.objectContaining({ output_mode: "detail" }));
    expect(screen.getByText(copy.workbench.ecomPlanTotalPrice(180))).toBeInTheDocument();
    expect(screen.getAllByText("768x1024")).toHaveLength(12);
  });

  it("卖点上限：达 8 条后「添加卖点」按钮隐藏（BE selling_points ≤8）", () => {
    render(<EcomDetailWizard />);
    const add = () => fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailAddPoint }));
    // 初始 1 条，再加 7 次 → 8 条，按钮应消失
    for (let i = 0; i < 7; i++) add();
    expect(screen.queryByRole("button", { name: copy.workbench.ecomDetailAddPoint })).not.toBeInTheDocument();
  });
});
