import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const searchMock = vi.hoisted(() => ({ params: {} as Record<string, string> }));
const platformsMock = vi.hoisted(() => ({ data: [] as Array<{ id: string; name: string }>, isLoading: false, isError: false }));
const createMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));

vi.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: (k: string) => searchMock.params[k] ?? null })
}));
vi.mock("@/lib/api/hooks", () => ({
  usePublishPlatforms: () => ({ data: platformsMock.data, isLoading: platformsMock.isLoading, isError: platformsMock.isError }),
  useCreatePublishDrafts: () => ({ mutateAsync: createMock.mutateAsync, isPending: createMock.isPending })
}));
// 子组件占位为标记，本测试只断「选平台→生成→渲染各平台卡」编排。
vi.mock("@/components/publish/publish-draft-card", () => ({
  PublishDraftCard: ({ platformName }: { platformName: string }) => <div data-testid="draft-card">{platformName}</div>
}));
vi.mock("@/components/publish/publish-records", () => ({ PublishRecords: () => <div data-testid="records" /> }));

import { PublishCenter } from "./publish-center";

const PLATFORMS = [
  { id: "douyin", name: "抖音" },
  { id: "kuaishou", name: "快手" },
  { id: "wechat_channels", name: "视频号" },
  { id: "xiaohongshu", name: "小红书" },
  { id: "bilibili", name: "B站" }
];

beforeEach(() => {
  searchMock.params = { source_kind: "video", source_task_id: "v1" };
  platformsMock.data = PLATFORMS;
  platformsMock.isLoading = false;
  platformsMock.isError = false;
  createMock.isPending = false;
  createMock.mutateAsync.mockResolvedValue([
    { id: "pub-1", platform: "douyin", source_kind: "video", source_task_id: "v1", title: "t", text: "x", topics: [], publish_url: "u", status: "draft", created_at: "" },
    { id: "pub-2", platform: "kuaishou", source_kind: "video", source_task_id: "v1", title: "t", text: "x", topics: [], publish_url: "u", status: "draft", created_at: "" }
  ]);
});
afterEach(() => vi.clearAllMocks());

describe("PublishCenter (发布中心编排)", () => {
  it("多选平台 → 生成草稿 → createDrafts 正确 body + 渲染各平台卡", async () => {
    render(<PublishCenter />);
    fireEvent.click(screen.getByRole("button", { name: "抖音" }));
    fireEvent.click(screen.getByRole("button", { name: "快手" }));
    fireEvent.click(screen.getByRole("button", { name: copy.publish.generateDrafts }));

    await waitFor(() => expect(createMock.mutateAsync).toHaveBeenCalled());
    expect(createMock.mutateAsync.mock.calls[0][0]).toEqual({ source_kind: "video", source_task_id: "v1", platforms: ["douyin", "kuaishou"] });
    expect(await screen.findAllByTestId("draft-card")).toHaveLength(2);
  });

  it("未选平台：生成按钮禁用", () => {
    render(<PublishCenter />);
    expect(screen.getByRole("button", { name: copy.publish.generateDrafts })).toBeDisabled();
  });

  it("无产物来源：提示从历史/成片进入，不显平台选择", () => {
    searchMock.params = {};
    render(<PublishCenter />);
    expect(screen.getByText(copy.publish.noSourceHint)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: copy.publish.generateDrafts })).not.toBeInTheDocument();
  });

  it("生成草稿失败：mutateAsync 拒绝 → role=alert 显示错误", async () => {
    createMock.mutateAsync.mockRejectedValue(new Error("boom"));
    render(<PublishCenter />);
    fireEvent.click(screen.getByRole("button", { name: "抖音" }));
    fireEvent.click(screen.getByRole("button", { name: copy.publish.generateDrafts }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  it("平台加载中：显示加载态", () => {
    platformsMock.data = [];
    platformsMock.isLoading = true;
    render(<PublishCenter />);
    expect(screen.getByText(copy.publish.recordsLoading)).toBeInTheDocument();
  });

  it("平台加载失败：显示错误态", () => {
    platformsMock.data = [];
    platformsMock.isError = true;
    render(<PublishCenter />);
    expect(screen.getByText(copy.publish.recordsError)).toBeInTheDocument();
  });
});
