import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAudioRecorder } from "./use-audio-recorder";
import { copy } from "@/lib/copy";

// jsdom 无 MediaRecorder/getUserMedia → 注入受控 mock，验证 hook 的 权限/启停/Blob 流程。
class MockMediaRecorder {
  state = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  constructor(public stream: unknown) {}
  start() {
    this.state = "recording";
  }
  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["chunk"], { type: "audio/webm" }) });
    this.onstop?.();
  }
}

// 稳定的轨道 stop spy（每次 getTracks 返回新对象但同一 stop fn），供断言清理调用。
let trackStop = vi.fn();
const fakeStream = { getTracks: () => [{ stop: trackStop }] };

function setSupport(supported: boolean, getUserMedia?: () => Promise<unknown>) {
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: supported ? { getUserMedia: getUserMedia ?? vi.fn().mockResolvedValue(fakeStream) } : undefined
  });
  (globalThis as { MediaRecorder?: unknown }).MediaRecorder = supported ? (MockMediaRecorder as unknown) : undefined;
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  trackStop = vi.fn();
});
afterEach(() => {
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: undefined });
  (globalThis as { MediaRecorder?: unknown }).MediaRecorder = undefined;
  vi.clearAllMocks();
});

describe("useAudioRecorder", () => {
  it("支持时：start→recording=true，stop→落 Blob + recording=false", async () => {
    setSupport(true);
    const { result } = renderHook(() => useAudioRecorder());
    expect(result.current.supported).toBe(true);

    await act(async () => {
      await result.current.start();
    });
    expect(result.current.recording).toBe(true);

    act(() => {
      result.current.stop();
    });
    await waitFor(() => expect(result.current.blob).toBeInstanceOf(Blob));
    expect(result.current.recording).toBe(false);
    expect(result.current.url).toBe("blob:mock");
  });

  it("权限被拒：start 落友好错误，不进入录音态", async () => {
    setSupport(true, vi.fn().mockRejectedValue(new Error("denied")));
    const { result } = renderHook(() => useAudioRecorder());

    await act(async () => {
      await result.current.start();
    });
    expect(result.current.recording).toBe(false);
    expect(result.current.error).toBe(copy.brandVoice.recordPermissionDenied);
  });

  it("不支持录音：supported=false，start 落不支持提示", async () => {
    setSupport(false);
    const { result } = renderHook(() => useAudioRecorder());
    expect(result.current.supported).toBe(false);

    await act(async () => {
      await result.current.start();
    });
    expect(result.current.error).toBe(copy.brandVoice.recordUnsupported);
  });

  it("setExternal：上传 Blob 复用同一 blob/url 试听态", () => {
    setSupport(false);
    const { result } = renderHook(() => useAudioRecorder());
    const file = new Blob(["x"], { type: "audio/mpeg" });

    act(() => {
      result.current.setExternal(file);
    });
    expect(result.current.blob).toBe(file);
    expect(result.current.url).toBe("blob:mock");
  });

  it("计时：录音中 durationSec 随时间增长，stop 后定格（防泄漏/计时承诺）", async () => {
    setSupport(true);
    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => useAudioRecorder());
      await act(async () => {
        await result.current.start();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });
      expect(result.current.durationSec).toBeGreaterThanOrEqual(2.5);

      act(() => {
        result.current.stop();
      });
      const frozen = result.current.durationSec;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(result.current.durationSec).toBe(frozen); // 计时器已 clear，不再增长
    } finally {
      vi.useRealTimers();
    }
  });

  it("清理：stop 停止音轨；卸载释放音轨 + 试听 URL（防麦克风/URL 泄漏）", async () => {
    setSupport(true);
    const { result, unmount } = renderHook(() => useAudioRecorder());
    await act(async () => {
      await result.current.start();
    });
    act(() => {
      result.current.stop();
    });
    expect(trackStop).toHaveBeenCalled(); // 轨道被 stop()，麦克风释放
    await waitFor(() => expect(result.current.blob).toBeInstanceOf(Blob));
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalled(); // 卸载释放试听 object URL
  });

  it("reset：清空 blob/url/durationSec 并释放 object URL（公开 API 回到干净态）", () => {
    setSupport(false);
    const { result } = renderHook(() => useAudioRecorder());
    act(() => {
      result.current.setExternal(new Blob(["x"], { type: "audio/mpeg" }));
    });
    expect(result.current.blob).not.toBeNull();

    act(() => {
      result.current.reset();
    });
    expect(result.current.blob).toBeNull();
    expect(result.current.url).toBeNull();
    expect(result.current.durationSec).toBe(0);
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });
});
