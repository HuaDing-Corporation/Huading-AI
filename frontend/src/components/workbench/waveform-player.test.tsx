import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// 受控 wavesurfer 假实例：隔离 jsdom 无法跑的真 wavesurfer，聚焦本组件的 seek/play/降级逻辑。
const wsMock = vi.hoisted(() => {
  const handlers: Record<string, () => void> = {};
  const ws = {
    seekTo: vi.fn(),
    play: vi.fn(() => Promise.resolve()),
    pause: vi.fn(),
    destroy: vi.fn(),
    getDuration: () => 30,
    on: (ev: string, cb: () => void) => {
      handlers[ev] = cb;
      return () => delete handlers[ev];
    }
  };
  return { ws, handlers, state: { wavesurfer: ws, isReady: true, isPlaying: false, currentTime: 0 } };
});
vi.mock("@wavesurfer/react", () => ({ useWavesurfer: () => wsMock.state }));

import { WaveformPlayer } from "./waveform-player";

const ARIA = "试听 测试曲";
const setRect = (el: HTMLElement, left: number, width: number) => {
  el.getBoundingClientRect = () =>
    ({ left, width, top: 0, height: 36, right: left + width, bottom: 36, x: left, y: 0, toJSON: () => ({}) }) as DOMRect;
};
// jsdom 的 PointerEvent 不带 clientX，手动构造带坐标的事件按指定类型派发（React 按 type 捕获）。
const firePointer = (el: HTMLElement, type: "pointerdown" | "pointermove", clientX: number) => {
  const ev = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(ev, "clientX", { value: clientX });
  Object.defineProperty(ev, "pointerId", { value: 1 });
  fireEvent(el, ev);
};

beforeEach(() => {
  wsMock.ws.seekTo.mockClear();
  wsMock.ws.play.mockClear();
  wsMock.ws.pause.mockClear();
  wsMock.ws.destroy.mockClear();
  Object.keys(wsMock.handlers).forEach((k) => delete wsMock.handlers[k]);
  wsMock.state.isReady = true;
  wsMock.state.isPlaying = false;
  wsMock.state.currentTime = 0;
});
afterEach(() => vi.clearAllMocks());

describe("WaveformPlayer (BGM 波形播放器)", () => {
  // 承重：点击波形 → seekTo(fraction)。
  it("点波形中点 → seekTo(0.5)（点击 seek 承重）", () => {
    render(<WaveformPlayer url="blob:x" ariaLabel={ARIA} isActive={false} onPlayStart={vi.fn()} />);
    const slider = screen.getByRole("slider");
    setRect(slider, 0, 100);
    firePointer(slider, "pointerdown", 50);
    expect(wsMock.ws.seekTo).toHaveBeenCalledWith(0.5);
  });

  it("拖动波形 → 持续 seekTo（触摸/鼠标统一 pointer）", () => {
    render(<WaveformPlayer url="blob:x" ariaLabel={ARIA} isActive={false} onPlayStart={vi.fn()} />);
    const slider = screen.getByRole("slider");
    setRect(slider, 0, 200);
    firePointer(slider, "pointerdown", 0);
    firePointer(slider, "pointermove", 150);
    expect(wsMock.ws.seekTo).toHaveBeenLastCalledWith(0.75);
  });

  it("seek 越界裁剪到 [0,1]", () => {
    render(<WaveformPlayer url="blob:x" ariaLabel={ARIA} isActive={false} onPlayStart={vi.fn()} />);
    const slider = screen.getByRole("slider");
    setRect(slider, 0, 100);
    firePointer(slider, "pointerdown", 250);
    expect(wsMock.ws.seekTo).toHaveBeenCalledWith(1);
  });

  it("播放：点播放 → ws.play() + onPlayStart（置为活跃）", () => {
    const onPlayStart = vi.fn();
    render(<WaveformPlayer url="blob:x" ariaLabel={ARIA} isActive={false} onPlayStart={onPlayStart} />);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.workbench.wfPlay) }));
    expect(wsMock.ws.play).toHaveBeenCalledTimes(1);
    expect(onPlayStart).toHaveBeenCalledTimes(1);
  });

  it("暂停：播放中点暂停 → ws.pause()，不重复 onPlayStart", () => {
    const onPlayStart = vi.fn();
    wsMock.state.isPlaying = true;
    render(<WaveformPlayer url="blob:x" ariaLabel={ARIA} isActive onPlayStart={onPlayStart} />);
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.wfPause }));
    expect(wsMock.ws.pause).toHaveBeenCalledTimes(1);
    expect(onPlayStart).not.toHaveBeenCalled();
  });

  it("单播：被切为非活跃且仍在播 → 自动暂停（切曲停上一首）", () => {
    wsMock.state.isPlaying = true;
    render(<WaveformPlayer url="blob:x" ariaLabel={ARIA} isActive={false} onPlayStart={vi.fn()} />);
    expect(wsMock.ws.pause).toHaveBeenCalled();
  });

  it("降级：解码/CORS 失败(error) → 销毁实例 + 原生 <audio> 可听", () => {
    render(<WaveformPlayer url="blob:x" ariaLabel={ARIA} isActive={false} onPlayStart={vi.fn()} />);
    expect(screen.getByRole("slider")).toBeInTheDocument();
    act(() => wsMock.handlers.error?.());
    // 降级后波形 slider 消失，原生 audio(带 ariaLabel)出现，且实例被销毁(不留游离 media)。
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.getByLabelText(ARIA)).toBeInTheDocument();
    expect(wsMock.ws.destroy).toHaveBeenCalled();
  });

  it("键盘 seek：ArrowRight ±1s / End→1 / Home→0（ARIA slider 契约）", () => {
    wsMock.state.currentTime = 0;
    render(<WaveformPlayer url="blob:x" ariaLabel={ARIA} isActive={false} onPlayStart={vi.fn()} />);
    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(wsMock.ws.seekTo).toHaveBeenLastCalledWith(expect.closeTo(1 / 30, 5)); // (0+1)/30
    fireEvent.keyDown(slider, { key: "End" });
    expect(wsMock.ws.seekTo).toHaveBeenLastCalledWith(1);
    fireEvent.keyDown(slider, { key: "Home" });
    expect(wsMock.ws.seekTo).toHaveBeenLastCalledWith(0);
  });

  it("isReady=false：点波形不 seek + 播放按钮禁用 + 显示加载态", () => {
    wsMock.state.isReady = false;
    render(<WaveformPlayer url="blob:x" ariaLabel={ARIA} isActive={false} onPlayStart={vi.fn()} />);
    const slider = screen.getByRole("slider");
    setRect(slider, 0, 100);
    firePointer(slider, "pointerdown", 50);
    expect(wsMock.ws.seekTo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: new RegExp(copy.workbench.wfPlay) })).toBeDisabled();
    expect(screen.getByText(copy.workbench.wfLoading)).toBeInTheDocument();
  });

  it("拖动门控：未先 pointerdown 的 pointermove 不 seek", () => {
    render(<WaveformPlayer url="blob:x" ariaLabel={ARIA} isActive={false} onPlayStart={vi.fn()} />);
    const slider = screen.getByRole("slider");
    setRect(slider, 0, 100);
    firePointer(slider, "pointermove", 50);
    expect(wsMock.ws.seekTo).not.toHaveBeenCalled();
  });

  it("时间/时长显示 + aria-valuetext(currentTime=65 → 1:05 / 0:30)", () => {
    wsMock.state.currentTime = 65;
    render(<WaveformPlayer url="blob:x" ariaLabel={ARIA} isActive={false} onPlayStart={vi.fn()} />);
    expect(screen.getByText("1:05 / 0:30")).toBeInTheDocument();
    expect(screen.getByRole("slider")).toHaveAttribute("aria-valuetext", "1:05 / 0:30");
  });
});
