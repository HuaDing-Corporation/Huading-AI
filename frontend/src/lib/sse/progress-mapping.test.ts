import { describe, expect, it } from "vitest";

import { fromVideoRead, labelFor, mapSseStatus, TERMINAL } from "./progress-mapping";

describe("progress-mapping", () => {
  it("maps SSE statuses to UI statuses", () => {
    expect(mapSseStatus("SUCCESS")).toBe("done");
    expect(mapSseStatus("FAILURE")).toBe("failed");
    expect(mapSseStatus("PROGRESS")).toBe("running");
    expect(mapSseStatus(undefined)).toBe("queued");
  });

  it("labels by status", () => {
    expect(labelFor("done", 100)).toBe("已完成");
    expect(labelFor("running", 42)).toBe("生成中 42%");
  });

  it("knows terminal states", () => {
    expect(TERMINAL.includes("done")).toBe(true);
    expect(TERMINAL.includes("running")).toBe(false);
  });

  it("maps a VideoRead to a TrackedTask", () => {
    const t = fromVideoRead({
      id: "v1",
      title: "T",
      prompt: "",
      mode: "x",
      status: "done",
      progress: 100,
      created_at: "",
      playback_url: "u"
    });
    expect(t.taskId).toBe("v1");
    expect(t.status).toBe("done");
    expect(t.playbackUrl).toBe("u");
  });
});
