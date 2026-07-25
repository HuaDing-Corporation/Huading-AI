import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { EcomReplicateJob, EcomReplicatePlanOutput } from "@/lib/api/ecom-replicate";

// ECOM-SUBTOOL-KEEPALIVE-UI-0001 · 承重：电商图切**子工具**不再丢输入
// （顶层 mode 已由 WORKBENCH-KEEPALIVE-UI-0001 治好，这是同一个病的更小范围）。
// 用**真实**三个子工具跑真实 state；一律按 subtool-* scope 断言 —— 白底图与 AI 模特共用外壳 EcomImageTool，
// 常驻后「生成方式」group /「生成」按钮 / role=switch /「画面比例」在容器层各有两份，全局查询必撞。
// 变异门：把 ecom-image-workbench.tsx 的常驻挂载改回条件渲染 → 本文件必红。

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
// 参考图/商品图上传占位（沿用 ecom-detail-wizard.test.tsx 的口径）：按 inputId 注入 asset id。
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
    </div>
  )
}));
vi.mock("@/lib/videos/tasks-context", () => ({
  useVideoTasks: () => ({ tasks: [], createAndTrack: vi.fn(), trackExisting: vi.fn(), refreshTask: vi.fn(), retryTask: vi.fn() })
}));
vi.mock("@/lib/api/hooks", () => ({
  useUploadImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadProductImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCutoutImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCutoutBatch: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useModelImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useModelBatch: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useModelStyles: () => ({ data: [{ id: "s1", name: "工作室柔光" }], isLoading: false })
}));

import { EcomImageWorkbench } from "./ecom-image-workbench";

afterEach(() => vi.clearAllMocks());

const subtool = (id: string) => within(screen.getByTestId(`subtool-${id}`));
const gotoSubtool = (label: string) => fireEvent.click(screen.getByRole("button", { name: label }));

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
function mainPlanJob(): EcomReplicateJob {
  return {
    job_id: "job-1",
    status: "plan_ready",
    heartbeat_at: null, // GEN-HEARTBEAT-UI-0001 · FIX3：BE 详情图响应键恒在（plan_ready → null）
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
    }
  };
}

/** 把详情图向导开到「规划表」步（已产出 job、尚未扣费）。 */
async function driveDetailToPlan() {
  const d = subtool("detail");
  fireEvent.click(d.getByRole("button", { name: copy.workbench.ecomDetailModeMain }));
  fireEvent.click(d.getByText("set-ecom-detail-ref"));
  fireEvent.click(d.getByText("set-ecom-detail-product"));
  fireEvent.change(d.getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder), { target: { value: "保温杯" } });
  fireEvent.change(d.getByPlaceholderText(copy.workbench.ecomDetailPointPlaceholder), { target: { value: "锁温" } });
  fireEvent.click(d.getByRole("button", { name: copy.workbench.ecomDetailPlan }));
  await subtool("detail").findByText(copy.workbench.ecomPlanTitle);
}

describe("ECOM-SUBTOOL-KEEPALIVE-UI-0001 · 切子工具不丢输入（承重）", () => {
  it("白底图 ↔ AI 模特：两边的输入切走再回来都原样保留", () => {
    render(<EcomImageWorkbench />);

    // 白底图：背景选「透明底」（默认白底）。
    fireEvent.click(subtool("cutout").getByRole("button", { name: copy.workbench.ecomBgTransparent }));
    expect(subtool("cutout").getByRole("button", { name: copy.workbench.ecomBgTransparent })).toHaveAttribute(
      "aria-pressed",
      "true"
    );

    // 切 AI 模特：填自定义补充。
    gotoSubtool(copy.workbench.ecomSubToolModel);
    fireEvent.change(subtool("model").getByPlaceholderText(copy.workbench.ecomCustomPlaceholder), {
      target: { value: "工作室柔光、简洁白底" }
    });

    // 切回白底图：透明底仍选中（旧行为：卸载 → 回落默认「白底」）。
    gotoSubtool(copy.workbench.ecomSubToolCutout);
    expect(subtool("cutout").getByRole("button", { name: copy.workbench.ecomBgTransparent })).toHaveAttribute(
      "aria-pressed",
      "true"
    );

    // 再切 AI 模特：自定义补充还在。
    gotoSubtool(copy.workbench.ecomSubToolModel);
    expect(subtool("model").getByPlaceholderText(copy.workbench.ecomCustomPlaceholder)).toHaveValue("工作室柔光、简洁白底");
  });

  it("AI 模特 ↔ 电商详情图：两边的输入切走再回来都原样保留", () => {
    render(<EcomImageWorkbench />);

    gotoSubtool(copy.workbench.ecomSubToolModel);
    fireEvent.change(subtool("model").getByPlaceholderText(copy.workbench.ecomCustomPlaceholder), {
      target: { value: "暖光街头微笑站姿" }
    });

    gotoSubtool(copy.workbench.ecomSubToolDetail);
    fireEvent.change(subtool("detail").getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder), {
      target: { value: "316 不锈钢保温杯" }
    });

    gotoSubtool(copy.workbench.ecomSubToolModel);
    expect(subtool("model").getByPlaceholderText(copy.workbench.ecomCustomPlaceholder)).toHaveValue("暖光街头微笑站姿");

    gotoSubtool(copy.workbench.ecomSubToolDetail);
    expect(subtool("detail").getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder)).toHaveValue("316 不锈钢保温杯");
  });

  it("电商详情图 ↔ 白底图：两边的输入切走再回来都原样保留", () => {
    render(<EcomImageWorkbench />);

    gotoSubtool(copy.workbench.ecomSubToolDetail);
    fireEvent.change(subtool("detail").getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder), {
      target: { value: "陶瓷马克杯" }
    });

    gotoSubtool(copy.workbench.ecomSubToolCutout);
    fireEvent.click(subtool("cutout").getByRole("button", { name: copy.workbench.ecomBgTransparent }));

    gotoSubtool(copy.workbench.ecomSubToolDetail);
    expect(subtool("detail").getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder)).toHaveValue("陶瓷马克杯");

    gotoSubtool(copy.workbench.ecomSubToolCutout);
    expect(subtool("cutout").getByRole("button", { name: copy.workbench.ecomBgTransparent })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  // 🔴🔴 本包收益最大的一条：详情图向导是多步状态机，且持有**已产出的整单 job**（plan.outputs + total_credits）。
  // 旧行为下切走子工具即卸载 → 向导整个回到第 1 步、规划结果凭空消失（若已确认扣费，用户是付了钱的）。
  it("详情图向导：走到「规划表」→ 切走 → 切回 → 仍停在规划表，且不重新发起 plan", async () => {
    api.planEcomReplicate.mockResolvedValue(mainPlanJob());
    render(<EcomImageWorkbench />);

    gotoSubtool(copy.workbench.ecomSubToolDetail);
    await driveDetailToPlan();

    // 切走再回来。
    gotoSubtool(copy.workbench.ecomSubToolCutout);
    gotoSubtool(copy.workbench.ecomSubToolDetail);

    // 仍停在规划表（旧行为：回到 upload 步，job 全丢）。
    expect(subtool("detail").getByText(copy.workbench.ecomPlanTitle)).toBeInTheDocument();
    expect(subtool("detail").getByText(copy.workbench.ecomPlanTotalPrice(75))).toBeInTheDocument();
    // 不重新规划（不重复请求、更不重复扣费）。
    expect(api.planEcomReplicate).toHaveBeenCalledTimes(1);
    expect(api.confirmEcomReplicate).not.toHaveBeenCalled();
  });

  it("三子工具并存：各自输入互不串台，且只有当前子工具可见", () => {
    render(<EcomImageWorkbench />);

    fireEvent.click(subtool("cutout").getByRole("button", { name: copy.workbench.ecomBgTransparent }));

    gotoSubtool(copy.workbench.ecomSubToolModel);
    fireEvent.change(subtool("model").getByPlaceholderText(copy.workbench.ecomCustomPlaceholder), {
      target: { value: "模特补充" }
    });

    gotoSubtool(copy.workbench.ecomSubToolDetail);
    fireEvent.change(subtool("detail").getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder), {
      target: { value: "详情商品信息" }
    });

    // 三份都还挂着、各自的值互不污染。
    // 注：隐藏子工具的元素**不在可及性树里**（[hidden] → display:none），RTL 的 getByRole 默认只查可及性树，
    // 故这里必须显式 hidden:true 才取得到 —— 这本身就是 a11y 生效的旁证（下面两条用 getByPlaceholderText，
    // 它不查可及性树，故不受影响）。
    expect(
      subtool("cutout").getByRole("button", { name: copy.workbench.ecomBgTransparent, hidden: true })
    ).toHaveAttribute("aria-pressed", "true");
    expect(subtool("model").getByPlaceholderText(copy.workbench.ecomCustomPlaceholder)).toHaveValue("模特补充");
    expect(subtool("detail").getByPlaceholderText(copy.workbench.ecomDetailInfoPlaceholder)).toHaveValue("详情商品信息");

    // 只有当前子工具可见。
    expect(screen.getByTestId("subtool-detail")).toBeVisible();
    expect(screen.getByTestId("subtool-cutout")).not.toBeVisible();
    expect(screen.getByTestId("subtool-model")).not.toBeVisible();
  });
});
