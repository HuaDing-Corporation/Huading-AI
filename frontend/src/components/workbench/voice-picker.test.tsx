import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { BrandVoice, Voice } from "@/lib/api/types";
import { VoicePicker } from "./voice-picker";

const voice = (id: string, name: string, source: Voice["source"] = "preset"): Voice => ({
  id,
  provider: "edge_tts",
  voice_code: id,
  display_name: name,
  gender: null,
  language: "zh-CN",
  sample_url: null,
  source
});
const brand = (id: string, name: string, delivery_status: BrandVoice["delivery_status"] = "active", provider = "cosyvoice-voice-clone"): BrandVoice => ({
  id,
  name,
  provider,
  status: delivery_status === "rejected" ? "failed" : "ready",
  order_status: delivery_status === "rejected" ? "rejected" : delivery_status === "awaiting_fulfillment" ? "awaiting_fulfillment" : "fulfilled",
  delivery_status,
  expires_at: delivery_status === "expired" ? "2026-08-29T00:00:00Z" : null,
  created_at: "2026-08-01T00:00:00Z"
});

describe("VoicePicker payer isolation", () => {
  it("preserves the legacy source grouping when no rights-aware list is supplied", () => {
    const onChange = vi.fn();
    render(<VoicePicker voices={[voice("preset", "系统音"), voice("legacy", "旧品牌音", "brand_voice")]} value="preset" onChange={onChange} />);
    fireEvent.click(screen.getByText("旧品牌音"));
    expect(onChange).toHaveBeenCalledWith("legacy");
  });

  it("renders only backend-authorized active brand voice records", () => {
    render(
      <VoicePicker
        voices={[voice("preset", "系统音"), voice("duplicate", "重复音", "brand_voice")]}
        brandVoices={[
          brand("paid", "我的已交付音", "active", "doubao-voice-clone"),
          brand("expired", "我的过期音", "expired", "doubao-voice-clone"),
          brand("waiting", "等待音", "awaiting_fulfillment", "doubao-voice-clone"),
          brand("rejected", "拒绝音", "rejected")
        ]}
        value="preset"
        onChange={() => undefined}
      />
    );
    expect(screen.getByText("我的已交付音")).toBeVisible();
    expect(screen.queryByText("我的过期音")).not.toBeInTheDocument();
    expect(screen.queryByText("等待音")).not.toBeInTheDocument();
    expect(screen.queryByText("拒绝音")).not.toBeInTheDocument();
    expect(screen.queryByText("重复音")).not.toBeInTheDocument();
  });

  it("does not expose expired non-canonical or non-payer records through the picker", () => {
    render(
      <VoicePicker
        voices={[voice("preset", "系统音")]}
        brandVoices={[
          brand("canonical-expired", "规范豆包过期音", "expired", "doubao-voice-clone"),
          brand("cosy-expired", "Cosy 过期音", "expired", "cosyvoice-voice-clone"),
          { ...brand("historical", "历史供应商音", "expired", "doubao"), order_status: null }
        ]}
        value="preset"
        onChange={() => undefined}
      />
    );
    expect(screen.queryByText("规范豆包过期音")).not.toBeInTheDocument();
    expect(screen.queryByText("Cosy 过期音")).not.toBeInTheDocument();
    expect(screen.queryByText("历史供应商音")).not.toBeInTheDocument();
  });

  it("does not re-lock a payer-authorized Doubao voice from client subscription inference", () => {
    const onChange = vi.fn();
    render(<VoicePicker voices={[voice("preset", "系统音")]} brandVoices={[brand("paid", "我的豆包音", "active", "doubao-voice-clone")]} canUseVip={false} value="preset" onChange={onChange} />);
    fireEvent.click(screen.getByText("我的豆包音"));
    expect(onChange).toHaveBeenCalledWith("paid");
  });

  it("shows provider labels from canonical values", () => {
    render(<VoicePicker voices={[voice("preset", "系统音")]} brandVoices={[brand("d", "豆包音", "active", "doubao-voice-clone"), brand("c", "Cosy 音", "active", "cosyvoice-voice-clone")]} value="preset" onChange={() => undefined} />);
    expect(screen.getByText("豆包")).toBeVisible();
    expect(screen.getByText("CosyVoice")).toBeVisible();
  });

  it("shows a creation link when the authorized active list is empty", () => {
    render(<VoicePicker voices={[voice("preset", "系统音")]} brandVoices={[brand("expired", "过期音", "expired")]} value="preset" onChange={() => undefined} />);
    expect(screen.getByRole("link", { name: "去创建" })).toHaveAttribute("href", "/brand-voices");
  });
});
