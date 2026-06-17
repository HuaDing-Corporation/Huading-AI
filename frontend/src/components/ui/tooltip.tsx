"use client";

import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export const TooltipProvider = TooltipPrimitive.Provider;

/** Glass-skinned tooltip — Radix behavior, 华鼎 surface. */
export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          sideOffset={6}
          className={cn(
            "z-50 rounded-field border border-line-gold bg-glass-soft px-2.5 py-1.5 backdrop-blur-md",
            "text-[12.5px] text-ink-soft shadow-focus-gold",
            "animate-in fade-in-0 zoom-in-95"
          )}
        >
          {content}
          <TooltipPrimitive.Arrow className="fill-line-gold" />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}
