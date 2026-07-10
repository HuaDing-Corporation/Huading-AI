"use client";

import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export const Select = SelectPrimitive.Root;
export const SelectValue = SelectPrimitive.Value;

export function SelectTrigger({
  children,
  className,
  id,
  "aria-labelledby": ariaLabelledby
}: {
  children: ReactNode;
  className?: string;
  id?: string;
  "aria-labelledby"?: string;
}) {
  return (
    <SelectPrimitive.Trigger
      id={id}
      aria-labelledby={ariaLabelledby}
      className={cn(
        "inline-flex items-center justify-between gap-2 rounded-field border border-line-gold",
        "bg-glass-soft px-3 py-2 text-[13px] text-ink outline-none transition-colors",
        "hover:bg-glass-hover focus-visible:shadow-focus-gold",
        className
      )}
    >
      {children}
      <SelectPrimitive.Icon>
        <ChevronDown size={15} strokeWidth={1.8} />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

export function SelectContent({ children }: { children: ReactNode }) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content className="glass z-50 overflow-hidden rounded-field shadow-focus-gold animate-in fade-in-0 zoom-in-95">
        <SelectPrimitive.Viewport className="p-1">{children}</SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

export function SelectItem({ value, icon, children }: { value: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <SelectPrimitive.Item
      value={value}
      className={cn(
        "flex cursor-pointer items-center gap-2 rounded-[10px] px-2.5 py-1.5 text-[13px] text-ink-soft outline-none",
        "data-[highlighted]:bg-glass-soft data-[highlighted]:text-ink"
      )}
    >
      {/* 可选前置图标（如画面比例矩形 glyph）；装饰性，aria 由调用方保证。 */}
      {icon}
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
      <SelectPrimitive.ItemIndicator className="ml-auto">
        <Check size={14} strokeWidth={2} />
      </SelectPrimitive.ItemIndicator>
    </SelectPrimitive.Item>
  );
}
