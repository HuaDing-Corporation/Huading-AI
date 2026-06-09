import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 font-medium transition-[transform,background,box-shadow,opacity] duration-150 ease-out active:scale-[.98] focus-visible:outline-none focus-visible:shadow-focus-gold disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        // Primary gold action button (gradient fill, deep-ink text for AA contrast).
        primary:
          "bg-grad-gold text-ink shadow-button hover:brightness-[1.03]",
        // Quiet glass-tinted secondary.
        soft:
          "border border-line-gold bg-glass-fill text-ink-soft hover:bg-white/55",
        ghost: "text-ink-soft hover:bg-white/45",
        // Square icon button (top bar).
        icon: "border border-line-gold bg-glass-fill text-ink-soft hover:bg-white/55"
      },
      size: {
        default: "h-11 rounded-btn px-5 text-sm",
        lg: "h-[52px] rounded-btn px-6 text-[15px] tracking-[1px]",
        sm: "h-9 rounded-field px-3.5 text-[13px]",
        icon: "h-10 w-10 rounded-[13px] text-[18px]"
      }
    },
    defaultVariants: {
      variant: "primary",
      size: "default"
    }
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
