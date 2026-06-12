import { beforeEach, describe, expect, it, vi } from "vitest";

const KEY = "huading.session";
const SEED = { token: "t1", tenantId: "ten1", userId: "u1", role: "owner" as const };

describe("authStore self-hydration", () => {
  beforeEach(() => {
    window.localStorage.clear();
    // Fresh module instance per test => `hydrated` starts false (simulates a
    // fresh page load where nothing has read the store yet).
    vi.resetModules();
  });

  it("get() returns the persisted session without an explicit hydrate()", async () => {
    window.localStorage.setItem(KEY, JSON.stringify(SEED));
    const { authStore } = await import("./store");
    expect(authStore.get()?.token).toBe("t1");
  });

  it("get() returns null when nothing is persisted", async () => {
    const { authStore } = await import("./store");
    expect(authStore.get()).toBeNull();
  });

  it("set() then get() returns the in-memory session and persists it", async () => {
    const { authStore } = await import("./store");
    authStore.set(SEED);
    expect(authStore.get()?.token).toBe("t1");
    expect(window.localStorage.getItem(KEY)).toContain("t1");
  });

  it("clear() drops the session and a later get() does not resurrect it from storage", async () => {
    window.localStorage.setItem(KEY, JSON.stringify(SEED));
    const { authStore } = await import("./store");
    authStore.clear();
    expect(authStore.get()).toBeNull();
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });
});
