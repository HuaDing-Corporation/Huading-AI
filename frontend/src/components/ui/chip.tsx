import * as React from "react";

import { cn } from "@/lib/utils";

export interface ChipProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  selected?: boolean;
}

/** Selectable filter chip — same surface as inputs; selected = heavier gold edge
 *  + champagne fill + deep-gold text. */
const Chip = React.forwardRef<HTMLButtonElement, ChipProps>(
  ({ className, selected = false, children, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      aria-pressed={selected}
      className={cn(
        "flex items-center justify-between gap-2 rounded-chip border px-3.5 py-3 text-[13px] outline-none transition-colors focus-visible:shadow-focus-gold",
        selected
          ? "border-line-sel bg-chip-sel font-medium text-gold-deep"
          : "border-line-gold bg-glass-fill text-ink-soft hover:bg-glass-hover",
        className
      )}
      {...props}
    >
      {children}
    </button>
  )
);
Chip.displayName = "Chip";

export { Chip };
