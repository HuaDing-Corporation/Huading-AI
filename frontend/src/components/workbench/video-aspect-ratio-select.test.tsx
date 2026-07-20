import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import { DEFAULT_VIDEO_ASPECT_RATIO, VIDEO_ASPECT_RATIOS, VideoAspectRatioSelect } from "./video-aspect-ratio-select";

afterEach(() => vi.clearAllMocks());

// VIDEO-GEN-PARAMS-UI-0001 需求3：视频画面比例 7 值（含 adaptive），默认 adaptive；
// hint 语义分流——adaptive→自适应说明；显式比例→裁切/重构警告（与图片侧「hint 只在 auto」相反，故独立组件）。
describe("VideoAspectRatioSelect (视频画面比例)", () => {
  it("默认 adaptive：值域为 7 值、默认常量 = adaptive", () => {
    expect(DEFAULT_VIDEO_ASPECT_RATIO).toBe("auto");
    expect([...VIDEO_ASPECT_RATIOS]).toEqual(["auto", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]);
  });

  it("选 adaptive → 显示自适应说明（不显裁切警告）", () => {
    render(<VideoAspectRatioSelect value="auto" onValueChange={vi.fn()} />);
    expect(screen.getByText(copy.workbench.vgAspectAdaptiveHint)).toBeInTheDocument();
    expect(screen.queryByText(copy.workbench.vgAspectCropHint)).not.toBeInTheDocument();
  });

  it("选显式比例(16:9) → 显示裁切/重构警告（不显自适应说明）", () => {
    render(<VideoAspectRatioSelect value="16:9" onValueChange={vi.fn()} />);
    expect(screen.getByText(copy.workbench.vgAspectCropHint)).toBeInTheDocument();
    expect(screen.queryByText(copy.workbench.vgAspectAdaptiveHint)).not.toBeInTheDocument();
  });

  it("下拉列出 7 个选项、选择回调透传值", async () => {
    const onValueChange = vi.fn();
    render(<VideoAspectRatioSelect value="auto" onValueChange={onValueChange} />);
    fireEvent.click(screen.getByRole("combobox", { name: new RegExp(copy.workbench.vgAspectLabel) }));
    // 7 个 option 都在（adaptive 显示为「自适应」）。
    expect(await screen.findByRole("option", { name: copy.workbench.vgAspectAdaptive })).toBeInTheDocument();
    for (const r of ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]) {
      expect(screen.getByRole("option", { name: r })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("option", { name: "21:9" }));
    expect(onValueChange).toHaveBeenCalledWith("21:9");
  });
});
