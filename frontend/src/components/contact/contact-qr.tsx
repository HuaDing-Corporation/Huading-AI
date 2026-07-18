import { Download, MessageCircle, Smartphone } from "lucide-react";

import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

/**
 * 客服微信二维码 + 双端扫码引导（LANDING-CONTACT-UI-0001）。落地页联系区与工作台弹窗共用。
 *
 * ## 为什么**没有**「点击加微信」链接（证伪任务包 §零 的「移动端点链接唤起微信」）
 *
 * 任务包设想 PC 扫码 / 移动端点 `u.wechat.com` 链接唤起微信，两端互补。**实测不成立**：
 *  - 该链接对桌面 / iPhone / Android / 微信内置浏览器四种 UA **一律 301 → wechat.com 官网**（curl -I 实测）；
 *  - `u.wechat.com` 下 `/.well-known/apple-app-site-association` 与 `/.well-known/assetlinks.json`
 *    **均为空** → iOS Universal Links / Android App Links 不存在，系统层也不会拦截点击唤起微信。
 * 它只是二维码的**编码内容**：仅当被微信「扫一扫」读到时，由微信 App 内部路由到加好友页（不走 HTTP）。
 * 放出来只会把用户送去微信官网 —— 比没有更糟。故移动端改走微信生态的标准路径：
 * **保存二维码图 → 微信「扫一扫」→ 右上角从相册选取识别**。
 *
 * ## 视觉
 * 二维码衬白底卡（`bg-white`，button.tsx 等既有先例）：这不是装饰，是**扫码对比度**的功能要求 ——
 * 暖金玻璃底上直接放码，摄像头识别率会掉。其余全部走 #154 既有 token，零新色值。
 *
 * ## 换号的代价（任务包 §三.1，说给下一个人）
 * 二维码是 `public/wechat-qr.png` 静态资源：**换微信号 = 换图 = 跑一次前端部署**（几分钟）。
 * 这是有意的取舍 —— 为一张 6KB 的图上 MinIO + BE 接口不值。⚠️ 不能热换，别以为改图立即生效。
 */
export function ContactQr({ compact }: { compact?: boolean }) {
  const C = copy.contact;
  // 尺寸只声明一处（width/height 属性即定尺寸；再写尺寸 class 就是四处重复，改一漏三时 class 会静默压过属性）
  const size = compact ? 148 : 176;
  return (
    <div className={cn("flex flex-col items-center gap-5 sm:flex-row sm:justify-center", compact && "gap-4")}>
      {/* 白底衬卡：扫码对比度（功能），非装饰 */}
      <div className="rounded-card border border-line-gold bg-white p-3 shadow-glass">
        {/* eslint-disable-next-line @next/next/no-img-element -- 6KB 本地静态图，无需 next/image 优化管线 */}
        <img src="/wechat-qr.png" alt={C.qrAlt} width={size} height={size} className="block" />
      </div>
      <div className="flex max-w-[280px] flex-col items-center gap-3 text-center sm:items-start sm:text-left">
        {/* PC 引导：手机扫屏幕上的码（sm 及以上显示） */}
        <p className="hidden items-start gap-2 text-[13.5px] leading-[1.65] text-ink-soft sm:flex">
          <Smartphone size={16} strokeWidth={1.8} aria-hidden className="mt-0.5 flex-none text-gold-deep" />
          {C.hintDesktop}
        </p>
        {/* 移动端引导：扫不了自己的屏幕 → 保存图 → 微信扫一扫从相册选取 */}
        <p className="flex items-start gap-2 text-[13.5px] leading-[1.65] text-ink-soft sm:hidden">
          <MessageCircle size={16} strokeWidth={1.8} aria-hidden className="mt-0.5 flex-none text-gold-deep" />
          {C.hintMobile}
        </p>
        {/* 保存按钮只给移动端：PC 主路径是直接扫屏，多一个下载按钮反而分散（每屏一个主动作）。
            h-11 = 44px 触控目标（WCAG 2.5.5 AAA）。download 属性 → 浏览器存图而非导航。
            ⚠️ download 在 Android/Chromium 有效（进可被相册访问的目录）；iOS Safari 存进「文件」App、不进
            「照片」→ 微信从相册扫不到 → 故下方补 iOS 长按兜底（FIX1 · P2-1）。 */}
        <a
          href="/wechat-qr.png"
          download="华鼎客服微信二维码.png"
          className="inline-flex h-11 items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-4 text-[13px] text-gold-deep transition-colors hover:bg-glass-hover focus-visible:shadow-focus-gold sm:hidden"
        >
          <Download size={15} strokeWidth={2} aria-hidden /> {C.saveQr}
        </a>
        {/* iOS 兜底文案：只在移动端出现（PC 用户直接扫屏，不需要）。 */}
        <p className="text-[12px] leading-[1.5] text-ink-faint sm:hidden">{C.hintSaveIos}</p>
      </div>
    </div>
  );
}
