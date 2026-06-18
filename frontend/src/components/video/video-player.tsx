"use client";

import { useRef } from "react";
import { Download } from "lucide-react";

import { copy } from "@/lib/copy";

export interface VideoPlayerProps {
  playbackUrl?: string | null;
  downloadUrl?: string | null;
  poster?: string | null;
  onUrlExpired: () => void;
}

/**
 * Pure-props video player with download link.
 * No hooks, no fetch. onUrlExpired is called at most once per render.
 */
export function VideoPlayer({ playbackUrl, downloadUrl, poster, onUrlExpired }: VideoPlayerProps) {
  const expired = useRef(false);

  function handleError() {
    if (!expired.current) {
      expired.current = true;
      onUrlExpired();
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <video
        controls
        preload="metadata"
        poster={poster ?? undefined}
        src={playbackUrl ?? undefined}
        onError={handleError}
        className="max-h-[480px] w-full rounded-field border border-line-gold bg-black/5"
      />
      {downloadUrl && (
        <a
          href={downloadUrl}
          download
          className="inline-flex w-fit items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[13px] text-gold-deep transition-colors hover:bg-glass-hover"
        >
          <Download size={15} strokeWidth={2} />
          {copy.detail.download}
        </a>
      )}
    </div>
  );
}
