"use client";

import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { cn } from "@/lib/utils";

export const Select = SelectPrimitive.Root;
export const SelectValue = SelectPrimitive.Value;

// SELECT-ARIA-LABEL-FIX-0001：透传 Radix Trigger 的全部 props（含 aria-label / aria-labelledby /
// aria-describedby / id / disabled …），恢复被简化掉的标准 shadcn/Radix 包装范式。此前只枚举了少数
// 几个 prop、其余静默丢弃——尤以 aria-label 为甚（连字符属性在 JSX 里逃逸 TS excess-property 检查，
// 类型系统永远不报错，只能靠测试兜）。className/children 单独解构：className 经 cn() 合并、children
// 后接下拉箭头图标，均不被 rest 覆盖。aria-label 与 aria-labelledby 同传时由 ARIA 规范定 labelledby 优先。
//
// FIX1（PR #171）：rest 透传曾把 `asChild` 也变成合法 API，但本组件**固定追加第二个子节点（箭头图标）**，
// 而 asChild 语义是「把 props 合并到唯一子元素」→ 传 asChild 必崩（Primitive.button failed to slot）。
// 故类型（Omit + asChild?:never）与运行时（解构摘出丢弃，防 as-any 绕过）双双排除 asChild。asChild 不含
// 连字符、是合法标识符 → TS 这次抓得到（与 aria-label 相反），类型层可当护栏（见 select.test.tsx 的 @ts-expect-error）。
export function SelectTrigger({
  children,
  className,
  asChild,
  ...props
}: Omit<ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger>, "asChild"> & { asChild?: never }) {
  void asChild; // 摘出即丢弃——本组件固定渲染 trigger + 箭头两个子节点，不支持 asChild
  return (
    <SelectPrimitive.Trigger
      {...props}
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
