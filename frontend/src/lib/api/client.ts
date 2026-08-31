// Central fetch wrapper: injects auth headers, unwraps the ApiResponse envelope,
// and turns errors into a typed ApiError. A 401 clears the session so the route
// guards bounce the user to /login.

import { authStore } from "@/lib/auth/store";
import type { ApiResponse } from "@/lib/api/types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export function apiUrl(path: string): string {
  if (API_BASE_URL.endsWith("/api") && path.startsWith("/api/")) {
    return `${API_BASE_URL}${path.slice("/api".length)}`;
  }
  return `${API_BASE_URL}${path}`;
}

export class ApiError extends Error {
  code: string;
  status: number;
  detail?: unknown;
  /**
   * 服务端在**自己的异常处理里**挂上的 per-endpoint 成败结论（BE `schemas/response.py OperationOutcome`；
   * 文案三端点由 `core/exceptions.py:_copy_generation_error_outcome` 生成）。
   *
   * 🔴 **它的价值不在字段内容，而在"它出现了"**（FIX6 · P1-2）：
   *    带 outcome  ⇒ 请求**到达了应用层**、走完了后端的异常分支（那条分支里做了 release）
   *    不带 outcome ⇒ 网络中断 / 网关超时 / 代理 5xx —— 后端做没做完，**前端无从知道**
   *    这就是"能不能对用户陈述资金事实"的分界线。见 `copyFailureBilling`。
   * ⚠️ 故意是 `unknown`：不在传输层假设形状，由消费方运行时窄化（与 `detail` 同一约定）。
   */
  outcome?: unknown;

  constructor(message: string, code: string, status: number, detail?: unknown, outcome?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.detail = detail;
    this.outcome = outcome;
  }
}

/** Public runtime guard for feature-level error parsers; keeps transport checks centralized. */
export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Auth + tenant headers for an authenticated request. */
export function authHeaders(): Record<string, string> {
  const session = authStore.get();
  if (!session) return {};
  return {
    Authorization: `Bearer ${session.token}`,
    "X-Tenant-ID": session.tenantId
  };
}

function handleUnauthorized() {
  // Drop the session; AuthProvider/route guards react and redirect to /login.
  if (authStore.get()) authStore.clear();
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Set false for public endpoints (login/register). Default true. */
  auth?: boolean;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, auth = true, headers, ...rest } = options;

  const finalHeaders: Record<string, string> = {
    Accept: "application/json",
    ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    ...(auth ? authHeaders() : {}),
    ...((headers as Record<string, string>) ?? {})
  };

  // Only an authenticated 401 (the request actually carried a token) should
  // drop the session. A tokenless 401 — e.g. a request that raced ahead of
  // session hydration, or a public endpoint — must not log the user out. (Fix 2)
  const sentAuth = finalHeaders.Authorization !== undefined;

  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      ...rest,
      headers: finalHeaders,
      body: body !== undefined ? JSON.stringify(body) : undefined
    });
  } catch {
    throw new ApiError("网络连接失败，请检查后端服务是否在线。", "NETWORK_ERROR", 0);
  }

  if (res.status === 401 && sentAuth) {
    handleUnauthorized();
  }

  let payload: ApiResponse<T> | null = null;
  try {
    payload = (await res.json()) as ApiResponse<T>;
  } catch {
    payload = null;
  }

  if (!res.ok || payload?.error) {
    const err = payload?.error;
    throw new ApiError(
      err?.message ?? `请求失败（${res.status}）`,
      err?.code ?? "HTTP_ERROR",
      res.status,
      err?.detail,
      err?.outcome
    );
  }

  return (payload?.data as T) ?? (null as T);
}

export interface MultipartOptions {
  defaultErrorMessage?: string;
  defaultErrorCode?: string;
}

/**
 * POST a multipart FormData (file/audio 上传) 并解包 ApiResponse 封套。与 apiFetch 分开：body 是
 * FormData，须由浏览器自行设 multipart Content-Type/boundary，故只附鉴权头。鉴权/401/封套/网络错误
 * 逻辑集中于此，供 uploads.ts(图片) 与 brand-voices.ts(音频) 复用(单一实现，DRY)。
 */
export async function multipartFetch<T>(path: string, form: FormData, opts: MultipartOptions = {}): Promise<T> {
  const headers = authHeaders();
  let res: Response;
  try {
    res = await fetch(apiUrl(path), { method: "POST", headers, body: form });
  } catch {
    throw new ApiError("网络连接失败，请检查后端服务是否在线。", "NETWORK_ERROR", 0);
  }

  // 仅当请求实际携带 token 的 401 才清会话（与 apiFetch 一致，避免无 token 的 401 误登出）。
  if (res.status === 401 && headers.Authorization !== undefined) handleUnauthorized();

  let payload: ApiResponse<T> | null = null;
  try {
    payload = (await res.json()) as ApiResponse<T>;
  } catch {
    payload = null;
  }

  if (!res.ok || payload?.error) {
    const err = payload?.error;
    throw new ApiError(
      err?.message ?? `${opts.defaultErrorMessage ?? "请求失败"}（${res.status}）`,
      err?.code ?? opts.defaultErrorCode ?? "HTTP_ERROR",
      res.status,
      err?.detail,
      err?.outcome
    );
  }

  return (payload?.data as T) ?? (null as T);
}
