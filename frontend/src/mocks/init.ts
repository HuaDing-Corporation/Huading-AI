"use client";

// Start MSW in the browser only when NEXT_PUBLIC_USE_MOCK=1 (dev/parallel period).
export async function initMocks() {
  if (process.env.NEXT_PUBLIC_USE_MOCK !== "1" || typeof window === "undefined") return;
  const { worker } = await import("./browser");
  await worker.start({ onUnhandledRequest: "bypass" });
}
