import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Liquid-glass surface. Applies the `.glass` material (frosted backdrop, gold
 * edge light, drop shadow) defined in globals.css. Rounding/padding are left to
 * the caller so the same surface serves panels, the top bar, the sidebar, etc.
 */
export interface GlassProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Subtle fade-up entrance (≤200ms ease-out). Off by default. */
  animateIn?: boolean;
}

export const Glass = React.forwardRef<HTMLDivElement, GlassProps>(
  ({ className, animateIn = false, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("glass", animateIn && "animate-glass-in", className)}
      {...props}
    />
  )
);
Glass.displayName = "Glass";
