import { apiFetch } from "@/lib/api/client";
import type { SubtitleTemplate, SubtitleTemplatesResponse } from "@/lib/api/types";

/** 字幕预设模板（5 套静态）— 渲染选择器 + CSS 近似预览用。解包 {templates}。 */
export async function listSubtitleTemplates(): Promise<SubtitleTemplate[]> {
  const res = await apiFetch<SubtitleTemplatesResponse>("/api/v1/oral/subtitle-templates", { method: "GET" });
  return res?.templates ?? [];
}
