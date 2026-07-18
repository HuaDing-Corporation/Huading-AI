import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

// ── MEDIA-URL-REFRESH-CONVERGE-0001 · FIX3 · P1 的**真实网络承重** ────────────────────────────
//
// 🔴 钉的是 **mediaKey 的「跨重取稳定」**，不是「整套图有防线」（那是第 5 片 / FIX2 已钉的，别重复钉）。
//
// 场景来自 Codex B 的临时反证，**是本 hook 存在的理由本身**：「批量生成 N 张、陆续完成」。
// 白底图/模特图/封面的一「套」是按 `batch_id` 聚合**多个任务**的（image_history.py:100 的 coalesce），
// 而 BE 只查 `status=="done"` 的任务（:84）、按 created_at 排序后**重新 enumerate**（:472）。
// 排序键固定 → **相对顺序**稳定；但 index 是**绝对位置**，成员资格随完成情况变 →
// **created_at 更早、完成更晚的兄弟一旦完成就插到前面，同一张坏图的 index 从 0 漂到 1、2**。
// 上一版拿 index 当 mediaKey → 每漂一次就换到一个**全新的失败预算** → 绕过「最多重取 2 次」。
//
// ⚠️ 与 history-set-dialog.test.tsx 的分工（照 history-grid 那套）：那边 mock hooks 层测渲染/导电（快、稳），
//    这边不 mock hooks —— 真实 useHistoryImageSet + 真实 QueryClient，只把最底层 adapter 换成 spy，
//    数的是**它**被调了几次 = 真实打了几次后端。
//    数 `refetch` 的 spy **不行**：TanStack 默认 cancelRefetch:true，两次并发 refetch 会让 queryFn 被调 3 次
//    —— 那个数字系统性地偏离真实请求数（history-grid.network.test.tsx:6-21 记的就是这个教训）。

const adapter = vi.hoisted(() => ({ getHistoryImageSet: vi.fn() }));
vi.mock("@/lib/api/history-images", async (importOriginal) => {
  // 只替换 fetch 函数，保留真实的 historyImageSetMediaKey / historyImageDimensions / 类型 ——
  // **被测的正是 historyImageSetMediaKey**，把它 mock 掉这条测试就什么也不测了。
  const actual = await importOriginal<typeof import("@/lib/api/history-images")>();
  return { ...actual, ...adapter };
});
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: { token: "t" }, ready: true, login: vi.fn(), logout: vi.fn() })
}));

import { HistorySetDialog } from "./history-set-dialog";
import type { HistoryItem } from "@/lib/api/history-images";

// 列表项：模特图的一「套」= 一个 batch（BE 按 batch_id 聚合多任务 → 正是会漂的那条路径）。
const ITEM: HistoryItem = {
  id: "batch-1",
  category: "ecom_model",
  title: "模特图 · 批量（3 张）",
  cover_url: "https://cdn/cover.png",
  created_at: "2026-07-10T12:00:00Z",
  status: "ready",
  item_count: 3
};

/** 坏图 = 对象已删：BE 每轮都能签出**新** URL（sig 换新，去重挡不住），但每个都 404。 */
const GONE = (sig: string) => `https://cdn/gone.png?sig=${sig}`;

/**
 * 一次 BE 响应。`done` 按**完成顺序**给出已完成的任务；BE 把它们按 created_at 重排后 enumerate。
 * 批次里三个任务的 created_at 顺序固定为 t-a < t-b < t-bad（`ORDER` 就是它），
 * 而**完成**顺序是 t-bad → t-b → t-a（坏图先完成）—— 于是坏图的 index 一路 0 → 1 → 2。
 */
const ORDER = ["t-a", "t-b", "t-bad"];
const urlOf = (taskId: string, sig: string) => (taskId === "t-bad" ? GONE(sig) : `https://cdn/${taskId}.png?sig=${sig}`);
const responseFor = (done: string[], sig: string) => {
  const tasks = ORDER.filter((id) => done.includes(id)); // BE：只查 done，按 created_at 排序
  return {
    id: "batch-1",
    category: "ecom_model",
    created_at: "2026-07-10T12:00:00Z",
    status: "ready",
    // BE：`for index, task in enumerate(tasks)` —— index 是**这一次响应里**的位置
    items: tasks.map((id, index) => ({ index, download_url: urlOf(id, sig), width: 1024, height: 1536 })),
    // BE：meta["task_ids"] = [task.id for task in tasks] —— 与 items **同序、同出一份 tasks**
    meta: { task_ids: tasks, style_id: "studio" }
  };
};

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** 坏图靠**它自己的 URL**认，不能靠 alt/下标 —— tile 的 alt 是 `item.index + 1`（tile:33），**它跟着漂**。 */
const badImg = () => screen.getAllByRole("img").find((img) => img.getAttribute("src")?.includes("gone.png"))!;
const healthyImgs = () => screen.getAllByRole("img").filter((img) => !img.getAttribute("src")?.includes("gone.png"));

afterEach(() => vi.clearAllMocks());

describe("HistorySetDialog · 真实 network 次数（FIX3：mediaKey 必须跨重取稳定）", () => {
  it("🔴 批量陆续完成 → 同一坏图 index 漂 0→1→2 → 封顶仍是 2 次（预算不许跟着 index 换新）", async () => {
    // 第 1 轮：只有坏图完成了 → 它是整套里唯一一张，index 0。
    adapter.getHistoryImageSet.mockResolvedValue(responseFor(["t-bad"], "1"));
    wrap(<HistorySetDialog item={ITEM} onClose={() => {}} />);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(1));
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(1); // 初次加载
    expect(badImg().getAttribute("src")).toBe(GONE("1"));

    /**
     * 坏图碎一次 → 重取 → 新一批 URL 落地（**必须等它真进 DOM**：否则下一轮 error 报的还是旧 URL，
     * 会被 hook 的「同一 URL 只报一次」挡掉 → 测试会因为**错误的原因**变绿）。
     * `nextDone` 模拟又一个兄弟完成 —— 它 created_at 更早 → 插到坏图**前面** → 坏图 index +1。
     */
    const breakBadAndAwaitFresh = async (nextDone: string[], sig: string) => {
      adapter.getHistoryImageSet.mockResolvedValue(responseFor(nextDone, sig));
      act(() => void fireEvent.error(badImg()));
      await waitFor(() => expect(badImg().getAttribute("src")).toBe(GONE(sig)));
      // 陆续完成的健康兄弟**真的加载出来了** —— 它们的成功不该替坏图清账（FIX2 的 P1-2 在此顺带承重）。
      act(() => healthyImgs().forEach((img) => fireEvent.load(img)));
    };

    // 第 2 轮：t-b 也完成了（created_at 比坏图早）→ 坏图 index 0 → **1**
    await breakBadAndAwaitFresh(["t-b", "t-bad"], "2");
    expect(screen.getAllByRole("img")).toHaveLength(2);
    expect(badImg().getAttribute("alt")).toBe("历史图片第 2 张"); // 位置真的漂了（alt = index+1）
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(2); // 1 初次 + 1 重取

    // 第 3 轮：t-a 也完成了 → 坏图 index 1 → **2**
    await breakBadAndAwaitFresh(["t-a", "t-b", "t-bad"], "3");
    expect(screen.getAllByRole("img")).toHaveLength(3);
    expect(badImg().getAttribute("alt")).toBe("历史图片第 3 张"); // 又漂了一格
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(3); // 1 初次 + 2 重取 = **封顶**

    // 第 4 轮：坏图第三次碎 —— 它已经连续失败 2 次，**同一个** mediaKey（task:t-bad）→ 封顶生效，不再重取。
    adapter.getHistoryImageSet.mockResolvedValue(responseFor(["t-a", "t-b", "t-bad"], "4"));
    act(() => void fireEvent.error(badImg()));
    await act(async () => {});

    // 🔴 承重点：仍是 3。用 index 当 key 时，坏图这三次分别落在 "0"/"1"/"2" 三个**全新**预算上 →
    // 每次都放行 → 4 次（批量 20 张时可放大到 20 次 —— 正是本包要封的顶）。
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(3);
  });

  // 🔴 方向②（反方向，后果不对称）：**A 继承 B 的毒预算 → 刷不了**。
  // 方向①（上一条）钉的是「坏图漂到新 index → 拿到新预算 → 多刷」（后果=浪费一次后端）。
  // 这条钉反向：坏图 B 在某个 index 把预算耗到封顶后**漂走**，一张健康图 A 漂进那个槽 → 若按 index 记账，
  // A 继承了 B 耗尽的预算 → A 自己失败时**刷不出来**（后果=用户对着碎图，功能失效，比浪费严重）。
  //
  // ⚠️ 这条**必须用 error-before-load**（A 首帧就失败、还没 load 过），不能用「load 成功后再过期」。
  //    实测（受控探针）：后者在 index 变异下**仍绿** —— 因为 A 的 onLoad（FIX2 加的清零）会先把继承来的毒
  //    清掉，suppression 根本触发不了；而每次 refetch 都重新 presign 整套（image_history.py:480）→ 刚漂进来
  //    的图 URL 是新鲜的、会 load。所以「过期-after-load」这条路被 FIX2 自己遮住了，抓不到本 bug。
  //    error-before-load 是真实可达的：对象存储最终一致性 / CDN 边缘未命中 → 刚写完的图**首次访问就 404**，
  //    retry 才成 —— 那一帧它 error 而从未 load。**别把它"简化"成 load-后-error，那会变假绿。**
  it("🔴 坏图漂走后、健康图漂进它耗尽的 index 槽、首帧瞬时失败 → 仍能自救重取（不继承毒预算）", async () => {
    // created 顺序：t-heal 最早、t-bad 后。完成顺序：t-bad 先（1-2 轮只它 done），t-heal 后（第 3 轮才 done）。
    // → t-bad 先独占 index 0 把预算耗满，t-heal 一完成就按 created_at 插到**前面** → t-bad 漂到 index 1、
    //   t-heal 占下 index 0（那个被 t-bad 耗尽的槽）。
    const ORDER2 = ["t-heal", "t-bad"];
    const url2 = (id: string, sig: string) =>
      id === "t-bad" ? `https://cdn/gone.png?sig=${sig}` : `https://cdn/heal.png?sig=${sig}`;
    const resp2 = (done: string[], sig: string) => {
      const tasks = ORDER2.filter((id) => done.includes(id));
      return {
        id: "batch-1",
        category: "ecom_model",
        created_at: "2026-07-10T12:00:00Z",
        status: "ready",
        items: tasks.map((id, index) => ({ index, download_url: url2(id, sig), width: 1024, height: 1536 })),
        meta: { task_ids: tasks }
      };
    };
    const badBy = () => screen.getAllByRole("img").find((i) => i.getAttribute("src")?.includes("gone.png"))!;
    const healBy = () => screen.getAllByRole("img").find((i) => i.getAttribute("src")?.includes("heal.png"))!;

    adapter.getHistoryImageSet.mockResolvedValue(resp2(["t-bad"], "1"));
    wrap(<HistorySetDialog item={ITEM} onClose={() => {}} />);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(1));
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(1);

    // 两轮把 t-bad 在 index 0 上的预算耗到封顶（consecutive → 2）。
    adapter.getHistoryImageSet.mockResolvedValue(resp2(["t-bad"], "2"));
    act(() => void fireEvent.error(badBy()));
    await waitFor(() => expect(badBy().getAttribute("src")).toBe(url2("t-bad", "2")));
    // 第 2 次失效的重取里 t-heal 也完成了 → 它插到前面，t-bad 漂到 index 1。
    adapter.getHistoryImageSet.mockResolvedValue(resp2(["t-heal", "t-bad"], "3"));
    act(() => void fireEvent.error(badBy()));
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(3); // 1 初次 + 2（t-bad 在 index0 耗满）
    expect(healBy().getAttribute("alt")).toBe("历史图片第 1 张"); // t-heal 真的占下了 index 0 那个槽

    // t-heal 首帧就瞬时失败（最终一致性/CDN 未命中）—— 它**从未 load 过**，毒无从被清。
    adapter.getHistoryImageSet.mockResolvedValue(resp2(["t-heal", "t-bad"], "4"));
    act(() => void fireEvent.error(healBy()));
    await act(async () => {});

    // 🔴 承重点：健康图有自己的身份（task:t-heal）→ 干净预算 → 能自救 → 第 4 次重取。
    // 按 index 记账时：t-heal 落在 output:0，那格被 t-bad 耗到 2 → suppression → 只有 3（healthy 刷不出来）。
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(4);
  });

  it("整套里两张各自坏 → 各自独立爬到封顶，互不吃对方的机会（单射：不同媒体 ≠ 同一个 key）", async () => {
    // 判据的另一半：稳定之外还要**单射**。两张坏图若共享一个 key，合计只会重取 2 次而不是各自 2 次。
    const twoBad = (sig: string) => ({
      id: "batch-1",
      category: "ecom_model",
      created_at: "2026-07-10T12:00:00Z",
      status: "ready",
      items: [
        { index: 0, download_url: `https://cdn/gone-a.png?sig=${sig}`, width: 1024, height: 1536 },
        { index: 1, download_url: `https://cdn/gone-b.png?sig=${sig}`, width: 1024, height: 1536 }
      ],
      meta: { task_ids: ["t-x", "t-y"] }
    });
    adapter.getHistoryImageSet.mockResolvedValue(twoBad("1"));
    wrap(<HistorySetDialog item={ITEM} onClose={() => {}} />);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(1);

    // 两轮「两张一起碎 → 重取 → 新 URL 仍全碎」。同一 tick 的两个 error 由**在飞门控**合流成 1 次重取，
    // 但两张的 consecutive **各自**涨 → 两轮后双双到顶 → 第三轮整域静默。
    for (const sig of ["2", "3", "4"]) {
      adapter.getHistoryImageSet.mockResolvedValue(twoBad(sig));
      act(() => screen.getAllByRole("img").forEach((img) => fireEvent.error(img)));
      await act(async () => {});
    }

    // 1 初次 + 2 重取（合流后每轮 1 次，封顶 2 轮）= 3。
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(3);
  });
});

// ── FIX4 · P1：mediaKey 缺 job/set 命名空间（跨 job 碰撞）───────────────────────────────────────
//
// 预算 Map 活在 HistorySetDialog **组件实例**里，跨「打开 set A → 关 → 打开 set B」持续存在
// （HistoryGrid 切换详情集时不卸载它）。ecom_detail 无 task_ids → key 落到 output:index，而
// **`(job_id, index)` 唯一 ≠ index 全局唯一** → 不带命名空间时，job-a 的 output:0 和 job-b 的 output:0
// 撞进同一份预算 → B 继承 A 耗尽的封顶。这条钉的正是判据第三条（命名空间）+ 量程（Map 存活期）。
describe("HistorySetDialog · FIX4 跨 job（同实例、换 set → 预算不继承）", () => {
  it("🔴 job A 的 output:0 耗尽封顶后切到 job B、相同 output index、B 首帧失败 → B 仍能自救", async () => {
    // ecom_detail：无 task_ids → key = output:index。每次 adapter 调用给全新 URL（sig 递增）→ 去重挡不住。
    let sig = 0;
    const respFor = (id: string) => ({
      id,
      category: "ecom_detail",
      created_at: "2026-07-10T12:00:00Z",
      status: "completed",
      items: [{ index: 0, download_url: `https://cdn/gone-${id}.png?sig=${++sig}`, width: 1254, height: 1254 }],
      meta: {} // ← 无 task_ids：ecom_detail 形态
    });
    adapter.getHistoryImageSet.mockImplementation((_cat: string, id: string) => Promise.resolve(respFor(id)));

    // 同一个 client + 同一个 HistorySetDialog 实例，只换 item（= Next 换 param 不 remount 的忠实模型）。
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = (item: HistoryItem) => (
      <QueryClientProvider client={client}>
        <HistorySetDialog item={item} onClose={() => {}} />
      </QueryClientProvider>
    );
    const itemA: HistoryItem = { ...ITEM, id: "job-a", category: "ecom_detail" };
    const itemB: HistoryItem = { ...ITEM, id: "job-b", category: "ecom_detail" };

    const { rerender } = render(view(itemA));
    await waitFor(() => expect(screen.getByRole("img").getAttribute("src")).toContain("gone-job-a"));
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(1); // job-a 初次加载

    // 两轮把 job-a 的 output:0 耗满（error → 重取 → 新 URL 落地）。
    for (let round = 0; round < 2; round++) {
      const prev = screen.getByRole("img").getAttribute("src");
      fireEvent.error(screen.getByRole("img"));
      await waitFor(() => expect(screen.getByRole("img").getAttribute("src")).not.toBe(prev)); // 新 URL 进来
    }
    await act(async () => {}); // 确保最后一次重取的在飞门控落定
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(3); // 1 初次 + 2 重取（封顶）

    // 切到 job-b（同一实例、换 item）。job-b 相同 output index 0、首帧就失败（error-before-load）。
    rerender(view(itemB));
    await waitFor(() => expect(screen.getByRole("img").getAttribute("src")).toContain("gone-job-b"));
    const before = adapter.getHistoryImageSet.mock.calls.length; // 含 job-b 初次加载
    fireEvent.error(screen.getByRole("img"));
    await act(async () => {});

    // 🔴 承重点：job-b 的 output:0 带了 job-b 命名空间 → 干净预算 → B 触发了自己的重取（+1）。
    // 去命名空间时：B 落在 output:0（被 A 耗到封顶）→ suppression → B 不重取（+0）。
    expect(adapter.getHistoryImageSet.mock.calls.length).toBe(before + 1);
  });
});

// ── FIX4 · §三：畸形 / 部分 / 重复 task_ids → 停用刷新，不静默退回不稳定 index ────────────────────
//
// 数据存在性分派消掉了显式分类分支，但没消掉契约假设（仍假定 task_ids 完整/同长/非空/唯一）。
// photo 分支缺少合法且唯一的 task_id 时，退回 index = 重演 FIX3 的漂移。故：没有可信身份 → NO_REFRESH。
describe("HistorySetDialog · FIX4 §三（畸形 task_ids → 停用刷新）", () => {
  it("🔴 部分 task_ids（index 1 拿不到）→ 那张停用刷新；有合法 id 的那张照常刷新", async () => {
    adapter.getHistoryImageSet.mockResolvedValue({
      id: "batch-x",
      category: "ecom_model", // photo 形态：有 task_ids，index 不稳
      created_at: "2026-07-10T12:00:00Z",
      status: "completed",
      items: [
        { index: 0, download_url: "https://cdn/ok.png?sig=1", width: 1024, height: 1536 },
        { index: 1, download_url: "https://cdn/bad.png?sig=1", width: 1024, height: 1536 }
      ],
      meta: { task_ids: ["t-ok"] } // 只 1 个 → index 1 → undefined → 畸形/部分
    });
    wrap(<HistorySetDialog item={{ ...ITEM, id: "batch-x", category: "ecom_model" }} onClose={() => {}} />);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(1);

    const imgs = screen.getAllByRole("img"); // [index0(ok), index1(bad)]
    fireEvent.error(imgs[1]); // 畸形那张 → NO_MEDIA_URL_REFRESH → 不重取
    await act(async () => {});
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(1);

    fireEvent.error(imgs[0]); // 有合法 task_id 那张 → 正常重取（证明不是整体哑了）
    await act(async () => {});
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(2);
  });

  it("🔴 task_ids 含重复 id → 那两张都停用刷新（重复 ≠ 唯一，不可作身份）", async () => {
    adapter.getHistoryImageSet.mockResolvedValue({
      id: "batch-y",
      category: "ecom_model",
      created_at: "2026-07-10T12:00:00Z",
      status: "completed",
      items: [
        { index: 0, download_url: "https://cdn/d0.png?sig=1", width: 1024, height: 1536 },
        { index: 1, download_url: "https://cdn/d1.png?sig=1", width: 1024, height: 1536 }
      ],
      meta: { task_ids: ["dup", "dup"] } // 重复 → 两张都不唯一
    });
    wrap(<HistorySetDialog item={{ ...ITEM, id: "batch-y", category: "ecom_model" }} onClose={() => {}} />);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(1);

    act(() => screen.getAllByRole("img").forEach((img) => fireEvent.error(img)));
    await act(async () => {});
    expect(adapter.getHistoryImageSet).toHaveBeenCalledTimes(1); // 都没重取
  });
});
