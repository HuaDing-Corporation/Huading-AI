"use client";

import { Play } from "lucide-react";

import { SelectableOption } from "@/components/ui/selectable-option";
import type { Voice } from "@/lib/api/types";
import { copy } from "@/lib/copy";

export interface VoicePickerProps {
  voices: Voice[];
  value: string;
  onChange: (id: string) => void;
}

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const groupClass = "mb-1.5 mt-2 block text-[11.5px] tracking-[.5px] text-ink-faint first:mt-0";

function playSample(url: string) {
  try {
    void new Audio(url).play();
  } catch {
    // sample playback is best-effort
  }
}

/** 单个音色选项（品牌/系统组复用，消除重复）。 */
function VoiceOption({ voice, selected, onSelect }: { voice: Voice; selected: boolean; onSelect: () => void }) {
  const meta = [voice.gender, voice.language].filter(Boolean).join(" · ");
  return (
    <SelectableOption selected={selected} onSelect={onSelect}>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-ink">{voice.display_name}</span>
        {meta && <span className="block truncate text-[11.5px] text-ink-faint">{meta}</span>}
      </span>
      {voice.sample_url && (
        <span
          role="button"
          tabIndex={0}
          aria-label={`试听 ${voice.display_name}`}
          onClick={(e) => {
            e.stopPropagation();
            playSample(voice.sample_url as string);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              e.stopPropagation();
              playSample(voice.sample_url as string);
            }
          }}
          className="flex h-7 w-7 flex-none items-center justify-center rounded-mark text-gold-deep hover:bg-glass-hover"
        >
          <Play size={14} strokeWidth={2} />
        </span>
      )}
    </SelectableOption>
  );
}

/**
 * Voice picker — 每个音色一个 SelectableOption（可试听）。纯 props。
 * 品牌音色(声音克隆，source==="brand_voice")单独分组「我的品牌音色」置顶；无品牌音色时退化为单一扁平
 * 列表(与历史行为一致，不破现有 new/ecom-video-form)。(BRAND-VOICE-UI-0001)
 */
export function VoicePicker({ voices, value, onChange }: VoicePickerProps) {
  // 按后端 VoiceRead.source 分组（§8）：brand_voice → 我的品牌音色；其余(preset)→ 系统音色。
  const brand = voices.filter((v) => v.source === "brand_voice");
  const standard = voices.filter((v) => v.source !== "brand_voice");
  const grouped = brand.length > 0;
  const gridClass = "grid grid-cols-1 gap-2 sm:grid-cols-2";

  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{copy.workbench.voiceLabel}</legend>

      {grouped && (
        <>
          {/* 分组标题 + role=group/aria-labelledby 关联，使屏幕阅读器读出「品牌/系统」归属 */}
          <span id="voice-group-brand" className={groupClass}>
            {copy.brandVoice.pickerBrandGroup}
          </span>
          <div className={gridClass} role="group" aria-labelledby="voice-group-brand">
            {brand.map((voice) => (
              <VoiceOption key={voice.id} voice={voice} selected={value === voice.id} onSelect={() => onChange(voice.id)} />
            ))}
          </div>
          <span id="voice-group-standard" className={groupClass}>
            {copy.brandVoice.pickerStandardGroup}
          </span>
        </>
      )}

      <div className={gridClass} role={grouped ? "group" : undefined} aria-labelledby={grouped ? "voice-group-standard" : undefined}>
        {standard.map((voice) => (
          <VoiceOption key={voice.id} voice={voice} selected={value === voice.id} onSelect={() => onChange(voice.id)} />
        ))}
      </div>
    </fieldset>
  );
}
