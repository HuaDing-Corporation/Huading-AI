import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { BrandVoice, Voice } from "@/lib/api/types";
import { VoicePicker } from "./voice-picker";

const voice = (id: string, display_name: string, extra: Partial<Voice> = {}): Voice => ({
  id, provider: "edge_tts", voice_code: id, display_name, gender: null, language: "zh-CN", sample_url: null, source: "preset", ...extra
});
const bv = (id: string, name: string, extra: Partial<BrandVoice> = {}): BrandVoice => ({
  id, name, status: "ready", created_at: "1970-01-01T00:00:00Z", ...extra
});

describe("VoicePicker (口播音色 · 选我的音色)", () => {
  it("非回归：不传 brandVoices → 扁平预设列表，不出分组标题，可选", () => {
    const onChange = vi.fn();
    render(<VoicePicker voices={[voice("v1", "知性女声"), voice("v2", "磁性男声")]} value="v1" onChange={onChange} />);
    expect(screen.getByText("知性女声")).toBeInTheDocument();
    expect(screen.getByText("磁性男声")).toBeInTheDocument();
    expect(screen.queryByText(copy.brandVoice.pickerBrandGroup)).not.toBeInTheDocument();
    expect(screen.queryByText(copy.brandVoice.pickerStandardGroup)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("磁性男声"));
    expect(onChange).toHaveBeenCalledWith("v2");
  });

  // 承重·legacy 非回归（Review P1）：批量电商 common-params 不传 brandVoices，但 /voices 会注入 ready 克隆
  // (source="brand_voice")。legacy 路径必须仍按 source 分组显示这些克隆可选——否则批量选克隆能力静默丢失。
  it("legacy 非回归：不传 brandVoices 但 voices 含 source=brand_voice 克隆 → 仍分组显示可选(不丢批量选克隆)", () => {
    const onChange = vi.fn();
    render(
      <VoicePicker
        voices={[voice("v1", "知性女声", { source: "preset" }), voice("c1", "我的主播音", { source: "brand_voice", provider: "clone" })]}
        value="v1"
        onChange={onChange}
      />
    );
    expect(screen.getByText(copy.brandVoice.pickerBrandGroup)).toBeInTheDocument();
    const brandGroup = document.querySelector('[role="group"][aria-labelledby="voice-group-brand"]');
    expect(brandGroup?.textContent).toContain("我的主播音");
    fireEvent.click(screen.getByText("我的主播音"));
    expect(onChange).toHaveBeenCalledWith("c1");
  });

  it("传 brandVoices：出「我的品牌音色 / 系统音色」分组；ready 品牌音色可见可选(用品牌 id)", () => {
    const onChange = vi.fn();
    render(
      <VoicePicker
        voices={[voice("v1", "知性女声")]}
        brandVoices={[bv("c1", "我的主播音", { status: "ready" })]}
        value="v1"
        onChange={onChange}
      />
    );
    expect(screen.getByText(copy.brandVoice.pickerBrandGroup)).toBeInTheDocument();
    expect(screen.getByText(copy.brandVoice.pickerStandardGroup)).toBeInTheDocument();
    const brandGroup = document.querySelector('[role="group"][aria-labelledby="voice-group-brand"]');
    expect(brandGroup?.textContent).toContain("我的主播音");
    expect(brandGroup?.textContent).not.toContain("知性女声");
    fireEvent.click(screen.getByText("我的主播音"));
    expect(onChange).toHaveBeenCalledWith("c1");
  });

  it("承重·不双渲染：voices 里混入 source=brand_voice 项(来自 /voices 注入)时，系统组不再渲染它", () => {
    render(
      <VoicePicker
        voices={[voice("v1", "知性女声"), voice("c1", "我的主播音", { source: "brand_voice", provider: "clone" })]}
        brandVoices={[bv("c1", "我的主播音", { status: "ready" })]}
        value="v1"
        onChange={() => {}}
      />
    );
    const standardGroup = document.querySelector('[role="group"][aria-labelledby="voice-group-standard"]');
    expect(standardGroup?.textContent).not.toContain("我的主播音");
    // 品牌组仅出现一次「我的主播音」
    expect(screen.getAllByText("我的主播音")).toHaveLength(1);
  });

  it("provider 徽标：doubao→豆包、cosyvoice→CosyVoice；**缺 provider 不显徽标也不报错**(兼容)", () => {
    render(
      <VoicePicker
        voices={[voice("v1", "知性女声")]}
        brandVoices={[
          bv("c1", "豆包音", { provider: "doubao" }),
          bv("c2", "免费音", { provider: "cosyvoice" }),
          bv("c3", "无标音") // 无 provider
        ]}
        value="v1"
        onChange={() => {}}
      />
    );
    expect(screen.getByText(copy.brandVoice.providerDoubao)).toBeInTheDocument();
    expect(screen.getByText(copy.brandVoice.providerCosyvoice)).toBeInTheDocument();
    // 无 provider 项照常渲染名字、无徽标、无崩溃
    expect(screen.getByText("无标音")).toBeInTheDocument();
  });

  it("processing 品牌音色 → 置灰不可选 + 「复刻中」，点击不触发 onChange", () => {
    const onChange = vi.fn();
    render(
      <VoicePicker
        voices={[voice("v1", "知性女声")]}
        brandVoices={[bv("c1", "复刻中的音", { status: "processing" })]}
        value="v1"
        onChange={onChange}
      />
    );
    expect(screen.getByText("复刻中的音")).toBeInTheDocument();
    expect(screen.getByText(copy.brandVoice.pickerCloning)).toBeInTheDocument();
    const btn = screen.getByText("复刻中的音").closest("button")!;
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("failed 品牌音色 → 不出现在选择器（既不可选也不显示）", () => {
    render(
      <VoicePicker
        voices={[voice("v1", "知性女声")]}
        brandVoices={[bv("c1", "就绪音", { status: "ready" }), bv("c2", "失败音", { status: "failed" })]}
        value="v1"
        onChange={() => {}}
      />
    );
    expect(screen.getByText("就绪音")).toBeInTheDocument();
    expect(screen.queryByText("失败音")).not.toBeInTheDocument();
  });

  it("空态：无可选/处理中品牌音色 → 引导「还没有品牌音色 · 去创建」跳 /brand-voices", () => {
    render(
      <VoicePicker
        voices={[voice("v1", "知性女声")]}
        brandVoices={[bv("c2", "失败音", { status: "failed" })]} // 仅失败(隐藏) → 视为空
        value="v1"
        onChange={() => {}}
      />
    );
    expect(screen.getByText(copy.brandVoice.pickerBrandEmpty)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: copy.brandVoice.pickerBrandCreate });
    expect(link).toHaveAttribute("href", "/brand-voices");
  });

  it("加载中：brandVoicesLoading → 显加载文案，不渲染品牌选项", () => {
    render(
      <VoicePicker
        voices={[voice("v1", "知性女声")]}
        brandVoices={[]}
        brandVoicesLoading
        value="v1"
        onChange={() => {}}
      />
    );
    expect(screen.getByText(copy.brandVoice.pickerBrandLoading)).toBeInTheDocument();
    expect(screen.queryByText(copy.brandVoice.pickerBrandEmpty)).not.toBeInTheDocument();
  });
});
