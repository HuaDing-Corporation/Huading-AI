import { describe, expect, it } from "vitest";

import { apiFetch, ApiError } from "./client";
import type { LabelSettings } from "@/lib/api/types";
import { getLabelSettings, updateLabelSettings } from "./label-settings";

// 集成测试：真 apiFetch → MSW。验证 mock 忠实(§5)：enabled 只读恒真、PUT 只收 position/text、
// 空文案 422。吸取声音克隆/0006 mock 掩盖契约教训。

describe("label-settings API ↔ MSW（mock 忠实，§5）", () => {
  it("GET：enabled 恒 true + position/text 在位", async () => {
    const s = await getLabelSettings();
    expect(s.enabled).toBe(true);
    expect(["br", "bl", "tr", "tl", "bc"]).toContain(s.position);
    expect(s.text).toBeTruthy();
  });

  it("PUT {position,text}：更新位置/文案，enabled 仍恒 true（合规不可关）", async () => {
    const s = await updateLabelSettings({ position: "tl", text: "AI 合成" });
    expect(s.position).toBe("tl");
    expect(s.text).toBe("AI 合成");
    expect(s.enabled).toBe(true);
  });

  it("PUT 空文案 → 422（mock 忠实校验，非伪造放行）", async () => {
    let caught: unknown;
    try {
      await updateLabelSettings({ position: "br", text: "" });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(422);
  });

  it("PUT 文案超长(>20) → 422", async () => {
    let caught: unknown;
    try {
      await updateLabelSettings({ position: "br", text: "x".repeat(21) });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(422);
  });

  // 合规承重(契约层)：客户端即便发 enabled:false，后端/mock 仍强制 enabled:true（不可关闭）。
  it("客户端发 enabled:false：响应 enabled 仍恒 true", async () => {
    const s = await apiFetch<LabelSettings>("/api/v1/tenant/label-settings", {
      method: "PUT",
      body: { position: "br", text: "AI 生成", enabled: false }
    });
    expect(s.enabled).toBe(true);
  });

  // 承重(FIX1)：position 必填，对齐后端 Literal——缺失/空/非法均 422，mock 不放宽(不掩盖前端漏发 position)。
  it("PUT 漏 position → 422（mock 不放宽，对齐后端必填 Literal）", async () => {
    let caught: unknown;
    try {
      await apiFetch<LabelSettings>("/api/v1/tenant/label-settings", { method: "PUT", body: { text: "x" } });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(422);
  });

  it("PUT 非法 position(center) → 422", async () => {
    let caught: unknown;
    try {
      await apiFetch<LabelSettings>("/api/v1/tenant/label-settings", { method: "PUT", body: { position: "center", text: "x" } });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(422);
  });
});
