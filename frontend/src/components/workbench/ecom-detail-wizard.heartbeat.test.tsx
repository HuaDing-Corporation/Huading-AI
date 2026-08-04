import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type {
  EcomReplicateConfirmAccepted,
  EcomReplicateJob,
  EcomReplicatePlanOutput
} from "@/lib/api/ecom-replicate";

// GEN-HEARTBEAT-UI-0001 · FIX1 · **通道②**（电商详情图）承重。
//
// 冻结原文说"沿用现有 SSE 通道、不新开通道"，暗含只有一条路——但详情图**根本没有 Redis/SSE 通道**
// （只有逐张 DB 状态），所以它的心跳走 **GET /ecom-images/replicate/{id} 轮询响应**。两条路各按各的形态做：
// 本链路**不走 tasks-context 看门狗**，故本文件不引入 VideoTasksProvider（门11 的结构证明）。
//
// 🔴 heartbeat_at **可能是 null**（Redis 不可用时 BE 降级，业务轮询不受影响）→ 必须优雅回落。
// 与既有 ecom-detail-wizard.test.tsx 同口径（mock adapter + fake timer + RTL shim），**不动那个文件**。
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

vi.mock("@/components/workbench/reference-images-picker", () => ({
  ReferenceImagesPicker: ({ onChange, inputId }: { onChange?: (ids: string[]) => void; inputId?: string }) => (
    <button type="button" onClick={() => onChange?.([`${inputId}-a1`])}>{`set-${inputId}`}</button>
  )
}));

import { EcomDetailWizard } from "./ecom-detail-wizard";

function output(index: number): EcomReplicatePlanOutput {
  return {
    id: `o${index}`,
    index,
    theme: "layout_match",
    reference_asset_id: "ref-1",
    product_asset_id: "prod-1",
    requested_size: "1024x1024",
    requested_aspect: "1:1",
    status: "planned",
    prompt: `复刻要点 ${index}`,
    asset_id: null,
    download_url: null,
    actual_width: null,
    actual_height: null
  };
}
function planJob(): EcomReplicateJob {
  return {
    job_id: "job-1",
    status: "plan_ready",
    // 键恒在（BE schema `str | None` 默认 None）；plan_ready 阶段还没开始生成 → null。
    heartbeat_at: null,
    output_mode: "main",
    output_count: 5,
    total_credits: 650,
    credit_rate: 130,
    requested_size: "1024x1024",
    requested_aspect: "1:1",
    plan: {
      outputs: Array.from({ length: 5 }, (_, i) => output(i)),
      reference_analysis_json: [],
      template_mapping_json: {},
      generation_plan_json: {}
    }
  };
}
const confirmMinimal: EcomReplicateConfirmAccepted = {
  job_id: "job-1",
  status: "generating",
  output_count: 5,
  total_credits: 650
};
/** 生成中的轮询响应；heartbeat_at 由各用例给（含 null = BE 降级）。 */
function generatingJob(heartbeat_at: string | null): EcomReplicateJob {
  return { ...planJob(), status: "generating", heartbeat_at };
}

const T_CONFIRM = 1_756_000_000_000;

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

/**
 * 驱到"生成中"，并把**计时基准钉死在 T_CONFIRM**（generatingSince = 确认扣费那一刻的 Date.now()）：
 * 故在最后一次点击前 setSystemTime，避免 RTL 的 fake-timer 推进把基准冲成不确定值。
 */
async function driveToGenerating(heartbeat_at: string | null) {
  api.planEcomReplicate.mockResolvedValue(planJob());
  api.confirmEcomReplicate.mockResolvedValue(confirmMinimal);
  api.getEcomReplicateJob.mockResolvedValue(generatingJob(heartbeat_at));

  render(<EcomDetailWizard />);
  fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailModeMain }));
  fireEvent.click(screen.getByText("set-ecom-detail-ref"));
  fireEvent.click(screen.getByText("set-ecom-detail-product"));
  fireEvent.change(screen.getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder), { target: { value: "保温杯" } });
  fireEvent.change(screen.getByPlaceholderText(copy.workbench.ecomDetailPointPlaceholder), { target: { value: "锁温" } });
  fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomDetailPlan }));
  await screen.findByText(copy.workbench.ecomPlanTitle);

  fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomPlanConfirm }));
  const chargeBtn = await screen.findByRole("button", { name: copy.workbench.ecomChargeConfirm });
  vi.setSystemTime(T_CONFIRM); // ← 计时基准钉死
  await act(async () => {
    fireEvent.click(chargeBtn);
  });
  await screen.findByText(copy.workbench.ecomGenTitle); // 已在生成中
  // 让第一拍轮询（1500ms）落地，把带 heartbeat_at 的响应喂进来。
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1_600);
  });
}

describe("GEN-HEARTBEAT-UI-0001 · FIX1 · 详情图轮询心跳（通道②）", () => {
  // 🔴 门9：轮询响应带 heartbeat_at → 等待态显示真实计时（确定文案）。
  // 变异：把 ecom-detail-wizard.tsx 里 `job.heartbeat_at != null && ...` 那行删掉 → 本条必红。
  it("heartbeat_at 有值 → 等待态显示诚实计时，且随时间推进", async () => {
    await driveToGenerating("2026-07-25T10:00:00Z");

    // 基准 T_CONFIRM 起算：把钟推到 +200s（3 分 20 秒），并让 ElapsedSince 的 1s 心跳重算一次。
    vi.setSystemTime(T_CONFIRM + 199_000);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(screen.getByText("仍在生成（已 3 分 20 秒）")).toBeInTheDocument();

    // 再推 60s → 文案确实在走（真实计时，不是恒定串）。
    vi.setSystemTime(T_CONFIRM + 259_000);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(screen.getByText("仍在生成（已 4 分 20 秒）")).toBeInTheDocument();
    expect(screen.queryByText("仍在生成（已 3 分 20 秒）")).toBeNull();
  });

  // 🔴 门10：heartbeat_at = null（Redis 不可用降级）→ 回落到原等待态。
  // 不许 NaN、不许 Invalid Date、不许因此判失败；原等待态的两行文案原样都在。
  // 变异：把 `job.heartbeat_at != null` 这个判空去掉（无条件渲染计时）→ 本条必红。
  it("heartbeat_at = null → 回落原等待态：无 NaN / 无 Invalid Date / 不判失败", async () => {
    await driveToGenerating(null);

    vi.setSystemTime(T_CONFIRM + 200_000);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    const body = document.body.textContent ?? "";
    expect(body).not.toContain("NaN");
    expect(body).not.toContain("Invalid Date");
    expect(body).not.toContain("仍在生成"); // 没有心跳证据 → 一个字都不说
    // 原等待态**原样保留**：标题 + 进度文案 + 等待提示都在，且没跳到失败/结果页。
    expect(screen.getByText(copy.workbench.ecomGenTitle)).toBeInTheDocument();
    expect(screen.getByText(copy.workbench.ecomGenWait)).toBeInTheDocument();
    expect(screen.getByText(copy.workbench.ecomGenProgress(0, 5))).toBeInTheDocument();
    expect(screen.queryByText(copy.workbench.ecomResultTitle)).toBeNull();
  });

  // 🔴 门11：两条通道各自独立——本向导**不挂 VideoTasksProvider / 不走 tasks-context 看门狗**，
  // 心跳照样工作。（另一半证据在回执：ecom-detail-wizard.tsx 不 import tasks-context。）
  it("与图片生成通道独立：没有 VideoTasksProvider 也照常显示心跳计时", async () => {
    await driveToGenerating("2026-07-25T10:00:00Z");
    vi.setSystemTime(T_CONFIRM + 59_000);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(screen.getByText("仍在生成（已 1 分 0 秒）")).toBeInTheDocument();
  });
});
