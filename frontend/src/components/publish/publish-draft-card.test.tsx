import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { PublishDraftItem } from "@/lib/api/types";

const markMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const writeTextMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/hooks", () => ({
  useMarkPublished: () => ({ mutateAsync: markMock.mutateAsync, isPending: markMock.isPending })
}));

import { PublishDraftCard } from "./publish-draft-card";

const item: PublishDraftItem = {
  platform_id: "douyin",
  title: "抖音标题",
  body: "适配抖音的文案内容",
  hashtags: ["#AI生成", "#好物"],
  cover_url: "https://mock.local/cover.png",
  media_url: "https://mock.local/v.mp4",
  publish_url: "https://mock.local/publish/douyin"
};
const RECORD_ID = "pub-1";

beforeEach(() => {
  markMock.isPending = false;
  markMock.mutateAsync.mockResolvedValue({ id: RECORD_ID, source_kind: "video", source_task_id: "v1", created_at: "", platforms: [] });
  writeTextMock.mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText: writeTextMock } });
});
afterEach(() => vi.clearAllMocks());

describe("PublishDraftCard (发布草稿卡)", () => {
  it("一键复制：writeText 收到含 标题+文案+话题 的组合文本", async () => {
    render(<PublishDraftCard recordId={RECORD_ID} item={item} platformName="抖音" />);
    fireEvent.click(screen.getByRole("button", { name: copy.publish.copyText }));
    await waitFor(() => expect(writeTextMock).toHaveBeenCalledTimes(1));
    const arg = writeTextMock.mock.calls[0][0] as string;
    expect(arg).toContain("适配抖音的文案内容");
    expect(arg).toContain("抖音标题");
    expect(arg).toContain("#AI生成");
  });

  // 合规核心：去发布仅 window.open 公开 publish_url，零网络请求/无 mutation。
  it("去发布：仅 window.open(publish_url, _blank)，零网络请求(合规承重)", () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    render(<PublishDraftCard recordId={RECORD_ID} item={item} platformName="抖音" />);
    fireEvent.click(screen.getByRole("button", { name: copy.publish.goPublishAt("抖音") }));
    expect(openSpy).toHaveBeenCalledWith("https://mock.local/publish/douyin", "_blank", "noopener,noreferrer");
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(markMock.mutateAsync).not.toHaveBeenCalled();
    openSpy.mockRestore();
    fetchSpy.mockRestore();
  });

  it("标记已发布：发 markPublished({recordId, platformId})，按钮转「已发布」", async () => {
    render(<PublishDraftCard recordId={RECORD_ID} item={item} platformName="抖音" />);
    fireEvent.click(screen.getByRole("button", { name: copy.publish.markPublished }));
    await waitFor(() => expect(markMock.mutateAsync).toHaveBeenCalledWith({ recordId: "pub-1", platformId: "douyin" }));
    expect(await screen.findByRole("button", { name: copy.publish.marked })).toBeDisabled();
  });

  it("编辑文案后复制：复制的是编辑后内容", async () => {
    render(<PublishDraftCard recordId={RECORD_ID} item={item} platformName="抖音" />);
    fireEvent.change(screen.getByLabelText(copy.publish.cardTextLabel), { target: { value: "改过的文案" } });
    fireEvent.click(screen.getByRole("button", { name: copy.publish.copyText }));
    await waitFor(() => expect(writeTextMock).toHaveBeenCalled());
    expect(writeTextMock.mock.calls[0][0] as string).toContain("改过的文案");
  });

  it("复制失败：clipboard 拒绝 → 友好错误", async () => {
    writeTextMock.mockRejectedValue(new Error("denied"));
    render(<PublishDraftCard recordId={RECORD_ID} item={item} platformName="抖音" />);
    fireEvent.click(screen.getByRole("button", { name: copy.publish.copyText }));
    expect(await screen.findByText(copy.publish.copyFailed)).toBeInTheDocument();
  });

  // 🔴 CLIPBOARD-TRUTH-0001 补网：非安全上下文（navigator.clipboard 缺失）→ **不谎报**「已复制」。
  // 旧写法的 `?.` 短路使这条路径谎报（界面翻「已复制」而剪贴板空）。本组件有 copyFailed 提示 →
  // 缺 API 与 writeText 抛错归一到同一失败路径：显示「复制失败，请手动选择文案」，且**不**显示「已复制」。
  // ⚠️ 放行微任务后再断言（同 copyable-block.test.tsx:54）—— 否则同步负断言好坏版本都绿 = 假测试。
  it("🔴 非安全上下文（无 clipboard）→ 显示复制失败、不谎报「已复制」", async () => {
    Object.assign(navigator, { clipboard: undefined });
    render(<PublishDraftCard recordId={RECORD_ID} item={item} platformName="抖音" />);
    fireEvent.click(screen.getByRole("button", { name: copy.publish.copyText }));
    expect(await screen.findByText(copy.publish.copyFailed)).toBeInTheDocument();
    // 负断言（放行微任务后）：坏实现会在此刻已翻「已复制」。
    expect(screen.queryByText(copy.publish.copied)).not.toBeInTheDocument();
  });

  it("标记失败：mutateAsync 拒绝 → role=alert，按钮不转已发布", async () => {
    markMock.mutateAsync.mockRejectedValue(new Error("boom"));
    render(<PublishDraftCard recordId={RECORD_ID} item={item} platformName="抖音" />);
    fireEvent.click(screen.getByRole("button", { name: copy.publish.markPublished }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: copy.publish.markPublished })).toBeInTheDocument();
  });

  it("下载成片：链接指向 media_url", () => {
    render(<PublishDraftCard recordId={RECORD_ID} item={item} platformName="抖音" />);
    expect(screen.getByRole("link", { name: new RegExp(copy.publish.download) })).toHaveAttribute("href", "https://mock.local/v.mp4");
  });

  it("防连点：标记中按钮显「标记中…」并禁用", () => {
    markMock.isPending = true;
    render(<PublishDraftCard recordId={RECORD_ID} item={item} platformName="抖音" />);
    expect(screen.getByRole("button", { name: copy.publish.marking })).toBeDisabled();
  });
});
