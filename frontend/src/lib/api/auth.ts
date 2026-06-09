import { apiFetch } from "@/lib/api/client";
import type { CurrentUserResponse, TokenResponse } from "@/lib/api/types";

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

export function fetchMe(): Promise<CurrentUserResponse> {
  return apiFetch<CurrentUserResponse>("/api/v1/auth/me", { method: "GET" });
}
