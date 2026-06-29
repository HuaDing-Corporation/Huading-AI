"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Pause, Play } from "lucide-react";
import { useWavesurfer } from "@wavesurfer/react";

import { copy } from "@/lib/copy";

/** 读设计 token CSS 变量画波形(canvas 需具体色)；SSR/缺失时回退暖香槟鎏金常量。 */
function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** 秒 → m:ss。 */
function fmtTime(s: number): string {
  const t = Number.isFinite(s) && s > 0 ? s : 0;
  const m = Math.floor(t / 60);
  const sec = Math.floor(t % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

const KEY_STEP_SEC = 1; // 方向键每步 ±1s

export interface WaveformPlayerProps {
  url: string;
  /** 无障碍名(如「试听 轻快上扬」)。 */
  ariaLabel: string;
  /** 是否当前活跃播放者；切到别首时本首自动暂停（同一时刻只播一首）。 */
  isActive: boolean;
  /** 本首开始播放时通知父级(置为活跃)。 */
  onPlayStart: () => void;
}

/**
 * BGM 波形试听播放器（BGM-WAVEFORM-UI-0001）。wavesurfer.js v7 渲染波形 + play/pause + 时间/时长；
 * **点击/拖动/键盘 seek**(自管 pointer + 方向键→ seekTo，鼠标/触摸/键盘统一、可测、合 ARIA slider 契约)。
 * token 配色(未播/已播两色，挂载一次性读取避免逐帧重算)。跨域解码失败(CORS/presigned)→ 销毁实例并降级
 * 原生 <audio>(至少可听)。纯试听，不改 BGM 选择/提交体。
 */
export function WaveformPlayer({ url, ariaLabel, isActive, onPlayStart }: WaveformPlayerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const draggingRef = useRef(false);
  const [failed, setFailed] = useState(false);
  // token 色挂载时读一次：避免播放中每帧 timeupdate 重渲染反复 getComputedStyle 强制样式重算。
  const [colors] = useState(() => ({
    wave: cssVar("--ink-faint", "#a89c80"),
    progress: cssVar("--gold-deep", "#8c6f3a")
  }));

  const { wavesurfer, isReady, isPlaying, currentTime } = useWavesurfer({
    container: containerRef,
    url,
    height: 36,
    waveColor: colors.wave,
    progressColor: colors.progress,
    cursorColor: colors.progress,
    cursorWidth: 2,
    barWidth: 2,
    barGap: 1,
    barRadius: 2,
    normalize: true,
    interact: false, // 自管 seek（pointer/键盘）→ 可测 + 触摸一致
    dragToSeek: false
  });

  // 跨域解码/加载失败(CORS presigned) → 降级原生 <audio>。
  useEffect(() => {
    if (!wavesurfer) return;
    const off = wavesurfer.on("error", () => setFailed(true));
    return () => off();
  }, [wavesurfer]);

  // 降级后销毁 wavesurfer 实例，避免游离 media 元素/解码内存悬挂(destroy 幂等，unmount 再调安全)。
  useEffect(() => {
    if (failed && wavesurfer) {
      try {
        wavesurfer.destroy();
      } catch {
        /* 已销毁/销毁中：忽略 */
      }
    }
  }, [failed, wavesurfer]);

  // 同一时刻只播一首：本首被切为非活跃且仍在播 → 暂停。
  useEffect(() => {
    if (!isActive && isPlaying && wavesurfer) wavesurfer.pause();
  }, [isActive, isPlaying, wavesurfer]);

  const seekFraction = useCallback(
    (fraction: number) => {
      if (!wavesurfer || !isReady) return;
      wavesurfer.seekTo(Math.min(1, Math.max(0, fraction)));
    },
    [wavesurfer, isReady]
  );

  const seekToClientX = useCallback(
    (clientX: number) => {
      const el = containerRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0) return;
      seekFraction((clientX - rect.left) / rect.width);
    },
    [seekFraction]
  );

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    draggingRef.current = true;
    try {
      e.currentTarget.setPointerCapture?.(e.pointerId);
    } catch {
      /* 拖拽捕获非关键：环境不支持/无效 pointerId 时忽略 */
    }
    seekToClientX(e.clientX);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (draggingRef.current) seekToClientX(e.clientX);
  };
  const endDrag = () => {
    draggingRef.current = false;
  };

  // 键盘 seek（ARIA slider 契约）：方向键 ±1s、Home/End 跳首尾。
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!wavesurfer || !isReady) return;
    const dur = wavesurfer.getDuration();
    if (dur <= 0) return;
    let fraction: number | null = null;
    if (e.key === "ArrowRight") fraction = (currentTime + KEY_STEP_SEC) / dur;
    else if (e.key === "ArrowLeft") fraction = (currentTime - KEY_STEP_SEC) / dur;
    else if (e.key === "Home") fraction = 0;
    else if (e.key === "End") fraction = 1;
    if (fraction === null) return;
    e.preventDefault();
    seekFraction(fraction);
  };

  const togglePlay = () => {
    if (!wavesurfer) return;
    if (isPlaying) {
      wavesurfer.pause();
    } else {
      onPlayStart();
      // play() 在自动播放策略未满足/解码失败时会 reject → 降级，避免未处理 rejection。
      wavesurfer.play().catch(() => setFailed(true));
    }
  };

  // 降级：原生 audio（CORS/解码失败时仍可听）。
  if (failed) {
    return <audio controls src={url} aria-label={ariaLabel} className="h-8 min-w-0 flex-1" />;
  }

  const duration = isReady && wavesurfer ? wavesurfer.getDuration() : 0;

  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      <button
        type="button"
        onClick={togglePlay}
        disabled={!isReady}
        aria-label={isPlaying ? copy.workbench.wfPause : `${copy.workbench.wfPlay}：${ariaLabel}`}
        className="flex h-8 w-8 flex-none items-center justify-center rounded-mark border border-line-gold bg-glass-fill text-gold-deep transition-colors hover:bg-glass-hover disabled:opacity-50"
      >
        {isPlaying ? <Pause size={14} strokeWidth={2} /> : <Play size={14} strokeWidth={2} />}
      </button>

      {/* 波形：点击/拖动(含触摸)/键盘 跳转播放位置 */}
      <div
        ref={containerRef}
        role="slider"
        aria-label={`${copy.workbench.wfSeek}：${ariaLabel}`}
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(currentTime)}
        aria-valuetext={`${fmtTime(currentTime)} / ${fmtTime(duration)}`}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        className="min-w-[88px] flex-1 cursor-pointer touch-none select-none outline-none focus-visible:shadow-focus-gold"
      />

      <span className="flex-none text-[11px] tabular-nums text-ink-faint">
        {isReady ? `${fmtTime(currentTime)} / ${fmtTime(duration)}` : copy.workbench.wfLoading}
      </span>
    </div>
  );
}
