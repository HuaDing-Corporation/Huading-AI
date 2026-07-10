import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const hooks = vi.hoisted(() => ({ useHistoryImages: vi.fn(), useHistoryImageSet: vi.fn() }));
vi.mock("@/lib/api/hooks", () => hooks);

import { HistoryGrid } from "./history-grid";

beforeEach(() => {
  hooks.useHistoryImageSet.mockReturnValue({ data: undefined, isLoading: false, isError: false, refetch: vi.fn() });
});
afterEach(() => vi.clearAllMocks());

describe("HistoryGrid (加载/错误/重试)", () => {
  it("加载中 → 显示加载态", () => {
    hooks.useHistoryImages.mockReturnValue({ isLoading: true, isError: false, data: undefined });
    render(<HistoryGrid category="image_gen" />);
    expect(screen.getByText(copy.historyImages.loading)).toBeInTheDocument();
  });

  it("错误 → 友好中文 + 重试触发 refetch（不泄裸串）", () => {
    const refetch = vi.fn();
    hooks.useHistoryImages.mockReturnValue({ isLoading: false, isError: true, error: new Error("boom raw"), data: undefined, refetch });
    render(<HistoryGrid category="image_gen" />);
    expect(screen.queryByText(/boom raw/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.historyImages.retry }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
