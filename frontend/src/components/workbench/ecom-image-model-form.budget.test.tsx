import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// 专测**合并客户端守卫**（D1 兜底）：把 ReferenceImagesPicker mock 成一个受控 input（change value=N → onChange(N 个 id)），
// 从而**确定性**地把表单置于任意「商品图 + 模特图」张数——无需真实上传/并发竞态即可覆盖「合计越限」这条兜底闸。
// 真实 picker 的联动 max / 非静默阻断 / 上传，在主测试文件用真组件覆盖（此处只关心表单对合计的守卫反应）。
vi.mock("@/components/workbench/reference-images-picker", () => ({
  ReferenceImagesPicker: ({ onChange, inputId }: { onChange?: (ids: string[]) => void; inputId?: string }) => (
    <input
      data-testid={`count-${inputId}`}
      onChange={(e) => onChange?.(Array.from({ length: Number(e.currentTarget.value) || 0 }, (_, i) => `${inputId}-${i}`))}
    />
  )
}));

const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const modelMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const stylesMock = vi.hoisted(() => ({ data: [] as Array<{ id: string; name: string }>, isLoading: false, isError: false }));
const trackExistingMock = vi.hoisted(() => vi.fn());
const tasksMock = vi.hoisted(() => ({ tasks: [] as Array<Record<string, unknown>> }));
vi.mock("@/lib/api/hooks", () => ({
  useUploadImage: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: uploadMock.isPending }),
  useModelImage: () => ({ mutateAsync: modelMock.mutateAsync, isPending: modelMock.isPending }),
  useModelStyles: () => ({ data: stylesMock.data, isLoading: stylesMock.isLoading, isError: stylesMock.isError })
}));
vi.mock("@/lib/videos/tasks-context", () => ({ useVideoTasks: () => ({ tasks: tasksMock.tasks, trackExisting: trackExistingMock }) }));

import { EcomImageModelForm } from "./ecom-image-model-form";

beforeEach(() => {
  window.localStorage.clear();
  modelMock.isPending = false;
  stylesMock.data = [];
  tasksMock.tasks = [];
  modelMock.mutateAsync.mockResolvedValue({ task_id: "t-1", status: "queued" });
});
afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

const setCount = (inputId: string, n: number) => fireEvent.change(screen.getByTestId(`count-${inputId}`), { target: { value: String(n) } });

describe("EcomImageModelForm · 合计守卫（D1 合并兜底）", () => {
  it("🔴 承重·合并超限兜底：商品图 4 + 模特图 3 = 7 → 越限警示 + 生成禁用（变异：去合并守卫 → 红）", () => {
    render(<EcomImageModelForm />);
    setCount("ecom-model-product", 4);
    setCount("ecom-model-model", 3);
    // 合计 7 > 6：红色越限警示（role=alert，非静默）+ 生成禁用（绝不发越限请求，兜住并发上传竞态）。
    expect(screen.getByRole("alert")).toHaveTextContent(copy.workbench.ecomModelOverLimit);
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
  });

  it("合计正好 6（商品图 4 + 模特图 2）→ 无越限、已选满、可生成", () => {
    render(<EcomImageModelForm />);
    setCount("ecom-model-product", 4);
    setCount("ecom-model-model", 2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText(copy.workbench.ecomModelBudgetFull)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成" })).toBeEnabled();
  });

  it("越限后移除到 6 → 警示消除、生成恢复可用（守卫随合计实时联动）", () => {
    render(<EcomImageModelForm />);
    setCount("ecom-model-product", 4);
    setCount("ecom-model-model", 3); // 7 → 越限
    expect(screen.getByRole("button", { name: "生成" })).toBeDisabled();
    setCount("ecom-model-model", 2); // 回到 6
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成" })).toBeEnabled();
  });
});
