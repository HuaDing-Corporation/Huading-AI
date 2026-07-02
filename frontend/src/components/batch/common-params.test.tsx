import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CommonParams } from "./common-params";

// BgmPicker 依赖 useBgmLibrary(query/auth)，本测聚焦 时长/分辨率/AI 标识 端到端 → 置空。
vi.mock("@/components/workbench/bgm-picker", () => ({ BgmPicker: () => null }));

afterEach(() => window.localStorage.clear());

describe("CommonParams (批量公共参数，端到端)", () => {
  it("ecom_table 默认：video_mode=seedance_i2v, duration 30, resolution 720p, apply_visible_label false", () => {
    const onChange = vi.fn();
    render(<CommonParams kind="ecom_table" onChange={onChange} />);
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ video_mode: "seedance_i2v", duration_sec: 30, resolution: "720p", apply_visible_label: false })
    );
  });

  it("prompt_set 默认：video_mode=video_gen, duration 5", () => {
    const onChange = vi.fn();
    render(<CommonParams kind="prompt_set" onChange={onChange} />);
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ video_mode: "video_gen", duration_sec: 5 }));
  });

  it("开启 AI 标识开关 → onChange 带 apply_visible_label:true（端到端承重：AiLabelToggle→hook→common）", () => {
    const onChange = vi.fn();
    render(<CommonParams kind="prompt_set" onChange={onChange} />);
    fireEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ apply_visible_label: true }));
  });

  it("选 1080P → onChange resolution:1080p", () => {
    const onChange = vi.fn();
    render(<CommonParams kind="prompt_set" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "1080P" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ resolution: "1080p" }));
  });
});
