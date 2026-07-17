import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VideoPlayer } from "@/components/video/video-player";

// MEDIA-URL-REFRESH-CONVERGE-0001 · **第 1 片：先补网**（本 commit 零产品代码改动）。
//
// VideoPlayer 此前**零测试覆盖** —— 而下一片要迁移它、还要修它身上一个真 bug（缺 URL 变更重置）。
// 无网迁移 = 拿用户的详情页赌运气。故本片先把「迁移**不该**改的东西」钉死，
// 下一片迁完这些原样全绿 = 零回归的证据（照 #185 第 1 片 4f75b68d 立的规矩：**顺序即证据**）。
//
// ⚠️ 本片**有意不钉**「刷新后新 URL 再失效 → 永不再报」这个当前行为 ——
// 那正是要修的 bug（详情页刷新后再过期 → 播放器哑死到组件卸载）。把 bug 钉进测试，
// 下一片就得改测试来修 bug，而「改既有测试的期望值」是本项目认过的、最容易掩盖问题的动作。
// 不变量才进网，缺陷不进网。
const URL_A = "https://cdn/a.mp4?sig=1";

afterEach(() => vi.clearAllMocks());

describe("VideoPlayer · 迁移前基线（不变量）", () => {
  it("渲染 <video>：src / poster / controls / preload=metadata", () => {
    render(<VideoPlayer playbackUrl={URL_A} downloadUrl={null} poster="https://cdn/a.jpg" onUrlExpired={vi.fn()} />);

    const video = document.querySelector("video");
    expect(video).toHaveAttribute("src", URL_A);
    expect(video).toHaveAttribute("poster", "https://cdn/a.jpg");
    expect(video).toHaveAttribute("controls");
    expect(video).toHaveAttribute("preload", "metadata");
  });

  it("有 downloadUrl → 渲染下载链接（带 download 属性）；无 → 不渲染", () => {
    const { unmount } = render(
      <VideoPlayer playbackUrl={URL_A} downloadUrl="https://cdn/a.mp4?dl=1" poster={null} onUrlExpired={vi.fn()} />
    );
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "https://cdn/a.mp4?dl=1");
    expect(link).toHaveAttribute("download");
    unmount();

    render(<VideoPlayer playbackUrl={URL_A} downloadUrl={null} poster={null} onUrlExpired={vi.fn()} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  // 🔴 **迁移必须保住的那条**：同一个 URL 连报多次 error → onUrlExpired 只调一次。
  // 浏览器对同一 src 会连发 error；每个都触发重取 = 打爆后端。这条在迁移前后都必须成立。
  it("同一 URL 连报多次 error → onUrlExpired 恰调一次", () => {
    const onUrlExpired = vi.fn();
    render(<VideoPlayer playbackUrl={URL_A} downloadUrl={null} poster={null} onUrlExpired={onUrlExpired} />);

    const video = document.querySelector("video")!;
    fireEvent.error(video);
    fireEvent.error(video);
    fireEvent.error(video);
    expect(onUrlExpired).toHaveBeenCalledTimes(1);
  });

  it("playbackUrl 为空 → 不渲染 src", () => {
    render(<VideoPlayer playbackUrl={null} downloadUrl={null} poster={null} onUrlExpired={vi.fn()} />);
    expect(document.querySelector("video")).not.toHaveAttribute("src");
  });

  // 🔴 写这张网的时候撞出了**第三个 bug**（前两个是「缺 URL 变更重置」「注释说 per render 实为 per mount」）：
  // handleError **没有 URL 存在性检查** —— playbackUrl=null 时报 error 照样调 onUrlExpired() →
  // 父组件 video-detail.tsx:62 invalidateQueries → **白打一次后端**（没有 URL 就没有「过期」可言）。
  // 共享 hook 有这道守卫（useMediaUrlRefresh: `if (!url) return;`），本组件没有。
  //
  // 按本片开头立的规矩「**不变量才进网，缺陷不进网**」→ 这条不属于基线，它是下一片迁移要**修**的东西。
  // 故此处只记录、不断言；对应的正向断言在迁移片里（迁完才绿）。
  it.todo("playbackUrl 为空时 error 不该报过期 —— 当前会误报（第 3 个 bug），迁移片修");
});
