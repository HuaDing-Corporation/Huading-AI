import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Circular gold avatar with white initials. */
export function Avatar({
  children,
  className
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex h-10 w-10 flex-none items-center justify-center rounded-full bg-grad-gold text-sm font-semibold text-white shadow-avatar",
        className
      )}
    >
      {children}
    </div>
  );
}
