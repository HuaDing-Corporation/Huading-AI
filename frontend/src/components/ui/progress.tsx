import { cn } from "@/lib/utils";

/** Slim progress bar — champagne track, gold-gradient fill. value in 0..100. */
export function Progress({
  value,
  className,
  barClassName
}: {
  value: number;
  className?: string;
  barClassName?: string;
}) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div
      className={cn("h-1.5 overflow-hidden rounded-[6px] bg-track", className)}
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={cn("h-full rounded-[6px] bg-grad-gold transition-[width] duration-500 ease-out", barClassName)}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
