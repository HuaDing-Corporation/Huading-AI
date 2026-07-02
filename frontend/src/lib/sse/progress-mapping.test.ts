import { describe, expect, it } from "vitest";

import { eventToProgress, fromVideoRead, mapSseStatus, TERMINAL } from "./progress-mapping";

describe("progress-mapping", () => {
  it("maps new lowercase + old uppercase statuses", () => {
    expect(mapSseStatus("done")).toBe("done");
    expect(mapSseStatus("SUCCESS")).toBe("done");
    expect(mapSseStatus("FAILURE")).toBe("failed");
    expect(mapSseStatus("running")).toBe("running");
    expect(mapSseStatus(undefined)).toBe("queued");
  });

  it("new frame progress is a 0..100 int and is NOT rescaled (sse-1)", () => {
    const p = eventToProgress({ status: "running", progress: 1 });
    expect(p?.status).toBe("running");
    expect(p?.progress).toBe(1); // must be 1%, not 100%
  });

  it("preserves fractional new-frame progress for the watchdog and rounds only the label", () => {
    const p = eventToProgress({ status: "running", progress: 25.125, step: "seedance" });
    expect(p?.progress).toBe(25.125);
    expect(p?.statusLabel).toContain("25%");
  });

  it("old uppercase frame rescales 0..1 progress", () => {
    const p = eventToProgress({ status: "PROGRESS", progress: 0.5 });
    expect(p?.progress).toBe(50);
  });

  it("drops sse_timeout keep-alive", () => {
    expect(eventToProgress({ stage: "sse_timeout" })).toBeNull();
  });

  it("maps a VideoListItem to a TrackedTask", () => {
    const t = fromVideoRead({ id: "v1", status: "done", progress: 100, topic: "T", created_at: "" });
    expect(t.taskId).toBe("v1");
    expect(t.status).toBe("done");
  });

  // LABEL-TOGGLE-UI-0001：徽标服务端数据源 apply_visible_label → applyVisibleLabel（两态 + 缺省兜底）。
  it("maps apply_visible_label → applyVisibleLabel (true/false/缺省→false)", () => {
    expect(fromVideoRead({ id: "a", status: "done", progress: 100, topic: "T", created_at: "", apply_visible_label: true }).applyVisibleLabel).toBe(true);
    expect(fromVideoRead({ id: "b", status: "done", progress: 100, topic: "T", created_at: "", apply_visible_label: false }).applyVisibleLabel).toBe(false);
    expect(fromVideoRead({ id: "c", status: "done", progress: 100, topic: "T", created_at: "" }).applyVisibleLabel).toBe(false);
  });

  it("knows terminal states", () => {
    expect(TERMINAL.includes("done")).toBe(true);
    expect(TERMINAL.includes("running")).toBe(false);
  });
});
