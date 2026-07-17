"use client";

import { X } from "lucide-react";

import { ContactQr } from "@/components/contact/contact-qr";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { copy } from "@/lib/copy";

/**
 * 「添加客服微信」弹窗（LANDING-CONTACT-UI-0001）——工作台侧的联系方式载体。
 * 两个入口共用：注册成功横幅的「查看微信二维码」+ 顶栏常驻「开通额度」。
 * Esc / 遮罩 / 关闭按钮均可关（Radix）——它是信息弹窗，不是强制流程。
 * onOpenChange 直接透传 Radix 的布尔信号（调用方可传 setState 本身），不降维成 onClose 回调。
 */
export function ContactDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const C = copy.contact;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <DialogTitle className="text-base font-semibold text-ink">{C.dialogTitle}</DialogTitle>
            <DialogDescription className="mt-1 text-[12.5px] text-ink-soft">{C.dialogDesc}</DialogDescription>
          </div>
          <DialogClose
            aria-label={copy.common.close}
            className="flex h-8 w-8 flex-none items-center justify-center rounded-mark text-ink-soft outline-none transition-colors hover:bg-glass-hover focus-visible:shadow-focus-gold"
          >
            <X size={16} strokeWidth={2} />
          </DialogClose>
        </div>
        <ContactQr compact />
      </DialogContent>
    </Dialog>
  );
}
