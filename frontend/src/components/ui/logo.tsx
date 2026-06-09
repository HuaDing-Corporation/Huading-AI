import { cn } from "@/lib/utils";

/**
 * Geometric "鼎" (ding / bronze cauldron) mark — two ears + body + two feet —
 * filled with the古铜金 gradient (--logo-grad-from → --logo-grad-to), set inside
 * a frosted-glass badge. Used for the console UI / favicon / nav.
 */
export function DingMark({
  size = 30,
  gradientId = "huading-ding"
}: {
  size?: number;
  gradientId?: string;
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 34 34" fill="none" aria-hidden="true">
      <path d="M7 12 H27 V20 Q27 25 22 25 H12 Q7 25 7 20 Z" fill={`url(#${gradientId})`} />
      <rect x="9" y="8" width="3.4" height="6" rx="1.7" fill={`url(#${gradientId})`} />
      <rect x="21.6" y="8" width="3.4" height="6" rx="1.7" fill={`url(#${gradientId})`} />
      <rect x="10.5" y="25" width="3" height="5" rx="1.5" fill={`url(#${gradientId})`} />
      <rect x="20.5" y="25" width="3" height="5" rx="1.5" fill={`url(#${gradientId})`} />
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="34" y2="34">
          <stop stopColor="var(--logo-grad-from)" />
          <stop offset="1" stopColor="var(--logo-grad-to)" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export function LogoBadge({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "flex h-12 w-12 items-center justify-center rounded-mark border border-line-glass bg-grad-mark shadow-mark",
        className
      )}
    >
      <DingMark size={30} />
    </div>
  );
}

/** Full brand lockup: glass badge + wordmark. */
export function Logo({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-3.5", className)}>
      <LogoBadge />
      <div className="leading-none">
        <b className="text-[18px] font-semibold tracking-[1px] text-ink">华鼎 AI</b>
        <span className="mt-0.5 block text-[11px] tracking-brand text-ink-faint">VIDEO&nbsp;ENGINE</span>
      </div>
    </div>
  );
}
