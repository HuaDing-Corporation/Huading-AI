"use client";

import { Clapperboard, Clock, Music, Type } from "lucide-react";

import { Card, CardTitle } from "@/components/ui/card";
import type { ReverseVideoAnalysis } from "@/lib/api/reverse-prompt";
import { copy } from "@/lib/copy";

const labelClass = "mb-1 block text-[12px] tracking-[.5px] text-ink-soft";

/**
 * 视频反推·视频分析视图（VIDEO-REVERSE-PROMPT-UI-0001）。在 Seedance 提示词结果**之上**展示：时长 / 节奏 /
 * 分镜列表 shot_list；音频转写 / BGM 风格 一期未启用（空→标「未启用（一期）」）。纯 props、无 hooks/fetch。
 */
export function ReverseVideoAnalysisView({ analysis }: { analysis: ReverseVideoAnalysis }) {
  const shots = analysis.shot_list ?? [];
  // pacing 是 BE 枚举（slow|medium|fast|variable）→ 映射中文显示；未知值兜底显原串（不裸奔英文场景外的鲁棒）。
  const pacingText = copy.reverse.vaPacingLabel[analysis.pacing] ?? analysis.pacing;
  return (
    <Card animateIn>
      <div className="mb-3 flex items-center gap-1.5">
        <Clapperboard size={16} strokeWidth={1.8} className="text-gold-deep" />
        <CardTitle>{copy.reverse.vaTitle}</CardTitle>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-x-4 gap-y-2">
        <div className="min-w-0">
          <span className={labelClass}>
            <Clock size={12} className="mr-1 inline" />
            {copy.reverse.vaDuration}
          </span>
          <p className="text-[13px] text-ink">
            {analysis.duration_sec ? copy.reverse.vaDurationValue(analysis.duration_sec) : copy.reverse.vaNotEnabled}
          </p>
        </div>
        <div className="min-w-0">
          <span className={labelClass}>{copy.reverse.vaPacing}</span>
          <p className="truncate text-[13px] text-ink" title={pacingText}>
            {pacingText}
          </p>
        </div>
      </div>

      {/* 分镜列表（FIX1：字段 start_sec/end_sec/visual/camera/motion/transition） */}
      {shots.length > 0 && (
        <div className="mb-3">
          <span className={labelClass}>{copy.reverse.vaShotList}</span>
          <ol className="flex flex-col gap-1.5">
            {shots.map((shot, i) => {
              const meta = [
                shot.camera && `${copy.reverse.vaShotCamera}：${shot.camera}`,
                shot.motion && `${copy.reverse.vaShotMotion}：${shot.motion}`,
                shot.transition && `${copy.reverse.vaShotTransition}：${shot.transition}`
              ].filter(Boolean);
              const hasRange = shot.start_sec != null && shot.end_sec != null;
              return (
                <li
                  key={i}
                  className="flex flex-col gap-1 rounded-field border border-line-gold bg-glass-soft px-3 py-2 text-[12.5px] text-ink"
                >
                  <div className="flex items-start gap-2">
                    <span className="flex-none rounded-pill bg-chip-sel px-2 py-0.5 text-[11px] font-medium text-gold-deep">
                      {copy.reverse.vaShot(i + 1)}
                    </span>
                    <span className="min-w-0 flex-1">{shot.visual}</span>
                    {hasRange ? (
                      <span className="flex-none text-[11px] text-ink-faint">
                        {copy.reverse.vaShotRange(shot.start_sec as number, shot.end_sec as number)}
                      </span>
                    ) : null}
                  </div>
                  {meta.length > 0 && <p className="text-[11.5px] text-ink-faint">{meta.join(" · ")}</p>}
                </li>
              );
            })}
          </ol>
        </div>
      )}

      {/* 音频转写 / BGM 风格 —— 🔴 FIX2 真联调：两者的「为空」含义**不再相同**，故不能再共用一句「未启用（一期）」：
          · audio_transcript：ASR 已落地（§八 M6，BE 走 _best_effort_audio_transcript 真调转写）→
            空的含义是「**这段素材没识别出台词**」（无音轨 / 转写降级），不是功能没开。
          · bgm_style：BE **写死 None** 且写明原因（apimart_gemini.py:806「ASR cannot classify music」）→
            空的含义是「判不了，不许编」。 */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2">
        <div className="min-w-0">
          <span className={labelClass}>
            <Type size={12} className="mr-1 inline" />
            {copy.reverse.vaAudioTranscript}
          </span>
          {/* 台词可能较长（真机是整段转写）→ 允许换行，不截断 */}
          <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-ink-faint">
            {analysis.audio_transcript || copy.reverse.vaNoTranscript}
          </p>
        </div>
        <div className="min-w-0">
          <span className={labelClass}>
            <Music size={12} className="mr-1 inline" />
            {copy.reverse.vaBgmStyle}
          </span>
          <p className="text-[12.5px] text-ink-faint">{analysis.bgm_style || copy.reverse.vaBgmUnsupported}</p>
        </div>
      </div>
    </Card>
  );
}
