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
//
// ── 第 3 片（迁移）在本文件**只做加法** ────────────────────────────────────────────────
// 上面那批基线断言**一字未动**、迁移后原样全绿 = 零回归证据（照 task-card 迁移时的同一结构）。
// 新增的 describe 在文件末尾：三个 bug 各有承重，其中第 3 个 bug 的 it.todo 就地实现。
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
  // → **已在下面的迁移片 describe 里实现**（"playbackUrl 为空 → error 不报过期"），故 todo 摘除。
});

// ── MEDIA-URL-REFRESH-CONVERGE-0001 · **第 3 片：迁移**（三个 bug 一次带走）──────────────────
//
// VideoPlayer 曾是全仓**最后一份手抄哨兵**。迁进 useMediaUrlRefresh 后，它身上三个 bug 一次带走：
//  ① 缺 URL 变更重置  → 下面「URL 换了 → 重新给一次机会」
//  ② 注释说 per render 实为 per mount → 注释是谎，删掉即修（**注释不是防线**，无从断言）
//  ③ 缺 URL 存在性守卫 → 下面「playbackUrl 为空 → error 不报过期」（原 it.todo 就地实现）
//
// 🔴 **本包最容易做砸的一处**（任务包 §四.1）：修 ① 的前提是「封顶」已经在。
// 单独补重置 → 对象已删时 BE 每次都签得出新 URL、个个 404 → error→重取→新 URL→error→…… **无限重取**；
// 原来缺的那半条恰好在充当粗糙的死循环刹车。
// **处理办法不是「记得先做封顶」，而是让顺序错不了**：重置与封顶在同一个 hook 里、同一次 commit 引入，
// 结构上不存在「重置已开、封顶未到」的中间态。下面两条一起进网 = 刹车与油门同时承重。
describe("VideoPlayer · 迁入共享哨兵后（三个 bug 各有承重）", () => {
  const URL_B = "https://cdn/a.mp4?sig=2";

  // bug ①：详情页刷新后新 URL 再过期 → 旧实现 expired.current 恒 true → 播放器哑死到组件卸载。
  it("🔴 URL 换了 → 重新给一次机会（旧实现刷新后就永久哑掉）", () => {
    const onUrlExpired = vi.fn();
    const { rerender } = render(
      <VideoPlayer playbackUrl={URL_A} downloadUrl={null} poster={null} onUrlExpired={onUrlExpired} />
    );
    const video = document.querySelector("video")!;

    fireEvent.error(video);
    expect(onUrlExpired).toHaveBeenCalledTimes(1);

    // 重取拿回新 URL，但它也过期了 → 必须还能再救（否则用户永远黑屏，且不知为何）
    rerender(<VideoPlayer playbackUrl={URL_B} downloadUrl={null} poster={null} onUrlExpired={onUrlExpired} />);
    fireEvent.error(video);
    expect(onUrlExpired).toHaveBeenCalledTimes(2);
  });

  // bug ③（原 it.todo 就地实现）：没有 URL 就没有「过期」可言，别空打后端。
  it("playbackUrl 为空 → error 不报过期（旧实现会误报，白打一次后端）", () => {
    const onUrlExpired = vi.fn();
    render(<VideoPlayer playbackUrl={null} downloadUrl={null} poster={null} onUrlExpired={onUrlExpired} />);

    fireEvent.error(document.querySelector("video")!);
    expect(onUrlExpired).not.toHaveBeenCalled();
  });

  // 🔴 死循环刹车 —— 这条是上面 bug ① 的**成立前提**：拆掉 hook 的封顶，这条必红。
  it("🔴 新 URL 仍失效 → 连续重取封顶，不无限循环（对象已删时 BE 能一直签出新 URL）", () => {
    const onUrlExpired = vi.fn();
    const { rerender } = render(
      <VideoPlayer playbackUrl="https://cdn/gone.mp4?sig=0" downloadUrl={null} poster={null} onUrlExpired={onUrlExpired} />
    );
    const video = document.querySelector("video")!;

    // 模拟「对象已被删除」：每轮 error → 重取 → BE 签出**新** URL → 仍然 404 → error → ……
    for (let i = 1; i <= 8; i++) {
      rerender(
        <VideoPlayer
          playbackUrl={`https://cdn/gone.mp4?sig=${i}`}
          downloadUrl={null}
          poster={null}
          onUrlExpired={onUrlExpired}
        />
      );
      fireEvent.error(video);
    }

    // 救得回来的一次就够；救不回来的最多浪费 2 次 —— 而不是打后端 8 次、80 次。
    expect(onUrlExpired).toHaveBeenCalledTimes(2);
  });

  // 封顶的另一半：不清零就会误伤长会话里的**正常**二次过期（播成功过 → 很久以后 TTL 到）。
  // ⚠️ 用 loadedMetadata 而非 load：React 的 media 事件表不含 load，<video onLoad> 根本接不上
  //    （实测：fireEvent.load(video) 静默不触发）。这也是 hook 注释里选 onLoadedMetadata 的原因。
  it("播成功过之后再过期 → 仍能再救（成功即清零，封顶不误伤正常的二次过期）", () => {
    const onUrlExpired = vi.fn();
    const { rerender } = render(
      <VideoPlayer playbackUrl={URL_A} downloadUrl={null} poster={null} onUrlExpired={onUrlExpired} />
    );
    const video = document.querySelector("video")!;

    fireEvent.error(video);
    rerender(<VideoPlayer playbackUrl={URL_B} downloadUrl={null} poster={null} onUrlExpired={onUrlExpired} />);
    fireEvent.error(video);
    expect(onUrlExpired).toHaveBeenCalledTimes(2); // 已到封顶

    // 第 3 个 URL 真的播起来了（拿到元数据 = 这个 URL 签得开、取得到）→ 预算清零
    rerender(
      <VideoPlayer playbackUrl="https://cdn/a.mp4?sig=3" downloadUrl={null} poster={null} onUrlExpired={onUrlExpired} />
    );
    fireEvent.loadedMetadata(video);

    // 很久以后它自己过期了 → 还能再救（若不清零，这里会哑 → 用户白等）
    fireEvent.error(video);
    expect(onUrlExpired).toHaveBeenCalledTimes(3);
  });
});
