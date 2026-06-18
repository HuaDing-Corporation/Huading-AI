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

function playSample(url: string) {
  try {
    void new Audio(url).play();
  } catch {
    // sample playback is best-effort
  }
}

/** Voice picker — each voice is a SelectableOption with optional sample play. Pure props. */
export function VoicePicker({ voices, value, onChange }: VoicePickerProps) {
  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{copy.workbench.voiceLabel}</legend>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {voices.map((voice) => {
          const meta = [voice.gender, voice.language].filter(Boolean).join(" · ");
          return (
            <SelectableOption
              key={voice.id}
              selected={value === voice.id}
              onSelect={() => onChange(voice.id)}
            >
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
        })}
      </div>
    </fieldset>
  );
}
