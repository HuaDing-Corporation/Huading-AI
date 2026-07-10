import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { registerTenant } from "@/lib/api/auth";
import { server } from "@/mocks/server";

// AUTH-UI-0001-FIX1 · P2（防假绿）：registerTenant 出站请求体断言——字段 snake_case、无多余键、
// full_name 空则省略键（非发空串）。用 server.use 覆盖捕获出站 body（全局 handler 不暴露入参）。
function captureRegisterBody(): { get: () => Record<string, unknown> | null } {
  let captured: Record<string, unknown> | null = null;
  server.use(
    http.post("*/api/v1/auth/register-tenant", async ({ request }) => {
      captured = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(
        {
          data: {
            tenant: { id: "t", slug: "huading", name: "华鼎科技" },
            user: { id: "u", tenant_id: "t", email: "a@b.com", full_name: null, role: "admin" },
            token: { access_token: "tok", token_type: "bearer", tenant_id: "t", user_id: "u", role: "admin" }
          },
          error: null,
          request_id: "test"
        },
        { status: 201 }
      );
    })
  );
  return { get: () => captured };
}

const FULL = {
  tenantSlug: "huading",
  tenantName: "华鼎科技",
  email: "a@b.com",
  password: "pw123456"
};

describe("registerTenant · 出站请求体（P2 防假绿）", () => {
  it("有 fullName：snake_case 五键齐、无多余键", async () => {
    const cap = captureRegisterBody();
    await registerTenant({ ...FULL, fullName: "陈大文" });
    expect(cap.get()).toEqual({
      tenant_slug: "huading",
      tenant_name: "华鼎科技",
      email: "a@b.com",
      password: "pw123456",
      full_name: "陈大文"
    });
  });

  it("fullName 未传 → 省略 full_name 键（不发空串），仅四键", async () => {
    const cap = captureRegisterBody();
    await registerTenant(FULL);
    const body = cap.get()!;
    expect(body).toEqual({
      tenant_slug: "huading",
      tenant_name: "华鼎科技",
      email: "a@b.com",
      password: "pw123456"
    });
    expect(body).not.toHaveProperty("full_name");
  });

  it("fullName 纯空白 → trim 后省略键", async () => {
    const cap = captureRegisterBody();
    await registerTenant({ ...FULL, fullName: "   " });
    expect(cap.get()).not.toHaveProperty("full_name");
  });

  it("有 fullName 时 trim 首尾空白后再发", async () => {
    const cap = captureRegisterBody();
    await registerTenant({ ...FULL, fullName: "  陈大文  " });
    expect((cap.get() as { full_name?: string }).full_name).toBe("陈大文");
  });
});
