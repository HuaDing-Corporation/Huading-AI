import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SubtitleStylePicker, isSubtitleStyleValid } from "./subtitle-style-picker";
import type { SubtitleTemplate } from "@/lib/api/types";

const TEMPLATES: SubtitleTemplate[] = [
  { id: "classic", name: "经典白", font_family: "Noto Sans SC", font_size: 48, color: "#FFFFFF", stroke_color: "#000000", stroke_width: 2, background: null, position: "bottom" },
  { id: "bold_yellow", name: "醒目黄", font_family: "Noto Sans SC", font_size: 56, color: "#FFE600", stroke_color: "#000000", stroke_width: 3, background: null, position: "bottom" }
];

describe("SubtitleStylePicker (字幕样式区)", () => {
  it("默认未选：跟随默认高亮，不显自定义/预览", () => {
    render(<SubtitleStylePicker templates={TEMPLATES} value={undefined} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "跟随默认" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "经典白" })).toBeInTheDocument();
    expect(screen.queryByLabelText("字号")).not.toBeInTheDocument();
  });

  it("选预设 → onChange({ template_id })", () => {
    const onChange = vi.fn();
    render(<SubtitleStylePicker templates={TEMPLATES} value={undefined} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "经典白" }));
    expect(onChange).toHaveBeenCalledWith({ template_id: "classic" });
  });

  it("选了预设：回显预设字号 + 改字号 patch font_size + 预览出现", () => {
    const onChange = vi.fn();
    render(<SubtitleStylePicker templates={TEMPLATES} value={{ template_id: "classic" }} onChange={onChange} />);
    const fontInput = screen.getByLabelText("字号");
    expect(fontInput).toHaveValue(48); // 继承预设
    fireEvent.change(fontInput, { target: { value: "60" } });
    expect(onChange).toHaveBeenCalledWith({ template_id: "classic", font_size: 60 });
    expect(screen.getByText("字幕预览示例文本")).toBeInTheDocument();
  });

  it("跟随默认 → onChange(undefined)（不传 subtitle_style）", () => {
    const onChange = vi.fn();
    render(<SubtitleStylePicker templates={TEMPLATES} value={{ template_id: "classic" }} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "跟随默认" }));
    expect(onChange).toHaveBeenCalledWith(undefined);
  });

  it("isSubtitleStyleValid：未选合法 / 越界字号非法", () => {
    expect(isSubtitleStyleValid(undefined)).toBe(true);
    expect(isSubtitleStyleValid({ template_id: "classic" })).toBe(true);
    expect(isSubtitleStyleValid({ template_id: "classic", font_size: 48 })).toBe(true);
    expect(isSubtitleStyleValid({ template_id: "classic", font_size: 8 })).toBe(false);
    expect(isSubtitleStyleValid({ template_id: "classic", font_size: 200 })).toBe(false);
  });
});
