import { apiFetch } from "@/lib/api/client";
import type { CurrentUserResponse, TenantRegisterResponse, TokenResponse } from "@/lib/api/types";

export interface LoginInput {
  tenantSlug: string;
  email: string;
  password: string;
}

export function login(input: LoginInput): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    auth: false,
    body: {
      tenant_slug: input.tenantSlug,
      email: input.email,
      password: input.password
    }
  });
}

// 注册（AUTH-UI-0001）——接已有 BE POST /auth/register-tenant（extra="forbid"，full_name 可选/空则省略）。
export interface RegisterInput {
  tenantSlug: string;
  tenantName: string;
  email: string;
  password: string;
  fullName?: string;
}

export function registerTenant(input: RegisterInput): Promise<TenantRegisterResponse> {
  const fullName = input.fullName?.trim();
  return apiFetch<TenantRegisterResponse>("/api/v1/auth/register-tenant", {
    method: "POST",
    auth: false,
    body: {
      tenant_slug: input.tenantSlug,
      tenant_name: input.tenantName,
      email: input.email,
      password: input.password,
      // 选填：空则不发键（BE extra="forbid" 接受缺省，full_name 默认 None）。
      ...(fullName ? { full_name: fullName } : {})
    }
  });
}

export function fetchMe(): Promise<CurrentUserResponse> {
  return apiFetch<CurrentUserResponse>("/api/v1/auth/me", { method: "GET" });
}
