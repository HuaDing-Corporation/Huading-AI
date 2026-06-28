import { apiFetch } from "@/lib/api/client";
import type { LabelSettings, LabelSettingsUpdate } from "@/lib/api/types";

/**
 * 深度合成标识设置 (LABEL-UI-0001) 数据层。沿用 apiFetch(鉴权/封套/ApiError)。
 * enabled 只读恒真：update 只发 position/text，不发 enabled → UI 结构上无法关闭显式标识(合规)。
 * 契约据 seam §3/§5 推断，待对冻结 seam + 真栈校验。
 */

/** 读租户标识设置（enabled 恒 true）。 */
export function getLabelSettings(): Promise<LabelSettings> {
  return apiFetch<LabelSettings>("/api/v1/tenant/label-settings", { method: "GET" });
}

/** 更新位置/文案（enabled 不可改，由后端强制恒真）。 */
export function updateLabelSettings(body: LabelSettingsUpdate): Promise<LabelSettings> {
  return apiFetch<LabelSettings>("/api/v1/tenant/label-settings", { method: "PUT", body });
}
