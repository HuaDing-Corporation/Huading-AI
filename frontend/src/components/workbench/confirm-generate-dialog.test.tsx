import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CreateVideoRequest } from "@/lib/api/types";

const est = vi.hoisted(() => ({
  mutate: vi.fn(),
  reset: vi.fn(),
  isPending: false,
  data: undefined as { estimated_credits: number; unit: string; note?: string } | undefined
}));

vi.mock("@/lib/api/hooks", () => ({
  useEstimateVideo: () => ({
    mutate: est.mutate,
    reset: est.reset,
    isPending: est.isPending,
    data: est.data
  })
}));

import { ConfirmGenerateDialog } from "./confirm-generate-dialog";

const req: CreateVideoRequest = { topic: "咖啡", voice_id: "v1" };

beforeEach(() => {
  est.mutate.mockClear();
  est.reset.mockClear();
  est.isPending = false;
  est.data = undefined;
});
afterEach(() => vi.clearAllMocks());

function renderDialog(overrides: Partial<Parameters<typeof ConfirmGenerateDialog>[0]> = {}) {
  return render(
    <ConfirmGenerateDialog
      open
      request={req}
      submitting={false}
      onConfirm={vi.fn()}
      onCancel={vi.fn()}
      {...overrides}
    />
  );
}

describe("ConfirmGenerateDialog", () => {
  it("fetches the estimate on open and shows credits + the irreversible warning", () => {
    est.data = { estimated_credits: 12, unit: "credits" };
    renderDialog();
    expect(est.mutate).toHaveBeenCalledWith(req);
    expect(screen.getByText("确定生成")).toBeInTheDocument(); // title
    expect(screen.getByText("12")).toBeInTheDocument(); // credits value
    expect(
      screen.getByText("确定生成即会消耗积分，生成过程中无法取消！")
    ).toBeInTheDocument();
  });

  it("shows the loading state while estimating", () => {
    est.isPending = true;
    renderDialog();
    expect(screen.getByText("估算中…")).toBeInTheDocument();
  });

  it("falls back gracefully when the estimate is unavailable (404/error), still confirmable", () => {
    est.data = undefined;
    const onConfirm = vi.fn();
    renderDialog({ onConfirm });
    expect(screen.getByText("暂无法预估，按实际结算")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确定" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("确定 → onConfirm, 取消 → onCancel", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    renderDialog({ onConfirm, onCancel });
    fireEvent.click(screen.getByRole("button", { name: "确定" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("disables both buttons while submitting (double-click guard)", () => {
    est.data = { estimated_credits: 5, unit: "credits" };
    renderDialog({ submitting: true });
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /提交中/ })).toBeDisabled();
  });

  it("renders nothing when closed and clears the estimate", () => {
    renderDialog({ open: false, request: null });
    expect(screen.queryByText("确定生成")).not.toBeInTheDocument();
    expect(est.mutate).not.toHaveBeenCalled();
    return waitFor(() => expect(est.reset).toHaveBeenCalled());
  });
});
