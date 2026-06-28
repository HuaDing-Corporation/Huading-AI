import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import { ApiError } from "@/lib/api/client";

const settingsMock = vi.hoisted(() => ({ data: null as Record<string, unknown> | null, isLoading: false, isError: false, refetch: vi.fn() }));
const updateMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));

vi.mock("@/lib/api/hooks", () => ({
  useLabelSettings: () => ({ data: settingsMock.data, isLoading: settingsMock.isLoading, isError: settingsMock.isError, refetch: settingsMock.refetch }),
  useUpdateLabelSettings: () => ({ mutateAsync: updateMock.mutateAsync, isPending: updateMock.isPending })
}));

import { LabelSettings } from "./label-settings";

beforeEach(() => {
  settingsMock.data = { position: "br", text: "AI 生成", enabled: true };
  settingsMock.isLoading = false;
  settingsMock.isError = false;
  updateMock.isPending = false;
  updateMock.mutateAsync.mockResolvedValue({ position: "br", text: "AI 生成", enabled: true });
});
afterEach(() => vi.clearAllMocks());

describe("LabelSettings (深度合成标识设置)", () => {
  // 承重：UI 不提供任何关闭显式标识的路径（无开关/复选框），仅锁定明示。
  it("承重·不可关闭：无 enabled 开关/复选框，仅显「不可关闭」明示", () => {
    render(<LabelSettings />);
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.getByText(copy.label.lockedTitle)).toBeInTheDocument();
    expect(screen.getByText(copy.label.lockedHint)).toBeInTheDocument();
  });

  // 承重：保存请求体只含 position/text，绝不含 enabled（结构上无法置 false）。
  it("承重·保存体不含 enabled：仅发 {position, text}", async () => {
    render(<LabelSettings />);
    fireEvent.click(screen.getByRole("button", { name: copy.label.posTl }));
    fireEvent.change(screen.getByLabelText(copy.label.textLabel), { target: { value: "AI 合成" } });
    fireEvent.click(screen.getByRole("button", { name: copy.label.save }));

    await waitFor(() => expect(updateMock.mutateAsync).toHaveBeenCalled());
    const body = updateMock.mutateAsync.mock.calls[0][0];
    expect(body).toEqual({ position: "tl", text: "AI 合成" });
    expect(body).not.toHaveProperty("enabled");
  });

  it("文案为空：保存禁用，不发请求", () => {
    settingsMock.data = { position: "br", text: "", enabled: true };
    render(<LabelSettings />);
    expect(screen.getByRole("button", { name: copy.label.save })).toBeDisabled();
  });

  it("文案 ≤20：超长输入截断到 20", () => {
    render(<LabelSettings />);
    fireEvent.change(screen.getByLabelText(copy.label.textLabel), { target: { value: "标".repeat(40) } });
    expect((screen.getByLabelText(copy.label.textLabel) as HTMLInputElement).value).toHaveLength(20);
    expect(screen.getByText("20/20")).toBeInTheDocument();
  });

  it("实时预览：渲染当前文案水印", () => {
    settingsMock.data = { position: "tr", text: "AI 生成", enabled: true };
    render(<LabelSettings />);
    // 预览框 + 文案水印（含示意文字 + 水印文案）
    expect(screen.getByText(copy.label.previewLabel)).toBeInTheDocument();
    expect(screen.getAllByText("AI 生成").length).toBeGreaterThan(0);
  });

  it("预览水印按 position 定位(POS_CLASS 映射)——切换位置改定位类", () => {
    settingsMock.data = { position: "tr", text: "AI 生成", enabled: true };
    render(<LabelSettings />);
    const mark = () => screen.getAllByText("AI 生成").find((el) => el.className.includes("absolute"));
    expect(mark()?.className).toContain("top-2");
    expect(mark()?.className).toContain("right-2");
    fireEvent.click(screen.getByRole("button", { name: copy.label.posBl }));
    expect(mark()?.className).toContain("bottom-2");
    expect(mark()?.className).toContain("left-2");
  });

  it("保存失败：mutateAsync 拒绝 → role=alert 显示 errorText 文案", async () => {
    updateMock.mutateAsync.mockRejectedValue(new ApiError("标识文案需为 1–20 个非空字符", "LABEL_TEXT_INVALID", 422));
    render(<LabelSettings />);
    fireEvent.click(screen.getByRole("button", { name: copy.label.save }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("标识文案"));
  });

  it("加载中：显示 loading", () => {
    settingsMock.isLoading = true;
    settingsMock.data = null;
    render(<LabelSettings />);
    expect(screen.getByText(copy.history.loading)).toBeInTheDocument();
  });

  it("加载失败：显示错误 + 重试触发 refetch", () => {
    settingsMock.isError = true;
    settingsMock.data = null;
    render(<LabelSettings />);
    expect(screen.getByText(copy.history.error)).toBeInTheDocument();
    fireEvent.click(screen.getByText(copy.history.retry));
    expect(settingsMock.refetch).toHaveBeenCalled();
  });
});
