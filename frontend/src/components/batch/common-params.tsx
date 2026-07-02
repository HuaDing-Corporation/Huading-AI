"use client";

import { useEffect, useState } from "react";

import type { BatchCommon, BatchKind, VideoGenBgm, VideoGenDuration, VideoGenResolution } from "@/lib/api/types";
import { VIDEO_GEN_DURATIONS } from "@/lib/api/types";
import { SelectableOption } from "@/components/ui/selectable-option";
import { DurationPicker } from "@/components/workbench/duration-picker";
import { BgmPicker } from "@/components/workbench/bgm-picker";
import { AiLabelToggle } from "@/components/label/ai-label-toggle";
import { useLabelTogglePreference } from "@/lib/preferences/label-toggle";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const RESOLUTIONS: VideoGenResolution[] = ["480p", "720p", "1080p"];

/**
 * 批量公共参数区（BATCH-PROD-UI-0001）——应用到本批每条。复用现组件：时长(按 kind：电商 DurationPicker
 * 5–120 / 提示词组 5/10/15 档)、分辨率(480/720/1080)、AI 标识开关(AiLabelToggle + 记忆)、BGM(BgmPicker)。
 * 内部持状态，经 onChange 上抛完整 BatchCommon（含 video_mode 按 kind）。onChange 需为稳定引用(如 setState)。
 */
export function CommonParams({ kind, onChange }: { kind: BatchKind; onChange: (common: BatchCommon) => void }) {
  const [durationSec, setDurationSec] = useState<number>(kind === "ecom_table" ? 30 : 5);
  const [resolution, setResolution] = useState<VideoGenResolution>("720p");
  const [applyLabel, setApplyLabel] = useLabelTogglePreference();
  const [bgm, setBgm] = useState<VideoGenBgm | undefined>(undefined);

  useEffect(() => {
    onChange({
      video_mode: kind === "ecom_table" ? "seedance_i2v" : "video_gen",
      duration_sec: durationSec,
      resolution,
      apply_visible_label: applyLabel,
      bgm
    });
  }, [kind, durationSec, resolution, applyLabel, bgm, onChange]);

  return (
    <div>
      <span className={labelClass}>{copy.batch.commonTitle}</span>

      {/* 时长：电商 5–120 档 / 提示词组 5/10/15 三档 */}
      {kind === "ecom_table" ? (
        <DurationPicker value={durationSec} onChange={setDurationSec} />
      ) : (
        <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
          <legend className={labelClass}>{copy.workbench.vgDurationLabel}</legend>
          <div className="grid grid-cols-3 gap-2">
            {VIDEO_GEN_DURATIONS.map((sec) => (
              <SelectableOption key={sec} selected={durationSec === sec} onSelect={() => setDurationSec(sec as VideoGenDuration)} className="justify-center">
                {copy.workbench.durationSeconds(sec)}
              </SelectableOption>
            ))}
          </div>
        </fieldset>
      )}

      {/* 分辨率 480/720/1080 */}
      <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
        <legend className={labelClass}>{copy.workbench.vgResolutionLabel}</legend>
        <div className="grid grid-cols-3 gap-2">
          {RESOLUTIONS.map((r) => (
            <SelectableOption key={r} selected={resolution === r} onSelect={() => setResolution(r)} className="justify-center">
              {r.toUpperCase()}
            </SelectableOption>
          ))}
        </div>
      </fieldset>

      <BgmPicker onChange={setBgm} />

      <AiLabelToggle checked={applyLabel} onChange={setApplyLabel} />
    </div>
  );
}
