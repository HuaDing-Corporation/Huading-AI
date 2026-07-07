import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { EcomReplicateJob } from "@/lib/api/ecom-replicate";

// ECOM-REPLICATE-UI-0001 · 向导编排 TDD。隔离网络：mock adapter（真契约已由 ecom-replicate.test.ts 8 条 MSW
// 用例覆盖），此处只锁 4-Step 状态机 + 资金安全（扣费恰一次/取消不扣/重试不二次扣）+ 原图红线（下载原图 bytes、
// 显示原始尺寸、零 canvas）+ 禁分批。isEcomReplicateSettled 保留真实（组件轮询终态判定依赖它）。
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

// 参考图/商品图上传占位：按 inputId 区分（隔离上传，reference-images-picker 有专测），点击即上抛 asset_id。
vi.mock("@/components/workbench/reference-images-picker", () => ({
  ReferenceImagesPicker: ({ onChange, inputId }: { onChange?: (ids: string[]) => void; inputId?: string }) => (
    <button type="button" onClick={() => onChange?.([`${inputId}-a1`])}>
      {`set-${inputId}`}
    </button>
  )
}));

import { EcomDetailWizard } from "./ecom-detail-wizard";

// ── 契约夹具（镜像 BE 形状） ──
function mainPlanJob(over?: Partial<EcomReplicateJob>): EcomReplicateJob {
  return {
    job_id: "job-1",
    status: "plan_ready",
    output_mode: "main",
    total_credits: 75,
    plan: Array.from({ length: 5 }, (_, i) => ({
      page_no: i + 1,
      theme: `主题${i + 1}`,
      ref_label: "ref_001",
      main_title: `主标题${i + 1}`,
      sub_title: `卖点${i + 1}`,
      display_style: "白底居中",
      requested_size: "1024x1024",
      no_crop_notice: "原图输出，不裁剪",
      reused: false
    })),
    outputs: [],
    ...over
  };
}

function detailPlanJob(): EcomReplicateJob {
  return {
    job_id: "job-2",
    status: "plan_ready",
    output_mode: "detail",
    total_credits: 180,
    plan: Array.from({ length: 12 }, (_, i) => ({
      page_no: i + 1,
      theme: `详情主题${i + 1}`,
      ref_label: "ref_001",
      main_title: `主标题${i + 1}`,
      sub_title: `卖点${i + 1}`,
      display_style: "场景展示",
      requested_size: "768x1024",
      no_crop_notice: "原图输出，不裁剪",
      reused: false
    })),
    outputs: []
  };
}

function generatingJob(): EcomReplicateJob {
  return {
    ...mainPlanJob(),
    status: "generating",
    outputs: Array.from({ length: 5 }, (_, i) => ({
      page_no: i + 1,
      status: "pending",
      asset_id: null,
      download_url: null,
      preview_url: null,
      requested_size: "1024x1024",
      actual_dimensions: null,
      error_code: null
    }))
  };
}

function completedJob(): EcomReplicateJob {
  return {
    ...mainPlanJob(),
    status: "completed",
    outputs: Array.from({ length: 5 }, (_, i) => ({
      page_no: i + 1,
      status: "succeeded",
      asset_id: `asset-${i + 1}`,
      download_url: `https://cdn/full-${i + 1}.png`,
      preview_url: `https://cdn/prev-${i + 1}.png`,
      requested_size: "1024x1024",
      actual_dimensions: "1254x1254",
      error_code: null
    }))
  };
}

function failedOutput(page: number): EcomReplicateJob["outputs"][number] {
  return { page_no: page, status: "failed", asset_id: null, download_url: null, preview_url: null, requested_size: "1024x1024", actual_dimensions: null, error_code: "PROVIDER_ERROR" };
}

function partialFailedJob(): EcomReplicateJob {
  const job = completedJob();
  job.status = "partial_failed";
  job.outputs[2] = failedOutput(3);
  return job;
}

function twoFailedJob(): EcomReplicateJob {
  const job = completedJob();
  job.status = "partial_failed";
  job.outputs[2] = failedOutput(3);
  job.outputs[4] = failedOutput(5);
  return job;
}

/** 已完成但后端未回 actual_dimensions（契约允许 null）→ 前端不得冒充请求尺寸。 */
function completedJobNoDims(): EcomReplicateJob {
  const job = completedJob();
  job.outputs = job.outputs.map((o) => ({ ...o, actual_dimensions: null }));
  return job;
}

/** 已完成但某张缺 download_url → 该张须给禁用态而非死链。 */
function completedJobMissingDownload(): EcomReplicateJob {
  const job = completedJob();
  job.outputs[0] = { ...job.outputs[0], download_url: null };
  return job;
}

/** 整单失败（无任何输出）。 */
function integralFailedJob(): EcomReplicateJob {
  return { ...mainPlanJob(), status: "failed", outputs: [] };
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
  fireEvent.click(await screen.findByRole("button", { name: copy.workbench.ecomChargeConfirm }));
}

beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.clearAllMocks());

describe("EcomDetailWizard (电商详情图向导 · 4 Step 状态机)", () => {
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

    fireEvent.change(screen.getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder), {
      target: { value: "保温杯" }
    });
    plan();
    expect(screen.getByText(copy.errors.ecomDetailNeedPoint)).toBeInTheDocument();

    expect(api.planEcomReplicate).not.toHaveBeenCalled();
  });

  it("齐全 → planEcomReplicate 请求体正确（模式/参考图/商品图/信息/卖点，不扣费）", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    await driveToPlan("main");
    expect(api.planEcomReplicate).toHaveBeenCalledTimes(1);
    expect(api.planEcomReplicate).toHaveBeenCalledWith({
      output_mode: "main",
      reference_image_asset_ids: ["ecom-detail-ref-a1"],
      product_image_asset_ids: ["ecom-detail-product-a1"],
      product_info: "保温杯",
      selling_points: ["锁温"]
    });
    // plan 阶段不扣费
    expect(api.confirmEcomReplicate).not.toHaveBeenCalled();
  });

  it("规划表渲染：§10 列 + 未裁剪提示；总价取后端 total_credits（不前端硬编码）", async () => {
    // 后端给非常规 total_credits(999) → UI 必须原样透出，证明读后端值而非硬编码 75。
    api.planEcomReplicate.mockResolvedValue(mainPlanJob({ total_credits: 999 }));
    await driveToPlan("main");
    expect(screen.getByText(copy.workbench.ecomPlanTitle)).toBeInTheDocument();
    expect(screen.getAllByText("原图输出，不裁剪")).toHaveLength(5);
    expect(screen.getAllByText("1024x1024")).toHaveLength(5);
    expect(screen.getByText(copy.workbench.ecomPlanTotalPrice(999))).toBeInTheDocument();
  });

  it("参考图不足 → 循环复用徽标 + 提示如实展示（前端不改写 BE 标注）", async () => {
    const job = mainPlanJob();
    job.plan[3].reused = true;
    job.plan[4].reused = true;
    api.planEcomReplicate.mockResolvedValue(job);
    await driveToPlan("main");
    expect(screen.getByText(copy.workbench.ecomPlanReuseHint)).toBeInTheDocument();
    expect(screen.getAllByText(copy.workbench.ecomPlanReusedBadge)).toHaveLength(2);
  });

  it("资金安全：扣费门确认 → confirm 恰一次（防连点二次不重复扣费）", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(generatingJob());
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

  it("生成中：只显进度、禁分批（无下载/结果图）", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(generatingJob());
    api.getEcomReplicateJob.mockResolvedValue(generatingJob());
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomGenTitle)).toBeInTheDocument();
    expect(screen.getByText(copy.workbench.ecomGenProgress(0, 5))).toBeInTheDocument();
    // 禁分批：生成中不得出现任何下载/结果图入口
    expect(screen.queryByRole("link", { name: copy.workbench.ecomResultDownload })).not.toBeInTheDocument();
  });

  it("轮询至完成 → 从「生成中」一次性切到结果（全程禁分批）", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(generatingJob());
    api.getEcomReplicateJob.mockResolvedValue(completedJob());
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomGenTitle)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: copy.workbench.ecomResultDownload })).not.toBeInTheDocument();
    // 轮询(1500ms)后完成 → 一次性 5 张齐现
    expect(await screen.findByText(copy.workbench.ecomResultTitle, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: copy.workbench.ecomResultDownload })).toHaveLength(5);
  });

  it("原图红线：下载给原图 URL(<a download>) + 显示 AI 原始尺寸 + 零 canvas 后处理", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(completedJob());
    await driveToPlan("main");
    await confirmCharge();
    const links = await screen.findAllByRole("link", { name: copy.workbench.ecomResultDownload });
    expect(links).toHaveLength(5);
    // 下载 = 原图 bytes（href 指原图 URL + download 属性），无前端裁剪/重导
    expect(links[0]).toHaveAttribute("href", "https://cdn/full-1.png");
    expect(links[0]).toHaveAttribute("download");
    // 不隐藏原始尺寸（§18：显示 AI 实返尺寸 + 未自动裁剪告知）
    expect(screen.getAllByText(copy.workbench.ecomResultSizeMain("1254x1254"))).toHaveLength(5);
    // 零 canvas：无任何前端 crop/resize/压缩/重导
    expect(document.querySelector("canvas")).toBeNull();
  });

  it("单张重试：失败图走 retry(带页码) + 不二次扣费 + 重试成功失败态消失", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(partialFailedJob());
    api.retryEcomReplicateOutput.mockResolvedValue(completedJob());
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomResultPartialHint)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomResultRetry }));
    await waitFor(() => expect(api.retryEcomReplicateOutput).toHaveBeenCalledWith("job-1", 3));
    // 资金安全：单张重试绝不触发二次 confirm 扣费
    expect(api.confirmEcomReplicate).toHaveBeenCalledTimes(1);
    // 重试成功 → 失败态清除
    await waitFor(() => expect(screen.queryByText(copy.workbench.ecomResultFailed)).not.toBeInTheDocument());
  });

  it("详情模式：12 张规划 + total_credits 由后端(180) + 尺寸 768x1024", async () => {
    api.planEcomReplicate.mockResolvedValue(detailPlanJob());
    await driveToPlan("detail");
    expect(api.planEcomReplicate).toHaveBeenCalledWith(expect.objectContaining({ output_mode: "detail" }));
    expect(screen.getByText(copy.workbench.ecomPlanTotalPrice(180))).toBeInTheDocument();
    expect(screen.getAllByText("768x1024")).toHaveLength(12);
  });

  it("轮询瞬时失败：不跳结果、保持生成中 + 软提示，下一拍自愈后一次性展示（不把 pending 当成品）", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(generatingJob());
    // 首拍 GET reject（瞬时 500/离线）→ 次拍恢复完成。
    api.getEcomReplicateJob.mockRejectedValueOnce(new Error("boom")).mockResolvedValue(completedJob());
    await driveToPlan("main");
    await confirmCharge();
    // 首拍失败后仍在生成中 + 软提示，绝不出现结果/下载入口。
    expect(await screen.findByText(copy.workbench.ecomGenRetrying, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText(copy.workbench.ecomGenTitle)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: copy.workbench.ecomResultDownload })).not.toBeInTheDocument();
    // 次拍自愈 → 一次性 5 张。
    expect(await screen.findByText(copy.workbench.ecomResultTitle, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: copy.workbench.ecomResultDownload })).toHaveLength(5);
  });

  it("原图尺寸透明：actual_dimensions 缺失 → 显示「尺寸以下载文件为准」，不把请求尺寸冒充实际输出", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(completedJobNoDims());
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomResultTitle)).toBeInTheDocument();
    // 不得对未回尺寸的图断言「AI 原始输出尺寸：1024x1024」（请求尺寸冒充实际）
    expect(screen.getAllByText(copy.workbench.ecomResultSizeUnknown)).toHaveLength(5);
    expect(screen.queryByText(copy.workbench.ecomResultSizeMain("1024x1024"))).not.toBeInTheDocument();
  });

  it("下载防御：download_url 缺失 → 禁用态「原图暂不可用」，不渲染死链", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(completedJobMissingDownload());
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomResultTitle)).toBeInTheDocument();
    // 缺 download_url 的那张不出现可点下载链接（4 张有链 + 1 张禁用态）
    expect(screen.getAllByRole("link", { name: copy.workbench.ecomResultDownload })).toHaveLength(4);
    expect(screen.getByText(copy.workbench.ecomResultDownloadUnavailable)).toBeInTheDocument();
  });

  it("并发重试防丢动作：一张重试在途时，其余失败张的重试按钮同步禁用", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(twoFailedJob());
    // 让首个重试保持在途（不 resolve），观察其余按钮是否被禁用。
    api.retryEcomReplicateOutput.mockReturnValue(new Promise(() => {}));
    await driveToPlan("main");
    await confirmCharge();
    const retryBtns = await screen.findAllByRole("button", { name: copy.workbench.ecomResultRetry });
    expect(retryBtns).toHaveLength(2);
    fireEvent.click(retryBtns[0]);
    // 首张进入「重试中…」，第二张（未在途）也被禁用，避免点了没反应。
    await waitFor(() => expect(screen.getByRole("button", { name: copy.workbench.ecomResultRetry })).toBeDisabled());
  });

  it("整单失败：status=failed / 空 outputs → 明确失败提示 + 返回入口，不渲染空网格坏图", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    api.confirmEcomReplicate.mockResolvedValue(integralFailedJob());
    await driveToPlan("main");
    await confirmCharge();
    expect(await screen.findByText(copy.workbench.ecomResultAllFailed)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: copy.workbench.ecomResultDownload })).not.toBeInTheDocument();
    // 提供返回入口
    expect(screen.getByRole("button", { name: copy.workbench.ecomPlanBack })).toBeInTheDocument();
  });
});
