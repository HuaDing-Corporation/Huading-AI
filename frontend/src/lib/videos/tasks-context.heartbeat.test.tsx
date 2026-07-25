import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

// GEN-HEARTBEAT-UI-0001 · 心跳承重。
//
// 本包存在的全部理由（Cowork 读码坐实、我复核属实）：applyEvent 的 stall 判据是
//   `if (next.progress > m.lastPct || step !== m.lastStep) { m.lastProgressAt = Date.now(); ... }`
// **只认「百分比真的前进」或「step 变了」**（那是故意的，防卡死任务靠重复事件续命）。
// → BE 发的心跳（progress/step 都不变）会被这条判据**整帧忽略**，27 分钟死寂照旧。
// 所以「后端发了心跳前端就自动好了」是错的，前端必须显式识别 heartbeat_at。
//
// 与 tasks-context.test.tsx 同口径：mock 掉常量成小值，用 fake timers 逼看门狗在测试内跳闸。
vi.mock("@/lib/api/videos", () => ({
  listVideos: vi.fn().mockResolvedValue([]),
  createVideo: vi.fn().mockResolvedValue({ id: "t1", status: "queued" }),
  getVideo: vi.fn().mockResolvedValue({}),
  streamVideoEvents: vi.fn().mockResolvedValue(undefined)
}));
let mockSession: { token: string } | null = { token: "t" };
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: mockSession, ready: true, login: vi.fn(), logout: vi.fn() })
}));
vi.mock("@/lib/sse/constants", async (orig) => ({
  ...(await orig<typeof import("@/lib/sse/constants")>()),
  STALL_MS: 50,
  HARD_CAP_MS: 10_000,
  POLL_MS: 10_000
}));

import { streamVideoEvents } from "@/lib/api/videos";

import { VideoTasksProvider, useVideoTasks } from "./tasks-context";

/** 暴露 status + progress + statusLabel：门2 要断言百分比**逐字不变**，故三者都露出来。 */
function Harness() {
  const { tasks, createAndTrack } = useVideoTasks();
  const t = tasks[0];
  return (
    <div>
      <button onClick={() => void createAndTrack({ topic: "x", voice_id: "v", avatar_asset_id: "a" }, "x")}>go</button>
      <span data-testid="status">{t?.status ?? "-"}</span>
      <span data-testid="progress">{t?.progress ?? "-"}</span>
      <span data-testid="label">{t?.statusLabel ?? "-"}</span>
      <span data-testid="beat">{t?.heartbeatAt == null ? "none" : "seen"}</span>
    </div>
  );
}

/** 捕获 SSE 的 onMessage，由测试体逐帧驱动（流永不 resolve = 后端仍在跑）。 */
let emit: ((e: unknown) => void) | null = null;
function mountAndStart() {
  (streamVideoEvents as Mock).mockImplementation(async (_id: string, onMessage: (e: unknown) => void) => {
    emit = onMessage;
    await new Promise(() => {});
  });
  return render(
    <VideoTasksProvider>
      <Harness />
    </VideoTasksProvider>
  );
}

const RUN_30 = { status: "running", progress: 30, step: null };
/** 心跳帧：**逐字照抄** progress/step，只多一个 heartbeat_at（冻结契约 §四）。 */
const beat = () => ({ ...RUN_30, heartbeat_at: new Date().toISOString() });

describe("GEN-HEARTBEAT-UI-0001 · 心跳识别（tasks-context）", () => {
  beforeEach(() => {
    mockSession = { token: "t" };
    emit = null;
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  // 🔴 门1（本包价值的直接证明）：30% 之后长时间**只有心跳**（百分比一动不动）→ 不得被标 failed。
  // 变异：删掉 tasks-context.tsx applyEvent 里的 `else if (beat)` 分支（回到"只认真实前进"）→ 本条必红。
  it("心跳重置 stall：30% 后长时间只有心跳（远超 STALL_MS）→ 任务不被标 failed", async () => {
    const { getByText, getByTestId } = mountAndStart();
    await act(async () => {
      getByText("go").click();
    });
    act(() => emit?.(RUN_30));

    // 每 20ms 一个心跳 × 10 = 200ms，累计远超 STALL_MS(50)；期间百分比**一次都没动**。
    for (let i = 0; i < 10; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });
      // 🔴 **每一拍推进后立刻查**，且必须在下一次 emit 之前查。
      // 血的教训（本包变异实测抓到的假绿）：只在循环结束后断言是**没有网的**——看门狗中途跳闸标 failed 后，
      // 循环里后续的 emit 会经 applyEvent 的 patch 把卡片**刷回 running**，于是"删掉心跳分支"这个变异
      // 照样绿。查在 emit 之前，跳闸就再也藏不住。
      expect(getByTestId("status").textContent).toBe("running");
      act(() => emit?.(beat()));
    }

    expect(getByTestId("status").textContent).toBe("running");
    expect(getByTestId("status").textContent).not.toBe("failed");
    expect(getByTestId("beat").textContent).toBe("seen"); // 确实被识别为心跳，不是当成普通帧混过去
  });

  // 🔴 门2：心跳**不许推进百分比**（不许做假进度）。连收 10 个心跳 → progress 逐字不变。
  // 变异：让心跳把百分比往前推（如 patch 里 `progress: next.progress + 1`）→ 本条必红。
  it("心跳不推百分比：连收 10 个心跳 → progress 与 statusLabel 逐字不变（30 / 生成中 30%）", async () => {
    const { getByText, getByTestId } = mountAndStart();
    await act(async () => {
      getByText("go").click();
    });
    act(() => emit?.(RUN_30));
    const label0 = getByTestId("label").textContent;

    for (let i = 0; i < 10; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });
      act(() => emit?.(beat()));
      // 每一拍都钉死：不是只看最后一帧，中途任何一次爬升都要当场红。
      expect(getByTestId("progress").textContent).toBe("30");
    }

    expect(getByTestId("progress").textContent).toBe("30"); // 逐字不变
    expect(getByTestId("label").textContent).toBe(label0); // 「生成中 30%」原样，没有自己爬
    expect(getByTestId("label").textContent).toContain("30%");
  });

  // 🔴 门3：看门狗没被心跳废掉——心跳**停了**之后，到 STALL_MS 仍然判 failed。
  // 变异：把 `else if (beat)` 改成无条件重置（不看 heartbeat_at）→ 重复帧也会续命 → 本条必红。
  it("真卡死仍被兜住：心跳停止后超过 STALL_MS → 仍标 failed", async () => {
    const { getByText, getByTestId } = mountAndStart();
    await act(async () => {
      getByText("go").click();
    });
    act(() => emit?.(RUN_30));

    // 先来 3 个心跳证明链路活着，然后**彻底安静**（后端真死了）。
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });
      act(() => emit?.(beat()));
    }
    expect(getByTestId("status").textContent).toBe("running");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200); // 静默 > STALL_MS(50)
    });
    expect(getByTestId("status").textContent).toBe("failed");
  });

  // 补网：重复的**非心跳**帧不得续命（原判据的语义必须保留——这正是 :131 注释说的"故意的"）。
  it("零回归：重复的普通帧（无 heartbeat_at）仍不重置 stall → 照常 failed", async () => {
    const { getByText, getByTestId } = mountAndStart();
    await act(async () => {
      getByText("go").click();
    });
    act(() => emit?.(RUN_30));

    // 帧一直在来（每 20ms 一个），但因为逐字重复、不算前进 → 时钟照走，看门狗照样跳闸。
    // 跳闸后**停止喂帧**：生产里 failTask 会 abort 掉流，不会再有事件进来；继续手喂等于制造假象
    // （applyEvent 的 patch 会把卡片刷回 running，那是测试驱动不忠实，不是实现问题）。
    for (let i = 0; i < 10; i++) {
      act(() => emit?.(RUN_30)); // 逐字重复，无心跳字段
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });
      if (getByTestId("status").textContent === "failed") break;
    }
    expect(getByTestId("status").textContent).toBe("failed");
  });
});
