import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), replace: vi.fn() })
}));

// Mock auth context
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: { token: "t" }, ready: true, login: vi.fn(), logout: vi.fn() })
}));

// Mock useVideo hook
vi.mock("@/lib/api/hooks", () => ({
  useVideo: vi.fn()
}));

import { ApiError } from "@/lib/api/client";
import { useVideo } from "@/lib/api/hooks";
import { videoKeys } from "@/lib/api/keys";
import { VideoDetail } from "./video-detail";
import { copy } from "@/lib/copy";
import type { Mock } from "vitest";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("VideoDetail", () => {
  it("renders loading state while isLoading", () => {
    (useVideo as Mock).mockReturnValue({ data: undefined, error: null, isLoading: true });
    render(<VideoDetail id="v1" />, { wrapper });
    expect(screen.getByText("加载中…")).toBeInTheDocument();
  });

  it("nf-1: renders dedicated not-found empty state for 404 ApiError", () => {
    const err = new ApiError("Not Found", "not_found", 404);
    (useVideo as Mock).mockReturnValue({ data: undefined, error: err, isLoading: false });
    render(<VideoDetail id="missing" />, { wrapper });
    expect(screen.getByText(copy.detail.notFound)).toBeInTheDocument();
    expect(screen.getByText(copy.detail.back)).toBeInTheDocument();
  });

  it("nf-1: renders not-found state for error.code === 'not_found' regardless of status", () => {
    const err = new ApiError("Cross-tenant", "not_found", 403);
    (useVideo as Mock).mockReturnValue({ data: undefined, error: err, isLoading: false });
    render(<VideoDetail id="cross-tenant" />, { wrapper });
    expect(screen.getByText(copy.detail.notFound)).toBeInTheDocument();
  });

  it("renders video detail when data is present", () => {
    (useVideo as Mock).mockReturnValue({
      data: {
        id: "v1",
        status: "done",
        progress: 100,
        topic: "测试视频",
        script: "今天的主题是测试",
        voice_id: "v-zhixing",
        aspect_ratio: "9:16",
        subtitle_enabled: true,
        playback_url: "https://mock.local/v.mp4",
        download_url: "https://mock.local/v.mp4?dl=1",
        thumbnail_url: null,
        duration_ms: 30000,
        apply_visible_label: true,
        created_at: "2026-06-18T00:00:00Z"
      },
      error: null,
      isLoading: false
    });
    render(<VideoDetail id="v1" />, { wrapper });
    expect(screen.getByText("测试视频")).toBeInTheDocument();
    expect(screen.getByText("今天的主题是测试")).toBeInTheDocument();
    expect(screen.getByText(copy.detail.download)).toBeInTheDocument();
    // LABEL-UI-0001：完成产物处显示「已含 AI 生成标识」知情提示。
    expect(screen.getByText(copy.label.productNotice)).toBeInTheDocument();
    // PUBLISH-UI-0001：成片处「发布」入口 → /publish 带 source_kind+source_task_id。
    expect(screen.getByRole("link", { name: new RegExp(copy.publish.entry) })).toHaveAttribute(
      "href",
      "/publish?source_kind=video&source_task_id=v1"
    );
  });

  it("renders an <img> result for a photo task (mode=photo) with a download link", () => {
    (useVideo as Mock).mockReturnValue({
      data: {
        id: "p1",
        status: "done",
        progress: 100,
        mode: "photo",
        topic: "白色大理石上的香水瓶",
        script: null,
        voice_id: null,
        aspect_ratio: null,
        subtitle_enabled: null,
        playback_url: "https://mock.local/p.png",
        download_url: "https://mock.local/p.png?dl=1",
        thumbnail_url: null,
        created_at: "2026-06-23T00:00:00Z"
      },
      error: null,
      isLoading: false
    });
    render(<VideoDetail id="p1" />, { wrapper });
    expect(screen.getByRole("img")).toHaveAttribute("src", "https://mock.local/p.png");
    expect(screen.getByText(copy.detail.downloadImage)).toBeInTheDocument();
  });

  it("shows friendly copy for a failed photo (never the raw error_message)", () => {
    (useVideo as Mock).mockReturnValue({
      data: {
        id: "p1",
        status: "failed",
        progress: 0,
        mode: "photo",
        topic: "一只猫",
        script: null,
        voice_id: null,
        aspect_ratio: null,
        subtitle_enabled: null,
        error_code: "IMAGE_MODERATION_BLOCKED",
        error_message: 'Error code: 400 - {"error":{"code":"moderation_blocked"}}',
        created_at: "2026-06-25T00:00:00Z"
      },
      error: null,
      isLoading: false
    });
    render(<VideoDetail id="p1" />, { wrapper });
    expect(screen.getByText(copy.errors.imageModeration)).toBeInTheDocument();
    expect(screen.queryByText(/Error code: 400/)).not.toBeInTheDocument();
  });

  // VIDEO-ERR-MAP-UI：视频失败详情页也走友好映射（照抄图片线），不露裸 error_message。
  it("shows friendly copy for a failed video (never the raw error_message)", () => {
    (useVideo as Mock).mockReturnValue({
      data: {
        id: "vf1",
        status: "failed",
        progress: 0,
        mode: "avatar_talk",
        topic: "口播失败",
        script: null,
        voice_id: null,
        aspect_ratio: null,
        subtitle_enabled: null,
        error_code: "VIDEO_TIMEOUT",
        error_message: "Error code: 504 - upstream timeout (raw)",
        created_at: "2026-06-25T00:00:00Z"
      },
      error: null,
      isLoading: false
    });
    render(<VideoDetail id="vf1" />, { wrapper });
    expect(screen.getByText(copy.errors.videoTimeout)).toBeInTheDocument();
    expect(screen.queryByText(/Error code: 504/)).not.toBeInTheDocument();
  });

  it("P2-3: renders without crashing when backend nullable fields are null", () => {
    (useVideo as Mock).mockReturnValue({
      data: {
        id: "v2",
        status: "running",
        progress: 40,
        topic: null,
        script: null,
        voice_id: null,
        aspect_ratio: null,
        subtitle_enabled: null,
        thumbnail_url: null,
        created_at: "2026-06-18T00:00:00Z"
      },
      error: null,
      isLoading: false
    });
    render(<VideoDetail id="v2" />, { wrapper });
    // null topic falls back to a placeholder; no crash, no subtitle block.
    expect(screen.getByText("未命名视频")).toBeInTheDocument();
    // LABEL-UI-0001 负向：未完成(running)无产物 → 不显「已含 AI 生成标识」(锁住条件分支)。
    expect(screen.queryByText(copy.label.productNotice)).not.toBeInTheDocument();
  });
});

// ── MEDIA-URL-REFRESH-CONVERGE-0001 · **第 2 片：补网**（本 commit 零产品代码改动）─────────────
//
// 任务包硬门 1「补网在前、迁移在后」要求的是**两处**：VideoPlayer（第 1 片 46f4195d 已补）与
// 本文件 photo 分支的 `<img onError={handleUrlExpired}>`（video-detail.tsx:152）。后者是**裸接** ——
// 连哨兵都没有，且**零 URL 过期测试**（上面 9 条一条都没碰 onError）。下一片要把它迁进
// useMediaUrlRefresh，无网迁移 = 拿用户的详情页赌运气。
//
// ⚠️ 照第 1 片立的规矩：**不变量才进网，缺陷不进网**。
// 这里钉的是「重取动作确实接上了」（迁移前后都必须成立）；**有意不钉**「连报多次 → 打后端多次」——
// 那是裸接的缺陷，是下一片要修的东西。把缺陷钉进网，下一片就得改测试来修 bug。

/**
 * 造一个 invalidateQueries 可观测的 client + wrapper。
 *
 * 为什么不改上面那个既有 `wrapper`：① 它每次被 React 调用都新建 client（rerender 即换一个），
 * 而本组的断言要跨 render 累计调用次数；② 更重要的是**零回归最好的证据是「我根本没动它，而它还绿着」** ——
 * 上面 9 条既有测试原样不动。
 */
function makeSpyWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  function spyWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return { spyWrapper, invalidate };
}

const photoDone = {
  id: "p1",
  status: "done",
  progress: 100,
  mode: "photo",
  topic: "白色大理石上的香水瓶",
  script: null,
  voice_id: null,
  aspect_ratio: null,
  subtitle_enabled: null,
  playback_url: "https://mock.local/p.png",
  download_url: null,
  thumbnail_url: null,
  created_at: "2026-06-23T00:00:00Z"
};

const videoDone = {
  id: "v1",
  status: "done",
  progress: 100,
  mode: "avatar_talk",
  topic: "测试视频",
  script: null,
  voice_id: null,
  aspect_ratio: null,
  subtitle_enabled: null,
  playback_url: "https://mock.local/v.mp4",
  download_url: null,
  thumbnail_url: null,
  created_at: "2026-06-18T00:00:00Z"
};

describe("VideoDetail · presign 失效重取（迁移前基线 · 不变量）", () => {
  it("photo 的 <img> 报 error → 重取本视频详情（防线接上了，不是摆设）", () => {
    (useVideo as Mock).mockReturnValue({ data: photoDone, error: null, isLoading: false });
    const { spyWrapper, invalidate } = makeSpyWrapper();
    render(<VideoDetail id="p1" />, { wrapper: spyWrapper });

    fireEvent.error(screen.getByRole("img"));
    // 重取的必须是**这个** id 的详情：错 key = 重取了别的东西，UI 里的旧 URL 一动不动。
    expect(invalidate).toHaveBeenCalledWith({ queryKey: videoKeys.detail("p1") });
  });

  it("video 分支：VideoPlayer 的 onUrlExpired 接到同一条重取动作，且同一 URL 连报多次只重取一次", () => {
    (useVideo as Mock).mockReturnValue({ data: videoDone, error: null, isLoading: false });
    const { spyWrapper, invalidate } = makeSpyWrapper();
    render(<VideoDetail id="v1" />, { wrapper: spyWrapper });

    const video = document.querySelector("video")!;
    fireEvent.error(video);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: videoKeys.detail("v1") });

    // 浏览器对同一 src 会连发 error。这条在迁移前由 VideoPlayer 的手抄哨兵挡住，迁移后由共享 hook 挡住 ——
    // **换实现不换行为**，故它是不变量，进网。
    fireEvent.error(video);
    fireEvent.error(video);
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  // 🔴 photo 分支缺的正是上面 video 分支有的那条：`<img>` 裸接 handleUrlExpired、零哨兵 →
  // 同一 URL 连发 3 个 error 就真打 3 次后端；对象已删（BE 每次都签得出新 URL、个个 404）时更是无限重取。
  // 按「缺陷不进网」→ 此处只记录、不断言；正向断言在迁移片里（迁完才绿）。
  // → **已在下面的第 3 片 describe 里实现**，故 todo 摘除。
});

// ── MEDIA-URL-REFRESH-CONVERGE-0001 · **第 3 片：迁移**（photo 的 <img> 迁入共享哨兵）───────────
// 上面「补网」那组的两条不变量迁移后原样全绿 = 零回归证据。本组是上一片 it.todo 的兑现。
describe("VideoDetail · photo 的 <img> 迁入共享哨兵后", () => {
  it("同一 URL 连报多次 → 只重取一次（裸接时每个 error 都真打一次后端）", () => {
    (useVideo as Mock).mockReturnValue({ data: photoDone, error: null, isLoading: false });
    const { spyWrapper, invalidate } = makeSpyWrapper();
    render(<VideoDetail id="p1" />, { wrapper: spyWrapper });

    const img = screen.getByRole("img");
    fireEvent.error(img);
    fireEvent.error(img);
    fireEvent.error(img);
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  // 🔴 死循环刹车：这是整条 photo 链路上原先**完全没有**的东西。
  // 裸接实现在这个场景下会打后端 8 次（乃至无限）—— 拆掉 hook 的封顶，这条必红。
  it("🔴 对象已删、BE 每次签出新 URL 但个个失效 → 连续重取封顶，不无限打后端", async () => {
    const { spyWrapper, invalidate } = makeSpyWrapper();
    (useVideo as Mock).mockReturnValue({
      data: { ...photoDone, playback_url: "https://mock.local/gone.png?sig=0" },
      error: null,
      isLoading: false
    });
    const { rerender } = render(<VideoDetail id="p1" />, { wrapper: spyWrapper });

    for (let i = 1; i <= 8; i++) {
      (useVideo as Mock).mockReturnValue({
        data: { ...photoDone, playback_url: `https://mock.local/gone.png?sig=${i}` },
        error: null,
        isLoading: false
      });
      rerender(<VideoDetail id="p1" />);
      fireEvent.error(screen.getByRole("img"));
      // FIX1：每轮之间隔着一次真实的重取往返 —— 没有往返就没有新 URL，也就无所谓「新 URL 又失效」。
      // 同步连发是同一次失效的重复上报，那正是在飞门控该挡住的东西。
      await act(async () => {});
    }

    expect(invalidate).toHaveBeenCalledTimes(2);
  });

  // 重取真的把新 URL 喂进了 UI（详情页数据源是 useVideo query 派生，不是冻结快照）→ 防线导电。
  it("重取拿回新 URL → <img src> 真的跟着换（数据源是 query 派生，重取才有意义）", () => {
    (useVideo as Mock).mockReturnValue({ data: photoDone, error: null, isLoading: false });
    const { spyWrapper } = makeSpyWrapper();
    const { rerender } = render(<VideoDetail id="p1" />, { wrapper: spyWrapper });

    expect(screen.getByRole("img")).toHaveAttribute("src", "https://mock.local/p.png");
    fireEvent.error(screen.getByRole("img"));

    (useVideo as Mock).mockReturnValue({
      data: { ...photoDone, playback_url: "https://mock.local/p.png?sig=fresh" },
      error: null,
      isLoading: false
    });
    rerender(<VideoDetail id="p1" />);
    expect(screen.getByRole("img")).toHaveAttribute("src", "https://mock.local/p.png?sig=fresh");
  });
});

// ── MEDIA-URL-REFRESH-CONVERGE-0001 · **FIX4：跨视频不继承封顶（「第 10 处」承重）** ──────────────
//
// 本组件是 `/videos/[id]` 页，`page.tsx` 渲染 `<VideoDetail id={id}/>` **无 `key={id}`** → Next.js 换 param
// **不 remount** → 预算 Map 跨 `/videos/a`→`/videos/b` 持续存在。上一版用**裸根 scope**（mediaKey ""），
// 视频 a 耗尽的封顶会被 b 继承（与本包 P1「output:index 缺命名空间」同型）。FIX4 改按 video id 切独立域
// （`forKey(id)`）。testing-library 的 rerender 保持**同一组件实例**（换 id prop、不 remount），正是 Next
// 换 param 的忠实模型 —— Map 会跨 a/b 存活，这条测试就跑在它的存活期边界上。
describe("VideoDetail · FIX4 跨视频（同实例、换 id → 预算不继承）", () => {
  const roundTrip = () => act(async () => {});
  const gone = (id: string, sig: string) => ({
    ...photoDone,
    id,
    playback_url: `https://mock.local/gone-${id}.png?sig=${sig}`
  });

  it("🔴 视频 a 耗尽封顶后切到 b、b 首帧失败 → b 仍能自救（不继承 a 的毒预算）", async () => {
    const { spyWrapper, invalidate } = makeSpyWrapper();

    // 视频 a：对象已删，两轮把它的封顶耗满（每轮换新 URL，避免被去重挡下）。
    (useVideo as Mock).mockReturnValue({ data: gone("a", "0"), error: null, isLoading: false });
    const { rerender } = render(<VideoDetail id="a" />, { wrapper: spyWrapper });
    fireEvent.error(screen.getByRole("img")); // invalidate #1
    await roundTrip();
    (useVideo as Mock).mockReturnValue({ data: gone("a", "1"), error: null, isLoading: false });
    rerender(<VideoDetail id="a" />);
    fireEvent.error(screen.getByRole("img")); // invalidate #2 → a 封顶
    await roundTrip();
    expect(invalidate).toHaveBeenCalledTimes(2);
    expect(invalidate).toHaveBeenLastCalledWith({ queryKey: videoKeys.detail("a") });

    // 用户切到视频 b（同一 VideoDetail 实例、换 id prop）。b 首帧就瞬时失败（error-before-load）。
    (useVideo as Mock).mockReturnValue({ data: gone("b", "0"), error: null, isLoading: false });
    rerender(<VideoDetail id="b" />);
    fireEvent.error(screen.getByRole("img"));
    await roundTrip();

    // 🔴 承重点：b 有自己的命名空间（forKey("b")）→ 干净预算 → 第 3 次重取，且重取的是 b 的详情。
    // 裸根 scope 时：b 落在 "" 上，那格被 a 耗到封顶 → suppression → 停在 2（b 刷不出来）。
    expect(invalidate).toHaveBeenCalledTimes(3);
    expect(invalidate).toHaveBeenLastCalledWith({ queryKey: videoKeys.detail("b") });
  });
});
