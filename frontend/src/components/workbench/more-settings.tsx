"use client";

import { ChevronDown, Lock } from "lucide-react";

import { copy } from "@/lib/copy";

export interface MoreSettingsProps {
  speed: number;
  onSpeedChange: (n: number) => void;
  /**
   * 语速滑杆的 DOM id。WORKBENCH-KEEPALIVE-UI-0001：面板常驻后口播与电商带货两份 MoreSettings 同存于 DOM，
   * id 必须各自唯一，否则 `<label for>` 会关联到文档中第一个（隐藏面板那份）滑杆。默认沿用口播的 video-speed。
   */
  id?: string;
}

const labelClass = "block text-[12.5px] tracking-[.5px] text-ink-soft";

function LockedRow({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 rounded-field bg-queue-bg px-3 py-2.5 text-[12.5px] text-ink-faint">
      <Lock size={14} strokeWidth={1.8} className="flex-none" />
      <span className="min-w-0 flex-1">{text}</span>
    </div>
  );
}

/** Collapsible advanced settings — speed slider + read-only locked aspect/subtitle rows. */
export function MoreSettings({ speed, onSpeedChange, id }: MoreSettingsProps) {
  const speedId = id ?? "video-speed";
  return (
    <details className="mb-[15px] rounded-field border border-line-gold bg-glass-fill">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3.5 py-3 text-[13px] text-ink-soft outline-none transition-colors hover:bg-glass-hover focus-visible:shadow-focus-gold [&::-webkit-details-marker]:hidden">
        {copy.workbench.more}
        <ChevronDown size={16} strokeWidth={2} className="text-ink-faint" />
      </summary>

      <div className="flex flex-col gap-3 border-t border-line-gold px-3.5 py-3.5">
        <div>
          <div className="mb-2 flex items-center justify-between gap-2">
            <label htmlFor={speedId} className={labelClass}>
              {copy.workbench.speed}
            </label>
            <span className="text-[12.5px] text-ink-faint">{speed.toFixed(1)}x</span>
          </div>
          <input
            id={speedId}
            type="range"
            min={0.5}
            max={2}
            step={0.1}
            value={speed}
            onChange={(e) => onSpeedChange(Number(e.target.value))}
            className="w-full accent-gold"
          />
        </div>

        <LockedRow text={copy.workbench.aspectLocked} />
        <LockedRow text={copy.workbench.subtitleLocked} />
      </div>
    </details>
  );
}
