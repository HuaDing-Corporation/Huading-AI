"use client";

// 华鼎AI智脑 · 输入框（AIBRAIN-UI-0001 · FIX1）—— 承载**发送前核心承重**：
//  1. 档位 tier 随请求传；2. 余额预检拦在开答前：`available<=0` → 弹充值窗、**不发请求**（BE 402 口径）。
// 图片走既有 `/uploads/images`（→ asset_id，喂 attachment_asset_ids）；语音走 Web Speech（不支持则隐藏）。
// ⚠️ 文档上传是 BE 增量 3，一期无端点 → **本期不提供文档入口**（不硬塞、不在一期路径调用）。

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ImagePlus, Loader2, Mic, MicOff, Send, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";
import { ApiError } from "@/lib/api/client";
import { useUploadImage } from "@/lib/api/hooks";
import { validateImageFile } from "@/lib/api/uploads";
import { precheckSend, type IntensityTier, type PendingAttachment, type SendMessageRequest } from "@/lib/aibrain/types";
import { IntensitySelector } from "@/components/aibrain/intensity-selector";
import { useVoiceInput } from "@/components/aibrain/use-voice-input";

export function Composer({
  tier,
  onTierChange,
  balance,
  sending,
  onSend,
  onInsufficient
}: {
  tier: IntensityTier;
  onTierChange: (t: IntensityTier) => void;
  /** 可用推理积分（wallet.available_credits）；`undefined` = 钱包未加载 → 预检不拦（CR#2）。 */
  balance: number | undefined;
  sending: boolean;
  /** 🔴 返回是否**发送成功**（P1-1）：只有 true 才清空输入/附件/revoke 预览；失败则原样保留，用户不必重打重传。 */
  onSend: (body: SendMessageRequest) => Promise<boolean>;
  /** 余额不足 → 让父层弹充值窗（不是普通报错）。 */
  onInsufficient: () => void;
}) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const imageInput = useRef<HTMLInputElement>(null);
  const voiceBase = useRef("");
  const objectUrls = useRef<string[]>([]); // 本地预览 objectURL，须显式 revoke（CR#3）
  // 🔴 FIX5：镜像最新 text/attachments。发送在 await 期间开了一个异步窗口，submit 闭包里的 `text`/`attachments`
  //   是发送**瞬间**的旧值——成功后要判断「窗口期有没有被动过」，必须读这两个 ref 拿最新值。
  const textRef = useRef(text);
  const attachmentsRef = useRef<PendingAttachment[]>(attachments);
  const uploadImage = useUploadImage();

  const voice = useVoiceInput((t) => setText(voiceBase.current + t));

  useEffect(() => {
    textRef.current = text;
    attachmentsRef.current = attachments;
  }, [text, attachments]);

  // 卸载时释放所有未撤销的预览 URL（换页/关闭仍会遗留，浏览器只在整页卸载时兜底）。
  useEffect(() => () => objectUrls.current.forEach((u) => URL.revokeObjectURL(u)), []);

  const canSend = (text.trim().length > 0 || attachments.length > 0) && !sending && !uploading;

  const submit = async () => {
    if (!canSend) return; // 与按钮 disabled 同一判据；此处兜住 Enter 提交路径
    const content = text.trim();
    setInlineError(null);
    // 🔴 发送前预检（拦在开答前，对齐 BE 402 口径：available<=0 才是「压根发不了」）。
    const check = precheckSend(balance);
    if (!check.ok) {
      onInsufficient(); // 弹充值窗、**不发请求**
      return;
    }
    // 发送瞬间快照：原始文字 + 这一批附件（用于成功后「比较再清空」，见下）。
    const sentText = text;
    const sentAttachments = attachments;
    // 🔴 P1-1：**await 结果，只有成功才清空**；失败（网络/402/422/502）→ 全部保留，不必重打重传，预览也不碎。
    const sent = await onSend({ content, tier, attachment_asset_ids: sentAttachments.map((a) => a.asset_id) });
    if (!sent) return;
    // 🔴 FIX5：await 打开了异步窗口——期间用户可能已边等边打下一条 / 加了新附件 / 用语音继续听写。**只清「仍等于
    //   发送快照」的部分**，决不无条件清空（否则把窗口期的新草稿、新附件、语音前缀一起抹掉；聊天里边等回复边打下
    //   一条是标准行为，不锁输入）。读 ref 取「窗口结束那一刻」的最新值（闭包里的 text/attachments 是发送瞬间旧值）。
    const currentText = textRef.current;
    const currentAttachments = attachmentsRef.current;
    if (currentText === sentText) {
      setText("");
      // 语音前缀基准（voiceBase = 开录前已有文字）**只在文字确实被清时才重置**——否则会截断窗口期仍在进行的语音草稿
      //   （下一次识别回调会 setText(voiceBase + t)，base 被清空 = 丢掉开录前的前缀）。与文字清空同一判据。
      voiceBase.current = "";
    }
    // 附件：这批仍原样未动才清空 + revoke **本批** 预览；窗口期加/删/换过 → 全保留、**不 revoke 任何 URL**
    //   （尤其不能 revoke 新附件的 URL——否则附件在、预览碎，等于把上一轮修好的一半又弄坏）。
    const attachmentsUntouched =
      currentAttachments.length === sentAttachments.length &&
      currentAttachments.every((a, i) => a.asset_id === sentAttachments[i].asset_id);
    if (attachmentsUntouched && sentAttachments.length > 0) {
      const sentUrls = new Set(sentAttachments.map((a) => a.preview_url));
      sentAttachments.forEach((a) => URL.revokeObjectURL(a.preview_url));
      objectUrls.current = objectUrls.current.filter((u) => !sentUrls.has(u));
      setAttachments([]);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  };

  const onPickImage = async (file: File | undefined) => {
    if (!file) return;
    const invalid = validateImageFile(file);
    if (invalid) {
      setInlineError(invalid);
      return;
    }
    setInlineError(null);
    setUploading(true);
    try {
      // /uploads/images → asset_id（BE 附件校验认 asset_id + 图片类型）。
      const { asset_id } = await uploadImage.mutateAsync(file);
      const preview_url = URL.createObjectURL(file);
      objectUrls.current.push(preview_url);
      setAttachments((prev) => [...prev, { asset_id, name: file.name, preview_url }]);
    } catch (err) {
      setInlineError(err instanceof ApiError ? err.message : copy.aibrain.uploadFailed);
    } finally {
      setUploading(false);
      if (imageInput.current) imageInput.current.value = "";
    }
  };

  const toggleVoice = () => {
    if (voice.listening) {
      voice.stop();
    } else {
      voiceBase.current = text ? `${text} ` : "";
      voice.start();
    }
  };

  return (
    <div className="flex flex-col gap-2 rounded-card border border-line-gold bg-glass-fill p-3 shadow-glass">
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {attachments.map((att, i) => (
            <span key={att.asset_id} className="inline-flex items-center gap-1.5 rounded-mark border border-line-gold bg-glass-soft py-1 pl-1 pr-1.5 text-[12px] text-ink-soft">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={att.preview_url} alt={att.name} className="h-8 w-8 rounded object-cover" />
              <span className="max-w-[120px] truncate">{att.name}</span>
              <button
                type="button"
                aria-label={copy.aibrain.attachRemove}
                onClick={() => {
                  URL.revokeObjectURL(att.preview_url);
                  objectUrls.current = objectUrls.current.filter((u) => u !== att.preview_url);
                  setAttachments((prev) => prev.filter((_, j) => j !== i));
                }}
                className="rounded p-0.5 text-ink-faint outline-none transition-colors hover:text-error-fg focus-visible:shadow-focus-gold"
              >
                <X size={13} strokeWidth={2} />
              </button>
            </span>
          ))}
        </div>
      )}

      <label htmlFor="aibrain-composer" className="sr-only">{copy.aibrain.inputPlaceholder}</label>
      <textarea
        id="aibrain-composer"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        rows={2}
        placeholder={voice.listening ? copy.aibrain.voiceListening : copy.aibrain.inputPlaceholder}
        className="w-full resize-y rounded-field bg-transparent px-1 py-1 text-sm leading-relaxed text-ink outline-none placeholder:text-ink-faint"
      />

      {inlineError && (
        <p role="alert" className="rounded-field bg-error-bg px-3 py-1.5 text-[12.5px] text-error-fg">
          {inlineError}
        </p>
      )}

      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <IntensitySelector value={tier} onChange={onTierChange} disabled={sending} />

          <input ref={imageInput} type="file" accept="image/jpeg,image/png,image/webp" className="hidden" onChange={(e) => void onPickImage(e.target.files?.[0])} />
          <Button variant="icon" size="icon" className="h-9 w-9" aria-label={copy.aibrain.attachImage} onClick={() => imageInput.current?.click()} disabled={uploading || sending}>
            <ImagePlus size={16} strokeWidth={1.8} />
          </Button>

          {/* 语音：不支持则隐藏（优雅降级，不报错）。 */}
          {voice.supported ? (
            <Button
              variant={voice.listening ? "primary" : "icon"}
              size="icon"
              className="h-9 w-9"
              aria-label={voice.listening ? copy.aibrain.voiceStop : copy.aibrain.voiceStart}
              aria-pressed={voice.listening}
              onClick={toggleVoice}
              disabled={sending}
            >
              {voice.listening ? <MicOff size={16} strokeWidth={1.8} /> : <Mic size={16} strokeWidth={1.8} />}
            </Button>
          ) : (
            <span className="hidden sm:inline text-[11px] text-ink-faint" title={copy.aibrain.voiceUnsupported}>
              {copy.aibrain.voiceUnsupported}
            </span>
          )}
        </div>

        <Button variant="primary" size="sm" onClick={() => void submit()} disabled={!canSend} aria-label={copy.aibrain.send}>
          {sending || uploading ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} strokeWidth={2} />}
          {sending ? copy.aibrain.sending : copy.aibrain.send}
        </Button>
      </div>
    </div>
  );
}
