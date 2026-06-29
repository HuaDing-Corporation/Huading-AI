import { afterEach, describe, expect, it, vi } from "vitest";

import { authStore } from "@/lib/auth/store";

import { apiFetch } from "./client";

const SESSION = { token: "t", tenantId: "x", userId: "u", role: "admin" as const };

function json401() {
  return new Response(JSON.stringify({ error: { code: "UNAUTHORIZED", message: "no" } }), {
    status: 401,
    headers: { "Content-Type": "application/json" }
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("api URL construction", () => {
  it("does not duplicate /api when NEXT_PUBLIC_API_BASE_URL already ends with /api", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://huadingai.cn/api");
    vi.resetModules();

    const { apiUrl } = await import("./client");

    expect(apiUrl("/api/v1/videos")).toBe("https://huadingai.cn/api/v1/videos");
  });
});

describe("apiFetch 401 handling", () => {
  it("does NOT clear the session when an auth request was sent tokenless but the session hydrates before the 401 lands", async () => {
    // Reproduces the production race exactly: listVideos() (auth:true) fires
    // before AuthProvider hydrates, so authHeaders() reads a null session and
    // sends NO Authorization (first get()). By the time the 401 response lands,
    // AuthProvider has hydrated, so a naive handleUnauthorized() sees a truthy
    // session (second get()) and wrongly clears it -> refresh logout.
    const get = vi.spyOn(authStore, "get");
    get.mockReturnValueOnce(null); // at send time (authHeaders) -> tokenless
    get.mockReturnValue(SESSION); // at response time (hydrated)
    const clear = vi.spyOn(authStore, "clear").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json401()));

    await expect(apiFetch("/api/v1/videos", { auth: true })).rejects.toMatchObject({ status: 401 });
    expect(clear).not.toHaveBeenCalled();
  });

  it("does NOT clear the session on a 401 from a public (auth:false) request", async () => {
    vi.spyOn(authStore, "get").mockReturnValue(SESSION);
    const clear = vi.spyOn(authStore, "clear").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json401()));

    await expect(apiFetch("/api/v1/auth/login", { auth: false })).rejects.toMatchObject({
      status: 401
    });
    expect(clear).not.toHaveBeenCalled();
  });

  it("DOES clear the session on a 401 from an authenticated request", async () => {
    // Token present (e.g. expired) -> authHeaders() sets Authorization -> a 401
    // means the token is bad, so the session should be dropped.
    vi.spyOn(authStore, "get").mockReturnValue(SESSION);
    const clear = vi.spyOn(authStore, "clear").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json401()));

    await expect(apiFetch("/api/v1/videos", { auth: true })).rejects.toMatchObject({ status: 401 });
    expect(clear).toHaveBeenCalledTimes(1);
  });
});
