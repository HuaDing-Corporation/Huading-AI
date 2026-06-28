import { ShieldCheck } from "lucide-react";

import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

/**
 * 产物处「已含 AI 生成标识」知情提示（LABEL-UI-0001）。纯展示、token 化，可复用于
 * 生成结果(video-detail)与历史(generation-history)。强调标识已含且不可去除（合规知情）。
 */
export function AiLabelNotice({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-2.5 py-1 text-[12px] text-ink-soft",
        className
      )}
    >
      <ShieldCheck size={13} strokeWidth={1.8} className="flex-none text-gold-deep" />
      {copy.label.productNotice}
    </span>
  );
}
