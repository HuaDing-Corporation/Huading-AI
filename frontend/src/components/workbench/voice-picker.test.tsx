import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { Voice } from "@/lib/api/types";
import { VoicePicker } from "./voice-picker";

const voice = (id: string, display_name: string, extra: Partial<Voice> = {}): Voice => ({
  id, provider: "edge_tts", voice_code: id, display_name, gender: null, language: "zh-CN", sample_url: null, ...extra
});

describe("VoicePicker (口播音色 · 品牌音色分组)", () => {
  it("无品牌音色：扁平列表，不出分组标题（不破现有）", () => {
    render(<VoicePicker voices={[voice("v1", "知性女声"), voice("v2", "磁性男声")]} value="v1" onChange={() => {}} />);
    expect(screen.getByText("知性女声")).toBeInTheDocument();
    expect(screen.getByText("磁性男声")).toBeInTheDocument();
    expect(screen.queryByText(copy.brandVoice.pickerBrandGroup)).not.toBeInTheDocument();
    expect(screen.queryByText(copy.brandVoice.pickerStandardGroup)).not.toBeInTheDocument();
  });

  // 承重②：按 voice.source==="brand_voice" 归「我的品牌音色」、preset 归系统组。
  // 把判定改回 is_brand_voice 则品牌音色落不进品牌组 → 红。
  it("含品牌音色：按 source 出「我的品牌音色 / 系统音色」分组，品牌音色可见可选", () => {
    const onChange = vi.fn();
    render(
      <VoicePicker
        voices={[
          voice("v1", "知性女声", { source: "preset" }),
          voice("c1", "我的主播音", { source: "brand_voice", provider: "clone" })
        ]}
        value="v1"
        onChange={onChange}
      />
    );
    const brandHeader = screen.getByText(copy.brandVoice.pickerBrandGroup);
    expect(brandHeader).toBeInTheDocument();
    expect(screen.getByText(copy.brandVoice.pickerStandardGroup)).toBeInTheDocument();

    // 品牌音色确实归入「我的品牌音色」组(role=group aria-labelledby 关联)，而非系统组
    const brandGroup = document.querySelector('[role="group"][aria-labelledby="voice-group-brand"]');
    expect(brandGroup?.textContent).toContain("我的主播音");
    expect(brandGroup?.textContent).not.toContain("知性女声");

    fireEvent.click(screen.getByText("我的主播音"));
    expect(onChange).toHaveBeenCalledWith("c1");
  });
});
