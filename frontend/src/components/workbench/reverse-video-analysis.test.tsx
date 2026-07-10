import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ReverseVideoAnalysis, ReverseVideoPacing } from "@/lib/api/reverse-prompt";
import { copy } from "@/lib/copy";
import { ReverseVideoAnalysisView } from "./reverse-video-analysis";

// VIDEO-REVERSE-PROMPT-UI-0001-FIX2 · pacing 枚举→中文映射（不显裸英文）+ 分镜序号（index）渲染。
const ANALYSIS: ReverseVideoAnalysis = {
  duration_sec: 18,
  pacing: "fast",
  shot_list: [
    { index: 0, start_sec: 0, end_sec: 4, visual: "产品特写镜头", camera: "推近", motion: "蒸汽升", transition: "叠化" }
  ],
  audio_transcript: null,
  bgm_style: null
};

describe("ReverseVideoAnalysisView · pacing 中文映射（FIX2）", () => {
  it("pacing=fast → 显示「快」，不显裸英文 fast", () => {
    render(<ReverseVideoAnalysisView analysis={ANALYSIS} />);
    expect(screen.getByText("快")).toBeInTheDocument();
    expect(screen.queryByText("fast")).not.toBeInTheDocument();
  });

  it("四档枚举各显对应中文（slow→慢 / medium→中 / fast→快 / variable→可变），均不显裸英文", () => {
    const cases: [ReverseVideoPacing, string][] = [
      ["slow", "慢"],
      ["medium", "中"],
      ["fast", "快"],
      ["variable", "可变"]
    ];
    for (const [enumVal, zh] of cases) {
      const { unmount } = render(<ReverseVideoAnalysisView analysis={{ ...ANALYSIS, pacing: enumVal }} />);
      expect(screen.getByText(zh)).toBeInTheDocument();
      expect(screen.queryByText(enumVal)).not.toBeInTheDocument();
      unmount();
    }
  });

  it("分镜渲染：visual + 序号（vaShot(index+1)）", () => {
    render(<ReverseVideoAnalysisView analysis={ANALYSIS} />);
    expect(screen.getByText("产品特写镜头")).toBeInTheDocument();
    expect(screen.getByText(copy.reverse.vaShot(1))).toBeInTheDocument(); // index 0 → 「镜头 1」
  });
});
