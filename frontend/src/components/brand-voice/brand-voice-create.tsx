"use client";

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { Lock, Mic, Square, Trash2, Upload } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useCreateBrandVoice } from "@/lib/api/hooks";
import type { BrandVoiceProvider } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-context";
import { canUseVipVoiceClone } from "@/lib/auth/vip";
import { useAudioRecorder } from "@/lib/media/use-audio-recorder";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const MIN_DURATION_SEC = 5;
// 豆包 VIP 通路扣费额度（范围4）。TODO(pricing)：优先从后端费率/报价接口读取；develop 的 routes/quota.py
// 仅有订阅额度 GET、无单项报价端点，故暂置常量（文案定稿 300 元 = 30000 积分），接口就绪后替换。
const DOUBAO_CLONE_CREDITS = 30000;
const PROVIDERS: { id: BrandVoiceProvider; title: string; desc: string }[] = [
  { id: "cosyvoice", title: copy.brandVoice.providerCosyTitle, desc: copy.brandVoice.providerCosyDesc },
  { id: "doubao", title: copy.brandVoice.providerDoubaoTitle, desc: copy.brandVoice.providerDoubaoDesc }
];
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
  const { session, ready } = useAuth();
  const uploadInputRef = useRef<HTMLInputElement>(null);

  const [name, setName] = useState("");
  const [consent, setConsent] = useState(false);
  // 克隆通路（范围4）：**缺省 doubao 通路**（与现状一致，兼容承重——现有克隆即豆包付费）；doubao 需
  // 创建前扣费确认。cosyvoice 为 COSYVOICE-CLONE-0001 新增的免费档，需用户主动选择。
  const [provider, setProvider] = useState<BrandVoiceProvider>("doubao");
  const [chargeOpen, setChargeOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // VIP 门禁（ADMIN-VIP-GATE-UI-0001 §二之二）：非 huading（且非 admin）→ doubao 卡置灰、不可选。
  // vipLocked 仅在 session 就绪后为真（避免加载态误判）；非授权时把缺省 doubao 自动切到免费档 cosyvoice，
  // 使表单落在可用通路（不让用户停在被禁选项、也不让提交撞 403）。
  const vipLocked = ready && !canUseVipVoiceClone(session);
  useEffect(() => {
    if (vipLocked && provider === "doubao") setProvider("cosyvoice");
  }, [vipLocked, provider]);

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

  // 实际创建（带 provider 通路）；成功清空回初始态、关扣费窗。仅在校验通过（含 doubao 扣费确认）后调用。
  const doCreate = async () => {
    if (!recorder.blob) return;
    try {
      // 三段式：上传音频→JSON 创建；consent_confirmed + provider 进 body（勾选才到此，consent 恒 true）。
      await create.mutateAsync({ name: name.trim(), audio: recorder.blob, consentConfirmed: consent, provider });
      // 成功：清空，回到初始态（通路复位缺省 doubao）。
      recorder.reset();
      setName("");
      setConsent(false);
      setProvider("doubao");
      setChargeOpen(false);
    } catch (err) {
      setChargeOpen(false);
      setError(errorText(err));
    }
  };

  const onCreate = () => {
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
    // VIP 门禁 defensive backstop：非授权用户绝不发 doubao 创建请求（正常已被置灰 + 自动切 cosyvoice，此为兜底）。
    if (provider === "doubao" && vipLocked) {
      setError(copy.errors.voiceClonePlanRequired);
      return;
    }
    // doubao 付费通路：创建前明确扣费确认（30000 积分）；cosyvoice 免费直建。
    if (provider === "doubao") {
      setChargeOpen(true);
      return;
    }
    void doCreate();
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

      {/* 克隆通路（范围4）：两档单选卡片；doubao 付费创建前扣费确认，cosyvoice 免费。 */}
      <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
        <legend className={labelClass}>{copy.brandVoice.providerSectionLabel}</legend>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {PROVIDERS.map((p) => {
            const locked = p.id === "doubao" && vipLocked; // VIP 通路对非授权用户置灰
            return (
              <SelectableOption
                key={p.id}
                selected={provider === p.id}
                disabled={locked}
                onSelect={() => setProvider(p.id)}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-ink">{p.title}</span>
                  <span className="block truncate text-[11.5px] text-ink-faint">
                    {locked ? copy.brandVoice.providerVipLocked : p.desc}
                  </span>
                </span>
                {locked && <Lock size={14} strokeWidth={1.8} className="flex-none text-ink-faint" aria-hidden />}
              </SelectableOption>
            );
          })}
        </div>
      </fieldset>

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

      <Button variant="primary" size="lg" className="w-full" onClick={onCreate} disabled={createDisabled}>
        {create.isPending ? copy.brandVoice.creating : copy.brandVoice.create}
      </Button>

      {/* doubao 付费通路扣费确认（明确 30000 积分；确认后才发创建请求）。 */}
      <ConfirmDialog
        open={chargeOpen}
        title={copy.brandVoice.chargeConfirmTitle}
        message={copy.brandVoice.chargeConfirmMessage(DOUBAO_CLONE_CREDITS)}
        confirmLabel={copy.brandVoice.chargeConfirmBtn}
        submitting={create.isPending}
        onConfirm={() => void doCreate()}
        onCancel={() => setChargeOpen(false)}
      />
    </Card>
  );
}
