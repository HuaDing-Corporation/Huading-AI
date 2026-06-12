// Central fetch wrapper: injects auth headers, unwraps the ApiResponse envelope,
// and turns errors into a typed ApiError. A 401 clears the session so the route
// guards bounce the user to /login.

import { authStore } from "@/lib/auth/store";
import type { ApiResponse } from "@/lib/api/types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export class ApiError extends Error {
  code: string;
  status: number;
  detail?: unknown;

  constructor(message: string, code: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
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
    res = await fetch(`${API_BASE_URL}${path}`, {
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
      err?.detail
    );
  }

  return (payload?.data as T) ?? (null as T);
}
