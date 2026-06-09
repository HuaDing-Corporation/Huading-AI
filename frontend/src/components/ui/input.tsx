import * as React from "react";

import { cn } from "@/lib/utils";

/** Text input — white-translucent fill, faint gold border, focus gold-ring. */
const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "w-full rounded-field border border-line-gold bg-glass-fill px-4 py-3.5 text-sm text-ink outline-none transition-shadow",
        "placeholder:text-ink-faint focus:border-line-sel focus:shadow-focus-gold",
        className
      )}
      {...props}
    />
  )
);
Input.displayName = "Input";

export { Input };
