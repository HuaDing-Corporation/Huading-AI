"use client";

import { useRef, useState, type ChangeEvent } from "react";
import { Mic, Square, Trash2, Upload } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useCreateBrandVoice } from "@/lib/api/hooks";
import { useAudioRecorder } from "@/lib/media/use-audio-recorder";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const MIN_DURATION_SEC = 5;
const MAX_AUDIO_BYTES = 20 * 1024 * 1024; // 20MB
// wav/mp3/m4a 常见 MIME（不同浏览器/系统差异，宽松匹配）。
const ALLOWED_AUDIO_TYPES = ["audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3", "audio/mp4", "audio/x-m4a", "audio/m4a", "audio/aac"];

/**
 * 品牌音色创建（BRAND-VOICE-UI-0001）—— 浏览器录音(useAudioRecorder) 或 上传音频 → 试听 →
 * 名称 → 授权 checkbox → 创建。授权为 load-bearing 客户端门：未勾点击不发请求(onCreate 内 guard)
 * 并给内联错误，绝不调用创建 mutation。录音不支持时引导改用上传。文案集中 copy；token 无硬编码。
 */
export function BrandVoiceCreate() {
  const recorder = useAudioRecorder();
  const create = useCreateBrandVoice();
  const uploadInputRef = useRef<HTMLInputElement>(null);

  const [name, setName] = useState("");
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 录音(已知时长)时长不足 5s 拦截；上传文件时长未知(durationSec=0)，仅校验类型/大小。
  const tooShort = recorder.durationSec > 0 && recorder.durationSec < MIN_DURATION_SEC;
  const audioReady = !!recorder.blob && !tooShort;

  const onUpload = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (uploadInputRef.current) uploadInputRef.current.value = "";
    if (!file) return;
    setError(null);
    if (!ALLOWED_AUDIO_TYPES.includes(file.type)) {
      setError(copy.errors.audioType);
      return;
    }
    if (file.size > MAX_AUDIO_BYTES) {
      setError(copy.errors.audioTooLarge);
      return;
    }
    recorder.setExternal(file);
  };

  const onCreate = async () => {
    setError(null);
    if (!recorder.blob) {
      setError(copy.brandVoice.createNeedAudio);
      return;
    }
    if (tooShort) {
      setError(copy.errors.audioTooShort);
      return;
    }
    if (!name.trim()) {
      setError(copy.brandVoice.createNeedName);
      return;
    }
    // load-bearing：授权未勾绝不发创建请求（先红后绿守此 guard）。
    if (!consent) {
      setError(copy.brandVoice.consentRequired);
      return;
    }
    try {
      // 三段式：上传音频→JSON 创建；consent_confirmed 进 body（勾选才到此，恒 true）。
      await create.mutateAsync({ name: name.trim(), audio: recorder.blob, consentConfirmed: consent });
      // 成功：清空，回到初始态。
      recorder.reset();
      setName("");
      setConsent(false);
    } catch (err) {
      setError(errorText(err));
    }
  };

  const createDisabled = create.isPending || !audioReady || !name.trim();

  return (
    <Card animateIn>
      <CardTitle>{copy.brandVoice.createTitle}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">{copy.brandVoice.createSubtitle}</CardSubtitle>

      {/* 录音区 */}
      <div className="mb-[15px]">
        <p className="mb-2 rounded-field border border-line-gold bg-glass-fill px-3 py-2.5 text-[13px] leading-relaxed text-ink-soft">
          {copy.brandVoice.recordPrompt}
        </p>
        {recorder.supported ? (
          <div className="flex items-center gap-2">
            {recorder.recording ? (
              <Button variant="primary" size="sm" onClick={recorder.stop}>
                <Square size={14} strokeWidth={2} /> {copy.brandVoice.recordStop}
              </Button>
            ) : (
              <Button variant="soft" size="sm" onClick={() => void recorder.start()}>
                <Mic size={14} strokeWidth={2} /> {recorder.blob ? copy.brandVoice.recordAgain : copy.brandVoice.recordStart}
              </Button>
            )}
            {recorder.recording && (
              <span className="text-[12.5px] text-ink-soft">
                {/* 状态文字 live 播报一次(开始录音)；秒数纯视觉(aria-hidden)，避免逐秒刷屏 SR */}
                <span aria-live="polite">{copy.brandVoice.recording}</span>{" "}
                <span aria-hidden="true">{recorder.durationSec.toFixed(0)}s</span>
              </span>
            )}
          </div>
        ) : (
          <p className="text-[12.5px] text-ink-soft">{copy.brandVoice.recordUnsupported}</p>
        )}
        {/* 录音运行时错误(权限被拒/启动失败)即时播报 + 可见，引导改用上传 */}
        {recorder.error && (
          <p role="alert" className="mt-1.5 text-[12.5px] text-error-fg">
            {recorder.error}
          </p>
        )}
        <p className="mt-1.5 text-[12px] text-ink-faint">{copy.brandVoice.recordHint}</p>
      </div>

      {/* 上传区 */}
      <div className="mb-[15px]">
        <label className={labelClass}>{copy.brandVoice.orUpload}</label>
        <input
          ref={uploadInputRef}
          id="brand-voice-audio"
          type="file"
          accept=".wav,.mp3,.m4a,audio/*"
          className="hidden"
          onChange={onUpload}
        />
        <button
          type="button"
          onClick={() => uploadInputRef.current?.click()}
          className="flex w-full items-center justify-center gap-2 rounded-field border border-dashed border-line-gold bg-glass-fill py-4 text-[13px] text-ink-soft transition-colors hover:bg-glass-hover"
        >
          <Upload size={16} strokeWidth={1.8} /> {copy.brandVoice.uploadAudio}
        </button>
      </div>

      {/* 试听 */}
      {recorder.url && (
        <div className="mb-[15px] flex items-center gap-3 rounded-field border border-line-gold bg-glass-fill p-2.5">
          <span className="text-[12.5px] text-gold-deep">{copy.brandVoice.audioReady}</span>
          <audio controls src={recorder.url} aria-label={copy.brandVoice.previewAria} className="h-9 min-w-0 flex-1" />
          <button
            type="button"
            onClick={recorder.reset}
            aria-label={copy.brandVoice.removeAudio}
            className="flex h-8 w-8 flex-none items-center justify-center rounded-mark text-ink-soft hover:bg-glass-hover"
          >
            <Trash2 size={16} strokeWidth={2} />
          </button>
        </div>
      )}

      {tooShort && <p className="mb-3 text-[12.5px] text-error-fg">{copy.errors.audioTooShort}</p>}

      {/* 名称 */}
      <div className="mb-[15px]">
        <label htmlFor="brand-voice-name" className={labelClass}>
          {copy.brandVoice.nameLabel}
        </label>
        <Input
          id="brand-voice-name"
          value={name}
          maxLength={30}
          onChange={(e) => setName(e.target.value.slice(0, 30))}
          placeholder={copy.brandVoice.namePlaceholder}
        />
      </div>

      {/* 授权 checkbox（load-bearing 门） */}
      <label className="mb-3 flex cursor-pointer items-start gap-2.5 text-[12.5px] leading-relaxed text-ink-soft">
        <input
          type="checkbox"
          checked={consent}
          onChange={(e) => setConsent(e.target.checked)}
          className="mt-0.5 h-4 w-4 flex-none accent-gold-deep"
        />
        <span>{copy.brandVoice.consentLabel}</span>
      </label>

      <p className="mb-3 text-[12px] text-ink-faint">{copy.brandVoice.complianceHint}</p>

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      <Button variant="primary" size="lg" className="w-full" onClick={() => void onCreate()} disabled={createDisabled}>
        {create.isPending ? copy.brandVoice.creating : copy.brandVoice.create}
      </Button>
    </Card>
  );
}
