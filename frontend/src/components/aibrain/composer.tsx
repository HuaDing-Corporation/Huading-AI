"use client";

// 华鼎AI智脑 · 输入框（AIBRAIN-UI-0001）—— 本组件承载**发送前的核心承重**：
//  1. 档位（tier）随请求传；
//  2. **余额预检拦在开答前**：不足 → 弹充值窗、**不发请求**；超单次上限 → friendly 提示、不发请求。
// 图片复用既有 /uploads（jpeg/png/webp ≤10MiB）；文档 pdf/docx/txt 只收下（mock 已收到）；语音走 Web Speech（不支持则隐藏）。

import { useRef, useState, type KeyboardEvent } from "react";
import { ImagePlus, Loader2, Mic, MicOff, Paperclip, Send, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";
import { ApiError } from "@/lib/api/client";
import { useUploadProductImage } from "@/lib/api/hooks";
import { validateImageFile } from "@/lib/api/uploads";
import { uploadDocument } from "@/lib/aibrain/api";
import { precheckSend, SINGLE_TURN_LIMIT, type ChatAttachment, type IntensityTier, type SendMessageRequest } from "@/lib/aibrain/types";
import { IntensitySelector } from "@/components/aibrain/intensity-selector";
import { useVoiceInput } from "@/components/aibrain/use-voice-input";

const DOC_TYPES = ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "text/plain"];

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
  balance: number;
  sending: boolean;
  onSend: (body: SendMessageRequest) => void;
  /** 余额不足 → 让父层弹充值窗（不是普通报错）。 */
  onInsufficient: (reserve: number) => void;
}) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const imageInput = useRef<HTMLInputElement>(null);
  const docInput = useRef<HTMLInputElement>(null);
  const voiceBase = useRef("");
  const uploadImage = useUploadProductImage();

  const voice = useVoiceInput((t) => setText(voiceBase.current + t));

  const canSend = (text.trim().length > 0 || attachments.length > 0) && !sending && !uploading;

  const submit = () => {
    const content = text.trim();
    if ((!content && attachments.length === 0) || sending || uploading) return;
    setInlineError(null);
    // 🔴 发送前预检（拦在开答前）。
    const check = precheckSend(balance, tier, attachments);
    if (!check.ok) {
      if (check.reason === "insufficient") {
        onInsufficient(check.reserve); // 弹充值窗、**不发请求**
      } else {
        setInlineError(copy.aibrain.overLimit(SINGLE_TURN_LIMIT)); // 超上限 friendly、不发请求
      }
      return;
    }
    onSend({ content, tier, attachments: attachments.length ? attachments : undefined });
    setText("");
    setAttachments([]);
    voiceBase.current = "";
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
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
      const { image_key } = await uploadImage.mutateAsync(file);
      setAttachments((prev) => [...prev, { kind: "image", ref: image_key, name: file.name, preview_url: URL.createObjectURL(file) }]);
    } catch (err) {
      setInlineError(err instanceof ApiError ? err.message : copy.aibrain.uploadFailed);
    } finally {
      setUploading(false);
      if (imageInput.current) imageInput.current.value = "";
    }
  };

  const onPickDoc = async (file: File | undefined) => {
    if (!file) return;
    if (!DOC_TYPES.includes(file.type)) {
      setInlineError(copy.aibrain.docTypeError);
      return;
    }
    setInlineError(null);
    setUploading(true);
    try {
      const att = await uploadDocument(file);
      setAttachments((prev) => [...prev, att]);
    } catch (err) {
      setInlineError(err instanceof ApiError ? err.message : copy.aibrain.uploadFailed);
    } finally {
      setUploading(false);
      if (docInput.current) docInput.current.value = "";
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
            <span key={`${att.ref}-${i}`} className="inline-flex items-center gap-1.5 rounded-mark border border-line-gold bg-glass-soft py-1 pl-2 pr-1 text-[12px] text-ink-soft">
              <span className="max-w-[160px] truncate">{att.name}</span>
              {att.kind === "document" ? <span className="text-ink-faint">· {copy.aibrain.docReceived}</span> : null}
              <button
                type="button"
                aria-label={copy.aibrain.attachRemove}
                onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
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

          <input ref={docInput} type="file" accept=".pdf,.docx,.txt" className="hidden" onChange={(e) => void onPickDoc(e.target.files?.[0])} />
          <Button variant="icon" size="icon" className="h-9 w-9" aria-label={copy.aibrain.attachDocument} onClick={() => docInput.current?.click()} disabled={uploading || sending}>
            <Paperclip size={16} strokeWidth={1.8} />
          </Button>

          {/* 语音：不支持则**隐藏**（优雅降级，不报错）——title 说明。 */}
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

        <Button variant="primary" size="sm" onClick={submit} disabled={!canSend} aria-label={copy.aibrain.send}>
          {sending || uploading ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} strokeWidth={2} />}
          {sending ? copy.aibrain.sending : copy.aibrain.send}
        </Button>
      </div>
    </div>
  );
}
