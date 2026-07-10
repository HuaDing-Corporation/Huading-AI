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
          <p className="truncate text-[13px] text-ink" title={analysis.pacing ?? undefined}>
            {analysis.pacing || "—"}
          </p>
        </div>
      </div>

      {/* 分镜列表 */}
      {shots.length > 0 && (
        <div className="mb-3">
          <span className={labelClass}>{copy.reverse.vaShotList}</span>
          <ol className="flex flex-col gap-1.5">
            {shots.map((shot) => (
              <li
                key={shot.index}
                className="flex items-start gap-2 rounded-field border border-line-gold bg-glass-soft px-3 py-2 text-[12.5px] text-ink"
              >
                <span className="flex-none rounded-pill bg-chip-sel px-2 py-0.5 text-[11px] font-medium text-gold-deep">
                  {copy.reverse.vaShot(shot.index + 1)}
                </span>
                <span className="min-w-0 flex-1">{shot.description}</span>
                {shot.duration_sec ? (
                  <span className="flex-none text-[11px] text-ink-faint">{copy.reverse.vaShotDuration(shot.duration_sec)}</span>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* 音频转写 / BGM 风格：一期空 → 明确标「未启用」 */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2">
        <div className="min-w-0">
          <span className={labelClass}>
            <Type size={12} className="mr-1 inline" />
            {copy.reverse.vaAudioTranscript}
          </span>
          <p className="text-[12.5px] text-ink-faint">{analysis.audio_transcript || copy.reverse.vaNotEnabled}</p>
        </div>
        <div className="min-w-0">
          <span className={labelClass}>
            <Music size={12} className="mr-1 inline" />
            {copy.reverse.vaBgmStyle}
          </span>
          <p className="text-[12.5px] text-ink-faint">{analysis.bgm_style || copy.reverse.vaNotEnabled}</p>
        </div>
      </div>
    </Card>
  );
}
