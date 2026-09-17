import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { fromVideoRead, eventToProgress } from "@/lib/sse/progress-mapping";
import { useMediaUrlRefreshScope } from "@/lib/media/use-media-url-refresh";
import { TaskCard } from "./task-card";
import { VideoDetailDialog } from "@/components/history/video-detail-dialog";

const read = {
  id: "heygen-test", status: "running" as const, progress: 38, topic: "口播测试",
  mode: "avatar_talk", created_at: "2026-09-15T00:00:00Z"
};
function Card({ task }: { task: ReturnType<typeof fromVideoRead> }) {
  const refresh = useMediaUrlRefreshScope(async () => {});
  return <TaskCard task={task} refresh={refresh} onOpen={vi.fn()} onRetry={vi.fn()} />;
}

describe("HeyGen 任务快照与恢复状态", () => {
  it("145收费失败：历史卡片与弹窗显示两费不退及实扣，不能一键重付", () => {
    const task = { ...fromVideoRead({ ...read, status: "failed", error_code: "HEYGEN_AUDIO_DURATION_EXCEEDED",
      billing_outcome: { completion_kind: "failed_charged", status: "settled", policy_version: "145s-no-refund-v1",
        requested_credits: 123, settled_credits: 123, released_credits: 0 } }), retryable: true };
    const card = render(<Card task={task} />);
    expect(screen.getByText(/生成失败（超145秒，费用不退）。已结算 123 积分/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看详情" })).toBeEnabled();
    card.unmount();
    function Detail() {
      const refresh = useMediaUrlRefreshScope(async () => {});
      return <VideoDetailDialog detail={{ task, createdAt: read.created_at, modeLabel: "数字人口播" }}
        onClose={vi.fn()} onOpenPage={vi.fn()} refresh={refresh} />;
    }
    render(<Detail />);
    expect(screen.getByText(/生成失败（超145秒，费用不退）。已结算 123 积分/)).toBeVisible();
  });
  // 捕获：把新建表单的目标型号用于所有历史，或丢弃服务端模型快照。
  it.each([
    ["heygen", "avatar_iv", "HeyGen Avatar IV"],
    ["heygen", "lipsync_precision", "HeyGen Precision"],
    ["omnihuman", null, "OmniHuman"],
    [undefined, undefined, "OmniHuman"]
  ] as const)("历史 %s/%s 只显示真实快照/兼容旧通路", (provider, model, label) => {
    const item = { ...read, avatar_provider: provider, avatar_model: model };
    render(<Card task={fromVideoRead(item)} />);
    expect(screen.getByText(label, { exact: false })).toBeInTheDocument();
    if (provider !== "heygen") expect(screen.queryByText(/HeyGen/)).not.toBeInTheDocument();
  });

  it("非数字人任务不因缺省字段而出现 OmniHuman/HeyGen", () => {
    render(<Card task={fromVideoRead({ ...read, mode: "video_gen" })} />);
    expect(screen.queryByText(/OmniHuman|HeyGen/)).not.toBeInTheDocument();
  });

  it.each([
    ["HEYGEN_PENDING", "仍在处理中"],
    ["HEYGEN_REVIEW_REQUIRED", "需要核对"]
  ])("%s：GET与SSE一致，不显示普通进度/完成/重试", (code, label) => {
    const task = fromVideoRead({ ...read, error_code: code });
    const event = eventToProgress({ status: "running", progress: 38, error_code: code });
    expect(task.statusLabel).toBe(label);
    expect(event?.statusLabel).toBe(label);
    render(<Card task={{ ...task, retryable: true, heartbeatAt: Date.now(), startedAt: Date.now() }} />);
    expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    expect(screen.getByRole("status")).toHaveTextContent(/请勿重复提交/);
    expect(screen.getByRole("button", { name: "查看详情" })).toBeEnabled();
    expect(screen.getByRole("status")).toHaveTextContent("heygen-test");
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(screen.queryByText(/生成中 38%|已完成|仍在生成/)).not.toBeInTheDocument();
  });
});
