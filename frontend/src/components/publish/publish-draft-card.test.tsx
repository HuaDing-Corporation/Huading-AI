import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { PublishRecord } from "@/lib/api/types";

const markMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const writeTextMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/hooks", () => ({
  useMarkPublished: () => ({ mutateAsync: markMock.mutateAsync, isPending: markMock.isPending })
}));

import { PublishDraftCard } from "./publish-draft-card";

const record: PublishRecord = {
  id: "pub-1",
  platform: "douyin",
  source_kind: "video",
  source_task_id: "v1",
  title: "抖音标题",
  text: "适配抖音的文案内容",
  topics: ["#AI生成", "#好物"],
  cover_url: "https://mock.local/cover.png",
  video_url: "https://mock.local/v.mp4",
  publish_url: "https://mock.local/publish/douyin",
  status: "draft",
  created_at: ""
};

beforeEach(() => {
  markMock.isPending = false;
  markMock.mutateAsync.mockResolvedValue({ ...record, status: "published" });
  writeTextMock.mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText: writeTextMock } });
});
afterEach(() => vi.clearAllMocks());

describe("PublishDraftCard (发布草稿卡)", () => {
  // 承重：一键复制写入剪贴板（含 标题+文案+话题）。
  it("一键复制：navigator.clipboard.writeText 收到含文案的组合文本", async () => {
    render(<PublishDraftCard record={record} platformName="抖音" />);
    fireEvent.click(screen.getByRole("button", { name: copy.publish.copyText }));
    await waitFor(() => expect(writeTextMock).toHaveBeenCalledTimes(1));
    const arg = writeTextMock.mock.calls[0][0] as string;
    expect(arg).toContain("适配抖音的文案内容");
    expect(arg).toContain("抖音标题");
    expect(arg).toContain("#AI生成");
  });

  // 合规核心：去发布仅 window.open 公开 publish_url，绝不触发任何网络请求/发布 API/社媒登录。
  it("去发布：仅 window.open(publish_url, _blank)，零网络请求(合规承重)", () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    render(<PublishDraftCard record={record} platformName="抖音" />);
    fireEvent.click(screen.getByRole("button", { name: copy.publish.goPublishAt("抖音") }));
    expect(openSpy).toHaveBeenCalledWith("https://mock.local/publish/douyin", "_blank", "noopener,noreferrer");
    expect(fetchSpy).not.toHaveBeenCalled(); // 硬探针：去发布不发任何 fetch
    expect(markMock.mutateAsync).not.toHaveBeenCalled(); // 也不调任何 mutation
    openSpy.mockRestore();
    fetchSpy.mockRestore();
  });

  it("复制失败：clipboard 拒绝 → 友好错误(不抛)", async () => {
    writeTextMock.mockRejectedValue(new Error("denied"));
    render(<PublishDraftCard record={record} platformName="抖音" />);
    fireEvent.click(screen.getByRole("button", { name: copy.publish.copyText }));
    expect(await screen.findByText(copy.publish.copyFailed)).toBeInTheDocument();
  });

  it("标记失败：mutateAsync 拒绝 → role=alert 显示错误，按钮不转已发布", async () => {
    markMock.mutateAsync.mockRejectedValue(new Error("boom"));
    render(<PublishDraftCard record={record} platformName="抖音" />);
    fireEvent.click(screen.getByRole("button", { name: copy.publish.markPublished }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: copy.publish.markPublished })).toBeInTheDocument();
  });

  it("标记已发布：发 markPublished(id)，按钮转「已发布」", async () => {
    render(<PublishDraftCard record={record} platformName="抖音" />);
    fireEvent.click(screen.getByRole("button", { name: copy.publish.markPublished }));
    await waitFor(() => expect(markMock.mutateAsync).toHaveBeenCalledWith("pub-1"));
    expect(await screen.findByRole("button", { name: copy.publish.marked })).toBeDisabled();
  });

  it("编辑文案后复制：复制的是编辑后的内容", async () => {
    render(<PublishDraftCard record={record} platformName="抖音" />);
    fireEvent.change(screen.getByLabelText(copy.publish.cardTextLabel), { target: { value: "改过的文案" } });
    fireEvent.click(screen.getByRole("button", { name: copy.publish.copyText }));
    await waitFor(() => expect(writeTextMock).toHaveBeenCalled());
    expect(writeTextMock.mock.calls[0][0] as string).toContain("改过的文案");
  });

  it("下载成片：链接指向 video_url", () => {
    render(<PublishDraftCard record={record} platformName="抖音" />);
    expect(screen.getByRole("link", { name: new RegExp(copy.publish.download) })).toHaveAttribute("href", "https://mock.local/v.mp4");
  });

  it("防连点：标记中按钮显「标记中…」并禁用", () => {
    markMock.isPending = true;
    render(<PublishDraftCard record={record} platformName="抖音" />);
    expect(screen.getByRole("button", { name: copy.publish.marking })).toBeDisabled();
  });
});
