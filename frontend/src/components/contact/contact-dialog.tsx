"use client";

import { X } from "lucide-react";

import { ContactQr } from "@/components/contact/contact-qr";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { copy } from "@/lib/copy";

/**
 * 「添加客服微信」弹窗（LANDING-CONTACT-UI-0001）——工作台侧的联系方式载体。
 * 两个入口共用：注册成功横幅的「查看微信二维码」+ 顶栏常驻「开通额度」。
 * Esc / 遮罩 / 关闭按钮均可关（Radix）——它是信息弹窗，不是强制流程。
 * onOpenChange 直接透传 Radix 的布尔信号（调用方可传 setState 本身），不降维成 onClose 回调。
 *
 * 🔴 外壳用 Radix 原语 + 既有 `Button`，**不复制 HistoryDetailDialog、也不手写关闭钮样式串**：
 * 本仓刚花四轮治完 `useMediaUrlRefresh` 的「三份拷贝三个样」（video-player 缺 URL 重置 /
 * task-card 是它的超集 / video-detail 裸接，而注释还写着 per render 实为 per mount）。
 * 关闭钮的样式串同理——它在本仓已经有三份且已漂移（h-8/rounded-mark vs h-9/rounded-field），
 * 本包不再添第四份：交给 ui/button 的 ghost/icon 承接。
 * （把 HistoryDetailDialog 提升成通用外壳是对的方向，但它硬编码 880px 宽 + 标题 truncate，
 *  且正被 #188 FIX3 改 → 那是独立一片的活，不塞进本包。）
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
          <DialogClose asChild>
            <Button variant="ghost" size="icon" aria-label={copy.common.close} className="flex-none">
              <X size={16} strokeWidth={2} />
            </Button>
          </DialogClose>
        </div>
        <ContactQr compact />
      </DialogContent>
    </Dialog>
  );
}
