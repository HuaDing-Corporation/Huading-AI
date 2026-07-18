"use client";

import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { copy } from "@/lib/copy";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel: string;
  /** 危险操作 → 红色确认按钮；否则金色主按钮。
   *  判据（DANGER-SEMANTICS-SIGNPOSTS-0001 · FIX1 · 方案 A 收准版）= 满足两支之一即 danger：
   *  ① 面向用户**不可恢复的删除/破坏性**操作（硬删，或软删但用户侧无恢复入口）；
   *  ② **高影响的负向/减损**操作（如扣减租户额度、停用租户——即便技术上可逆）。
   *  ⚠️ 判据**不是「能否撤销」**：扣减/停用可逆却仍 danger（属②负向减损），付费生成不可逆却用金色主按钮
   *  （正常付费、既非破坏也非减损）——「可否撤销」既非必要也非充分条件。 */
  danger?: boolean;
  /** 防连点：请求中禁用确认+取消。 */
  submitting?: boolean;
  /** 操作失败原因(弹窗内显示，避免被模态遮罩盖住列表横幅)。 */
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * 通用确认弹窗 — 破坏性操作前必经(HIST-UI-0001)。复用 ui/dialog(Radix glass)。
 * danger 控制确认按钮危险视觉(token 红 bg-error-bg/text-error-fg)；submitting 防连点。
 * 纯 props，无 hooks/fetch；文案经 props + copy.common。
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  danger,
  submitting,
  error,
  onConfirm,
  onCancel
}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !submitting) onCancel();
      }}
    >
      <DialogContent className="w-[min(92vw,420px)]">
        <DialogTitle className="text-base font-semibold text-ink">{title}</DialogTitle>
        <DialogDescription className="mt-2 text-[13px] leading-relaxed text-ink-soft">
          {message}
        </DialogDescription>
        {error && (
          <p role="alert" className="mt-3 rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
            {error}
          </p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="soft" size="sm" onClick={onCancel} disabled={submitting}>
            {copy.common.cancel}
          </Button>
          {danger ? (
            <button
              type="button"
              onClick={onConfirm}
              disabled={submitting}
              className="inline-flex h-9 items-center justify-center rounded-field bg-error-bg px-3.5 text-[13px] font-medium text-error-fg transition-colors hover:bg-error-bg/70 focus-visible:shadow-focus-gold disabled:pointer-events-none disabled:opacity-50"
            >
              {submitting ? copy.common.processing : confirmLabel}
            </button>
          ) : (
            <Button variant="primary" size="sm" onClick={onConfirm} disabled={submitting}>
              {submitting ? copy.common.processing : confirmLabel}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
