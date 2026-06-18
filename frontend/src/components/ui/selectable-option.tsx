"use client";

import type { ReactNode } from "react";

import { selectableSurface } from "@/components/ui/chip";
import { cn } from "@/lib/utils";

export interface SelectableOptionProps {
  selected?: boolean;
  disabled?: boolean;
  onSelect?: () => void;
  className?: string;
  children: ReactNode;
}

/** Reusable selectable card/row — same selected surface as Chip (single source). */
export function SelectableOption({
  selected = false,
  disabled = false,
  onSelect,
  className,
  children
}: SelectableOptionProps) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      disabled={disabled}
      onClick={onSelect}
      className={cn(
        "flex items-center gap-3 rounded-field border px-3 py-2.5 text-left text-[13px] outline-none transition-colors focus-visible:shadow-focus-gold",
        selectableSurface(selected),
        disabled && "cursor-not-allowed opacity-60",
        className
      )}
    >
      {children}
    </button>
  );
}
