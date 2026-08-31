import { ApiError, apiFetch, apiUrl, authHeaders } from "@/lib/api/client";
import { billingHeaders, parseBillingQuote, parseBillingSummary } from "@/lib/api/billing";
import { authStore } from "@/lib/auth/store";
import type { BillingConfirmation, BillingQuote, BrandVoice, ClearResult, CreateVideoRequest, DeleteResult, ScenePromptRequest, ScenePromptResponse, VideoAcceptedContract, VideoDetail, VideoEstimateContract, VideoEvent, VideoListItem, VideoListResponse, VideoPricingContext, Voice } from "@/lib/api/types";

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []): boolean {
  const allowed = new Set([...required, ...optional]);
  return required.every((key) => Object.hasOwn(value, key)) && Object.keys(value).every((key) => allowed.has(key));
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function canonicalBrandVoiceProvider(value: unknown): "cosyvoice" | "doubao" | null {
  if (value === "cosyvoice" || value === "cosyvoice-voice-clone") return "cosyvoice";
  if (value === "doubao" || value === "doubao-voice-clone") return "doubao";
  return null;
}

/** Resolve quote-validation context from the actual selected voice records. */
export function videoPricingContextForVoice(
  voiceId: string | undefined,
  voices: readonly Voice[],
  brandVoices: readonly BrandVoice[]
): VideoPricingContext | null {
  if (!voiceId) return { voice_kind: "none", voice_provider: null };
  const brandVoice = brandVoices.find((voice) => voice.id === voiceId);
  if (brandVoice) {
    const provider = canonicalBrandVoiceProvider(brandVoice.provider);
    return provider ? { voice_kind: "brand", voice_provider: provider } : null;
  }
  const voice = voices.find((candidate) => candidate.id === voiceId);
  if (!voice) return null;
  if (voice.source === "brand_voice") {
    const provider = canonicalBrandVoiceProvider(voice.provider);
    return provider ? { voice_kind: "brand", voice_provider: provider } : null;
  }
  return nonEmptyString(voice.provider)
    ? { voice_kind: "standard", voice_provider: voice.provider }
    : null;
}

function optionalNote(value: Record<string, unknown>): boolean {
  return !Object.hasOwn(value, "note") || value.note === null || typeof value.note === "string";
}

export function parseVideoEstimateContract(
  value: unknown,
  pricingContext?: VideoPricingContext | null
): VideoEstimateContract | null {
  const quote = parseBillingQuote(value, pricingContext);
  if (quote) {
    return quote.operation === "video_create" && quote.pricing_shape === "composite" ? quote : null;
  }
  if (!record(value)) return null;
  if (value.pricing_contract === "legacy_estimate") {
    return hasOnlyKeys(
      value,
      ["pricing_contract", "estimated_credits", "unit"],
      ["note"]
    ) &&
      Number.isSafeInteger(value.estimated_credits) &&
      (value.estimated_credits as number) >= 0 &&
      value.unit === "credits" &&
      optionalNote(value)
      ? (value as unknown as VideoEstimateContract)
      : null;
  }
  if (value.pricing_contract === "deferred_unpriced") {
    return hasOnlyKeys(
      value,
      ["pricing_contract", "estimated_credits", "unit", "unpriced"],
      ["note"]
    ) &&
      value.estimated_credits === 0 &&
      value.unit === "credits" &&
      value.unpriced === true &&
      optionalNote(value)
      ? (value as unknown as VideoEstimateContract)
      : null;
  }
  return null;
}

export function parseVideoAcceptedContract(value: unknown): VideoAcceptedContract | null {
  if (!record(value)) return null;
  const baseKeys = ["pricing_contract", "id", "status"] as const;
  const baseValid =
    nonEmptyString(value.id) &&
    nonEmptyString(value.status) &&
    (!Object.hasOwn(value, "task_id") ||
      (nonEmptyString(value.task_id) && value.task_id === value.id));
  if (!baseValid) return null;

  if (value.pricing_contract === "billing_quote") {
    if (!hasOnlyKeys(value, baseKeys, ["task_id", "billing"])) return null;
    const billing = parseBillingSummary(value.billing);
    return billing ? (value as unknown as VideoAcceptedContract) : null;
  }
  if (
    value.pricing_contract === "legacy_estimate" ||
    value.pricing_contract === "deferred_unpriced"
  ) {
    return hasOnlyKeys(value, baseKeys, ["task_id"])
      ? (value as unknown as VideoAcceptedContract)
      : null;
  }
  return null;
}

export async function createVideo(
  input: CreateVideoRequest,
  confirmation?: BillingConfirmation
): Promise<VideoAcceptedContract> {
  const value = await apiFetch<unknown>("/api/v1/videos", {
    method: "POST",
    body: input,
    ...(confirmation ? { headers: billingHeaders(confirmation) } : {})
  });
  const accepted = parseVideoAcceptedContract(value);
  const matchesConfirmation = confirmation
    ? accepted?.pricing_contract === "billing_quote" &&
      accepted.billing.idempotency_key.toLowerCase() === confirmation.idempotency_key.toLowerCase()
    : accepted?.pricing_contract !== "billing_quote";
  if (!accepted || !matchesConfirmation) {
    throw new ApiError(
      "视频创建响应的定价协议异常，请稍后查询任务状态。",
      "INVALID_VIDEO_ACCEPTED_CONTRACT",
      502
    );
  }
  return accepted;
}

/** Estimate the credits a request would consume — shown in the 确定生成 dialog. */
export async function estimateVideo(
  input: CreateVideoRequest,
  pricingContext?: VideoPricingContext | null
): Promise<VideoEstimateContract> {
  const value = await apiFetch<unknown>("/api/v1/videos/estimate", { method: "POST", body: input });
  const estimate = parseVideoEstimateContract(value, pricingContext);
  if (!estimate) {
    throw new ApiError(
      "视频报价协议异常，请刷新后重试。",
      "INVALID_VIDEO_PRICING_CONTRACT",
      502
    );
  }
  return estimate;
}

/** Generate a 画面提示词 (scene prompt) + 负面提示词 for 电商带货 i2v — decoupled from
 *  the 口播 script so editing one never changes the other. ECOM-VIDEO-OPTIMIZE-UI-0001
 *  契约 §4.2：从只发 topic → 发产品图 keys（≥1，luna 多模态读图）+ 文案 + topic；
 *  undefined 字段被 JSON.stringify 丢弃，故仅 product_image_keys 恒发。 */
export function estimateScenePrompt(params: ScenePromptRequest): Promise<BillingQuote> {
  return apiFetch<BillingQuote>("/api/v1/videos/scene-prompt/estimate", {
    method: "POST",
    body: params
  });
}

export function generateScenePrompt(
  params: ScenePromptRequest,
  confirmation: BillingConfirmation
): Promise<ScenePromptResponse> {
  return apiFetch<ScenePromptResponse>("/api/v1/videos/scene-prompt", {
    method: "POST",
    body: params,
    headers: billingHeaders(confirmation)
  });
}

/** Authoritative record for one video (status + playback/download URLs). */
export function getVideo(id: string): Promise<VideoDetail> {
  return apiFetch<VideoDetail>(`/api/v1/videos/${encodeURIComponent(id)}`, { method: "GET" });
}

/** This tenant's videos, newest first — used to hydrate the task list on mount. */
export async function listVideos(): Promise<VideoListItem[]> {
  const res = await apiFetch<VideoListResponse>("/api/v1/videos", { method: "GET" });
  return res?.items ?? [];
}

/** One page of videos, optionally filtered by mode — backs the 历史生成 tabs
 *  (returns total so the caller can paginate via offset). */
export function listVideosPage(
  params: { mode?: string; kind?: string; limit?: number; offset?: number } = {}
): Promise<VideoListResponse> {
  const query = new URLSearchParams();
  if (params.mode) query.set("mode", params.mode);
  if (params.kind) query.set("kind", params.kind); // 图片细分筛（kind=cover 仅封面，真后端）
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset != null) query.set("offset", String(params.offset));
  const qs = query.toString();
  return apiFetch<VideoListResponse>(`/api/v1/videos${qs ? `?${qs}` : ""}`, { method: "GET" });
}

/** 硬删单条视频/图片(+媒体 best-effort)；跨租户/不存在 → 404(HIST-UI-0001)。 */
export function deleteVideo(id: string): Promise<DeleteResult> {
  return apiFetch<DeleteResult>(`/api/v1/videos/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** 清空某模块全部视频/图片(mode 必填，硬删 + 媒体 best-effort)。 */
export function clearVideos(mode: string): Promise<ClearResult> {
  return apiFetch<ClearResult>(`/api/v1/videos?mode=${encodeURIComponent(mode)}`, { method: "DELETE" });
}

/**
 * Subscribe to the SSE progress stream. EventSource can't send Authorization /
 * X-Tenant-ID headers, so we read the stream over fetch and parse `data:` lines
 * ourselves. Resolves when the stream ends; pass an AbortSignal to cancel.
 */
export async function streamVideoEvents(
  taskId: string,
  onMessage: (event: VideoEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  // SSE 也必须走 apiUrl() 单前缀守卫（ECOM-FIXES-0001 Bug B）：此前直拼 ${API_BASE_URL}/api/v1/... 在
  // API_BASE_URL 以 /api 结尾的生产配置下拼成 /api/api/v1/.../events → 404。fetch 主路径走了 apiUrl，SSE 漏了。
  const res = await fetch(
    apiUrl(`/api/v1/videos/${encodeURIComponent(taskId)}/events`),
    { headers: { Accept: "text/event-stream", ...authHeaders() }, signal }
  );

  if (res.status === 401) {
    if (authStore.get()) authStore.clear();
    throw new ApiError("登录已过期，请重新登录。", "UNAUTHORIZED", 401);
  }
  if (!res.ok || !res.body) {
    throw new ApiError(`进度订阅失败（${res.status}）`, "SSE_ERROR", res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const dataLine = frame.split("\n").find((line) => line.startsWith("data:"));
      if (!dataLine) continue;
      try {
        onMessage(JSON.parse(dataLine.slice(5).trim()) as VideoEvent);
      } catch {
        // ignore malformed frame
      }
    }
  }
}
