import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CommonParams } from "./common-params";
import { copy } from "@/lib/copy";
import type { Voice } from "@/lib/api/types";

const VOICES: Voice[] = [
  { id: "v1", provider: "edge_tts", voice_code: "x", display_name: "知性女声", gender: "female", language: "zh-CN", source: "preset" },
  { id: "v2", provider: "edge_tts", voice_code: "y", display_name: "磁性男声", gender: "male", language: "zh-CN", source: "preset" }
];
vi.mock("@/lib/api/hooks", () => ({ useVoices: () => ({ data: VOICES }) }));
// BgmPicker 依赖 useBgmLibrary(query/auth)，本测聚焦 时长/分辨率/音色/AI 标识 → 置空。
vi.mock("@/components/workbench/bgm-picker", () => ({ BgmPicker: () => null }));

afterEach(() => window.localStorage.clear());

describe("CommonParams (批量公共参数，端到端)", () => {
  it("ecom_table 默认：video_mode=seedance_i2v, duration 30, resolution 720p, apply_visible_label false, 默认音色 v1（承重·voice_id 非空）", () => {
    const onChange = vi.fn();
    render(<CommonParams kind="ecom_table" onChange={onChange} />);
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ video_mode: "seedance_i2v", duration_sec: 30, resolution: "720p", apply_visible_label: false, voice_id: "v1" })
    );
  });

  it("prompt_set：video_mode=video_gen, duration 5, **不含 voice_id**（video_gen 无配音）", () => {
    const onChange = vi.fn();
    render(<CommonParams kind="prompt_set" onChange={onChange} />);
    const last = onChange.mock.calls.at(-1)![0];
    expect(last).toMatchObject({ video_mode: "video_gen", duration_sec: 5 });
    expect(last.voice_id).toBeUndefined();
    // 真断言：VoicePicker 未渲染（其 legend=copy.workbench.voiceLabel「音色」）；radiogroup 恒 null 属假绿，弃用。
    expect(screen.queryByText(copy.workbench.voiceLabel)).not.toBeInTheDocument();
  });

  it("商品表切换音色 → onChange voice_id 随之", () => {
    const onChange = vi.fn();
    render(<CommonParams kind="ecom_table" onChange={onChange} />);
    fireEvent.click(screen.getByText("磁性男声"));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ voice_id: "v2" }));
  });

  it("开启 AI 标识开关 → onChange 带 apply_visible_label:true（端到端）", () => {
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
