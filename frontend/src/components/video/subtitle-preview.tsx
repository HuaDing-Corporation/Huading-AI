export interface SubtitlePreviewProps {
  script: string;
}

/**
 * Pure-props subtitle preview — renders the script text only.
 * Subtitles are burned into the video; this is text-only display.
 * No hooks, no fetch.
 */
export function SubtitlePreview({ script }: SubtitlePreviewProps) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-[12px] text-ink-faint">字幕已烧入，仅文本预览</p>
      <div className="rounded-field border border-line-gold bg-glass-fill px-4 py-3 text-[13.5px] leading-relaxed text-ink">
        {script || <span className="text-ink-faint">（无文案）</span>}
      </div>
    </div>
  );
}
