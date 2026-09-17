import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/mocks/server";
import { streamVideoEvents } from "@/lib/api/videos";
import { eventToProgress, fromVideoRead } from "./progress-mapping";
import type { VideoListItem } from "@/lib/api/types";

const outcome = { completion_kind: "failed_charged", status: "settled", policy_version: "145s-no-refund-v1",
  requested_credits: 123, settled_credits: 123, released_credits: 0 } as const;
const read: VideoListItem = { id: "failed-task", topic: "原文案", mode: "avatar_talk", avatar_provider: "heygen",
  status: "failed", progress: 30, error_code: "HEYGEN_AUDIO_DURATION_EXCEEDED", created_at: "2026-09-15T00:00:00Z" };

describe("145s paid failure presentation", () => {
  it("rejects a contradictory SSE terminal instead of swallowing it as a successful stream", async () => {
    server.use(http.get("http://localhost:8000/api/v1/videos/invalid/events", () =>
      new HttpResponse(`data: ${JSON.stringify({ status: "done", error_code: "HEYGEN_AUDIO_DURATION_EXCEEDED", billing_outcome: outcome })}\n\n`,
        { headers: { "Content-Type": "text/event-stream" } })));
    const received = vi.fn();
    await expect(streamVideoEvents("invalid", received)).rejects.toMatchObject({ code: "INVALID_AVATAR_BILLING_OUTCOME" });
    expect(received).not.toHaveBeenCalled();
  });
  it("REST/history preserves failure and authoritative money, never success/refund", () => {
    const mapped = fromVideoRead({ ...read, billing_outcome: outcome });
    expect(mapped.status).toBe("failed");
    expect(mapped.error).toContain("生成失败（超145秒，费用不退）");
    expect(mapped.error).toContain("已结算 123 积分");
    expect(mapped.error).not.toMatch(/已退款|生成成功/);
  });
  it("SSE communicates the same failure and amount", () => {
    expect(eventToProgress({ status: "failed", error_code: "HEYGEN_AUDIO_DURATION_EXCEEDED", billing_outcome: outcome }))
      .toMatchObject({ status: "failed", error: expect.stringContaining("已结算 123 积分") });
  });
  it.each([
    { status: "done" }, { error_code: "OTHER_FAILURE" },
    { billing_outcome: { ...outcome, settled_credits: 124 } },
    { billing_outcome: { ...outcome, released_credits: 1 } }
  ])("does not turn contradictory billing into a success or trusted money: %j", (change) => {
    expect(() => fromVideoRead({ ...read, billing_outcome: outcome, ...change } as VideoListItem)).toThrow();
  });
});
