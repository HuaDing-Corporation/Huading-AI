import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const libraryMock = vi.hoisted(() => ({ data: [] as unknown[], isLoading: false, isError: false, refetch: vi.fn() }));
vi.mock("@/lib/api/hooks", () => ({
  useUploadAudio: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: uploadMock.isPending }),
  useBgmLibrary: () => libraryMock
}));
// 波形播放器占位为标记（其 seek/play 等有专测；此处只验 bgm-picker 三态编排），保留 ariaLabel 供断言。
vi.mock("@/components/workbench/waveform-player", () => ({
  WaveformPlayer: ({ ariaLabel }: { ariaLabel: string }) => <div data-testid="waveform" aria-label={ariaLabel} />
}));

import { BgmPicker } from "./bgm-picker";

const TRACKS = [
  { track_id: "bgm-uplift", name: "轻快上扬", duration_sec: 30, preview_url: "https://mock.local/u.mp3", license: "CC0" },
  { track_id: "bgm-calm", name: "舒缓氛围", duration_sec: 45, preview_url: "https://mock.local/c.mp3", license: "CC0" }
];

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  uploadMock.isPending = false;
  uploadMock.mutateAsync.mockResolvedValue({ asset_id: "audio-1" });
  libraryMock.data = TRACKS;
  libraryMock.isLoading = false;
  libraryMock.isError = false;
});
afterEach(() => vi.clearAllMocks());

describe("BgmPicker (BGM 三态)", () => {
  it("默认「无」→ onChange(undefined)", () => {
    const onChange = vi.fn();
    render(<BgmPicker onChange={onChange} />);
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  it("配乐库：渲染曲目 + 波形试听播放器，选用 → onChange({source:library, track_id})", async () => {
    const onChange = vi.fn();
    render(<BgmPicker onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.vgBgmLibrary }));

    expect(screen.getByText("轻快上扬")).toBeInTheDocument();
    // 波形播放器（占位标记，带 ariaLabel=试听 曲名）。
    expect(screen.getByLabelText(copy.workbench.vgBgmPreviewLabel("轻快上扬"))).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: copy.workbench.vgBgmSelect })[0]);
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith({ source: "library", track_id: "bgm-uplift" }));
    expect(screen.getByRole("button", { name: copy.workbench.vgBgmSelected })).toBeInTheDocument();
  });

  it("上传：上传音频 → onChange({source:upload, asset_id})", async () => {
    const onChange = vi.fn();
    render(<BgmPicker onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.vgBgmUpload }));
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "m.mp3", { type: "audio/mpeg" })] } });

    await waitFor(() => expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith({ source: "upload", asset_id: "audio-1" }));
    expect(screen.getByText(copy.workbench.vgBgmUploaded)).toBeInTheDocument();
  });

  it("已选库曲后切到「无」→ onChange(undefined)（无模式不带 BGM）", async () => {
    const onChange = vi.fn();
    render(<BgmPicker onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.vgBgmLibrary }));
    fireEvent.click(screen.getAllByRole("button", { name: copy.workbench.vgBgmSelect })[0]);
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith({ source: "library", track_id: "bgm-uplift" }));

    fireEvent.click(screen.getByRole("button", { name: copy.workbench.vgBgmNone }));
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(undefined));
  });

  it("库内换选另一曲 → onChange 更新为新 track_id", async () => {
    const onChange = vi.fn();
    render(<BgmPicker onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.vgBgmLibrary }));
    const selectBtns = screen.getAllByRole("button", { name: copy.workbench.vgBgmSelect });
    fireEvent.click(selectBtns[0]);
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith({ source: "library", track_id: "bgm-uplift" }));
    // 第二曲此时仍是「选用」按钮。
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.vgBgmSelect }));
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith({ source: "library", track_id: "bgm-calm" }));
  });

  it("先库选 → 切到上传(未传) → onChange(undefined)（互斥，不串台）", async () => {
    const onChange = vi.fn();
    render(<BgmPicker onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.vgBgmLibrary }));
    fireEvent.click(screen.getAllByRole("button", { name: copy.workbench.vgBgmSelect })[0]);
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith({ source: "library", track_id: "bgm-uplift" }));

    fireEvent.click(screen.getByRole("button", { name: copy.workbench.vgBgmUpload }));
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(undefined));
  });

  it("上传失败：catch 友好提示，onChange 不发上传 BGM", async () => {
    const onChange = vi.fn();
    uploadMock.mutateAsync.mockRejectedValue(new Error("boom"));
    render(<BgmPicker onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.vgBgmUpload }));
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "m.mp3", { type: "audio/mpeg" })] } });
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(onChange).not.toHaveBeenCalledWith(expect.objectContaining({ source: "upload" }));
  });

  it("配乐库加载失败 → 错误 + 重试", () => {
    libraryMock.data = [];
    libraryMock.isError = true;
    render(<BgmPicker onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.vgBgmLibrary }));
    expect(screen.getByText(copy.workbench.vgBgmLibraryError)).toBeInTheDocument();
    fireEvent.click(screen.getByText(copy.cover.retry));
    expect(libraryMock.refetch).toHaveBeenCalled();
  });

  it("非音频文件 → 友好提示，不上传", () => {
    render(<BgmPicker onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.vgBgmUpload }));
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "a.png", { type: "image/png" })] } });
    expect(screen.getByText(copy.errors.uploadType)).toBeInTheDocument();
    expect(uploadMock.mutateAsync).not.toHaveBeenCalled();
  });
});
