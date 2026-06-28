import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const listMock = vi.hoisted(() => ({ data: [] as Array<Record<string, unknown>>, isLoading: false, isError: false, refetch: vi.fn() }));
const deleteMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));

vi.mock("@/lib/api/hooks", () => ({
  useBrandVoices: () => ({ data: listMock.data, isLoading: listMock.isLoading, isError: listMock.isError, refetch: listMock.refetch }),
  useDeleteBrandVoice: () => ({ mutateAsync: deleteMock.mutateAsync, isPending: deleteMock.isPending })
}));

import { BrandVoiceList } from "./brand-voice-list";

const bv = (id: string, name: string, status: string, extra: Record<string, unknown> = {}) => ({
  id, name, status, created_at: "", ...extra
});

beforeEach(() => {
  listMock.isLoading = false;
  listMock.isError = false;
  deleteMock.isPending = false;
  deleteMock.mutateAsync.mockResolvedValue({ deleted: true });
  listMock.data = [];
});
afterEach(() => vi.clearAllMocks());

describe("BrandVoiceList (品牌音色列表)", () => {
  it("三种 status 各出对应徽章 + 处理中提示（§8：失败仅徽章无 message、不试听）", () => {
    listMock.data = [bv("a", "处理音", "processing"), bv("b", "可用音", "ready"), bv("c", "失败音", "failed")];
    render(<BrandVoiceList />);
    expect(screen.getByText(copy.brandVoice.statusProcessing)).toBeInTheDocument();
    expect(screen.getByText(copy.brandVoice.statusReady)).toBeInTheDocument();
    expect(screen.getByText(copy.brandVoice.statusFailed)).toBeInTheDocument();
    expect(screen.getByText(copy.brandVoice.processingHint)).toBeInTheDocument();
    // §8：列表不试听克隆音色（无任何「试听」按钮，仅删除）
    expect(screen.queryByRole("button", { name: /试听/ })).not.toBeInTheDocument();
  });

  it("空态：提示去创建", () => {
    listMock.data = [];
    render(<BrandVoiceList />);
    expect(screen.getByText(copy.brandVoice.listEmpty)).toBeInTheDocument();
  });

  it("删除：点删除→确认弹窗→确认调用 deleteBrandVoice(id)", async () => {
    listMock.data = [bv("a", "可用音", "ready")];
    render(<BrandVoiceList />);

    fireEvent.click(screen.getByRole("button", { name: `${copy.brandVoice.delete} 可用音` }));
    // 确认弹窗出现
    expect(screen.getByText(copy.brandVoice.deleteConfirmTitle)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.brandVoice.deleteConfirmBtn }));

    await waitFor(() => expect(deleteMock.mutateAsync).toHaveBeenCalledWith("a"));
  });

  it("加载失败：出错误态 + 重试", () => {
    listMock.isError = true;
    render(<BrandVoiceList />);
    expect(screen.getByText(copy.brandVoice.listError)).toBeInTheDocument();
  });
});
