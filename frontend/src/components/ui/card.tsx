import * as React from "react";

import { cn } from "@/lib/utils";
import { Glass, type GlassProps } from "@/components/ui/glass";

/** Glass card — rounded-26 surface with standard inner padding. */
export const Card = React.forwardRef<HTMLDivElement, GlassProps>(
  ({ className, ...props }, ref) => (
    <Glass ref={ref} className={cn("rounded-card p-6", className)} {...props} />
  )
);
Card.displayName = "Card";

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("text-base font-semibold text-ink", className)} {...props} />;
}

export function CardSubtitle({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("text-[13px] text-ink-soft", className)} {...props} />;
}
