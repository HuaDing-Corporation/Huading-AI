"use client";

async function unregisterServiceWorkers() {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
  const registrations = await navigator.serviceWorker.getRegistrations();
  await Promise.all(registrations.map((registration) => registration.unregister()));
}

// Start MSW in the browser only when NEXT_PUBLIC_USE_MOCK=1 (dev/parallel period).
// Production is not a PWA; unregister stale workers so browsers cannot keep a
// bad bundle or old MSW worker pinned after deploys.
export async function initMocks() {
  if (typeof window === "undefined") return;
  if (process.env.NEXT_PUBLIC_USE_MOCK !== "1") {
    await unregisterServiceWorkers();
    return;
  }
  const { worker } = await import("./browser");
  await worker.start({ onUnhandledRequest: "bypass" });
}
