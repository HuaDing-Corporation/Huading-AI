"use client";

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Lock, Mic, Square, Trash2, Upload } from "lucide-react";

import { BillingStatus } from "@/components/billing/billing-status";
import { PricingConfirmDialog } from "@/components/billing/pricing-confirm-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SelectableOption } from "@/components/ui/selectable-option";
import { createBrandVoiceOrder, estimateBrandVoiceOrder, type BrandVoiceOrderInput, type BrandVoiceOrderRead } from "@/lib/api/brand-voice-orders";
import { createBrandVoice, estimateBrandVoice, uploadAudio, type BrandVoiceCreateResponse } from "@/lib/api/brand-voices";
import { errorText } from "@/lib/api/error-text";
import { brandVoiceKeys, brandVoiceOrderKeys, voicesKey } from "@/lib/api/keys";
import type { BillingOperationLookupFor, BillingQuote, BrandVoice, BrandVoiceCreateBody, BrandVoiceProvider } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-context";
import { canUseVipVoiceClone } from "@/lib/auth/vip";
import { useBillingAction } from "@/lib/billing/use-billing-action";
import { copy } from "@/lib/copy";
import { useAudioRecorder } from "@/lib/media/use-audio-recorder";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const MIN_DURATION_SEC = 5;
const MAX_AUDIO_BYTES = 20 * 1024 * 1024;
const ALLOWED_AUDIO_TYPES = ["audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3", "audio/mp4", "audio/x-m4a", "audio/m4a", "audio/aac"];
const PROVIDERS: { id: BrandVoiceProvider; title: string; desc: string }[] = [
  { id: "cosyvoice", title: copy.brandVoice.providerCosyTitle, desc: "自动创建；创建与后续使用价格均以服务端报价为准" },
  { id: "doubao", title: copy.brandVoice.providerDoubaoTitle, desc: "提交人工订单，由平台交付；交付后有效 365 天" }
];

export interface BrandVoiceCreateProps {
  renewVoice?: BrandVoice | null;
  onFinished?: () => void;
}

export function BrandVoiceCreate({ renewVoice = null, onFinished }: BrandVoiceCreateProps) {
  const queryClient = useQueryClient();
  const recorder = useAudioRecorder();
  const recorderResetRef = useRef(recorder.reset);
  const handledOrderResultRef = useRef<string | null>(null);
  const handledCosyResultRef = useRef<string | null>(null);
  const { session, ready } = useAuth();
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState(renewVoice?.name ?? "");
  const [consent, setConsent] = useState(false);
  const [provider, setProvider] = useState<BrandVoiceProvider>(renewVoice ? "doubao" : "doubao");
  const [cosyInput, setCosyInput] = useState<BrandVoiceCreateBody | null>(null);
  const [orderInput, setOrderInput] = useState<BrandVoiceOrderInput | null>(null);
  const [pricingOpen, setPricingOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const vipLocked = ready && !canUseVipVoiceClone(session);
  const tooShort = recorder.durationSec > 0 && recorder.durationSec < MIN_DURATION_SEC;
  const audioReady = !!recorder.blob && !tooShort;

  useEffect(() => {
    recorderResetRef.current = recorder.reset;
  }, [recorder.reset]);

  useEffect(() => {
    if (renewVoice) {
      setName(renewVoice.name);
      setProvider("doubao");
    } else if (vipLocked && provider === "doubao") {
      setProvider("cosyvoice");
    }
  }, [provider, renewVoice, vipLocked]);

  const cosyBilling = useBillingAction<BrandVoiceCreateBody, BillingQuote, BrandVoiceCreateResponse, "cosyvoice_brand_voice_create">({
    operation: "cosyvoice_brand_voice_create",
    input: cosyInput,
    estimate: estimateBrandVoice,
    submit: createBrandVoice,
    resultFromLookup: (lookup: BillingOperationLookupFor<"cosyvoice_brand_voice_create">) =>
      lookup.state === "completed" && lookup.completion_kind === "succeeded" && lookup.result_type === "brand_voice"
        ? ({ ...lookup.result, billing: lookup.billing } as BrandVoiceCreateResponse)
        : null
  });
  const orderOperation = renewVoice ? "doubao_brand_voice_order_renew" : "doubao_brand_voice_order_create";
  const orderBilling = useBillingAction<BrandVoiceOrderInput, BillingQuote, BrandVoiceOrderRead, typeof orderOperation>({
    operation: orderOperation,
    input: orderInput,
    estimate: estimateBrandVoiceOrder,
    submit: createBrandVoiceOrder,
    resultFromLookup: (lookup) =>
      lookup.state === "completed" && lookup.completion_kind === "succeeded" && lookup.result_type === "brand_voice_order"
        ? lookup.result
        : null
  });
  const activeBilling = provider === "doubao" ? orderBilling : cosyBilling;

  useEffect(() => {
    if ((provider === "doubao" ? orderInput : cosyInput) !== null) setPricingOpen(true);
  }, [cosyInput, orderInput, provider]);

  useEffect(() => {
    if (orderBilling.phase === "succeeded" && orderBilling.result) {
      const resultKey = `${orderBilling.result.id}:${orderBilling.result.billing.operation_id}`;
      if (handledOrderResultRef.current === resultKey) return;
      handledOrderResultRef.current = resultKey;
      setPricingOpen(false);
      setSuccess("订单已提交，请在「人工开通订单」查询最新交付及退款状态。");
      recorderResetRef.current();
      setConsent(false);
      setOrderInput(null);
      void queryClient.invalidateQueries({ queryKey: brandVoiceOrderKeys.all });
      void queryClient.invalidateQueries({ queryKey: brandVoiceKeys.all });
      void queryClient.invalidateQueries({ queryKey: voicesKey });
      onFinished?.();
    }
  }, [onFinished, orderBilling.phase, orderBilling.result, queryClient]);

  useEffect(() => {
    if (cosyBilling.phase === "succeeded" && cosyBilling.result) {
      const resultKey = `${cosyBilling.result.id}:${cosyBilling.result.billing.operation_id}`;
      if (handledCosyResultRef.current === resultKey) return;
      handledCosyResultRef.current = resultKey;
      setPricingOpen(false);
      const settled = cosyBilling.result.billing.settled_credits;
      setSuccess(settled === 0 ? "创建成功，本次创建免费（扣除 0 积分）" : `创建成功，已结算 ${settled} 积分`);
      recorderResetRef.current();
      setConsent(false);
      setCosyInput(null);
      void queryClient.invalidateQueries({ queryKey: brandVoiceOrderKeys.all });
      void queryClient.invalidateQueries({ queryKey: brandVoiceKeys.all });
      void queryClient.invalidateQueries({ queryKey: voicesKey });
      onFinished?.();
    }
  }, [cosyBilling.phase, cosyBilling.result, onFinished, queryClient]);

  const onUpload = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (uploadInputRef.current) uploadInputRef.current.value = "";
    if (!file) return;
    setError(null);
    if (!ALLOWED_AUDIO_TYPES.includes(file.type)) return setError(copy.errors.audioType);
    if (file.size > MAX_AUDIO_BYTES) return setError(copy.errors.audioTooLarge);
    recorder.setExternal(file);
  };

  const onSubmit = async () => {
    setError(null);
    setSuccess(null);
    if (!recorder.blob) return setError(copy.brandVoice.createNeedAudio);
    if (tooShort) return setError(copy.errors.audioTooShort);
    if (!name.trim()) return setError(copy.brandVoice.createNeedName);
    if (!consent) return setError(copy.brandVoice.consentRequired);
    if (provider === "doubao" && vipLocked) return setError(copy.errors.voiceClonePlanRequired);
    setUploading(true);
    if (provider === "doubao") handledOrderResultRef.current = null;
    else handledCosyResultRef.current = null;
    try {
      const uploaded = await uploadAudio(recorder.blob);
      if (provider === "doubao") {
        setOrderInput({
          order_type: renewVoice ? "renew" : "create",
          requested_name: name.trim(),
          source_audio_asset_id: uploaded.asset_id,
          consent_confirmed: true,
          existing_brand_voice_id: renewVoice?.id ?? null
        });
        setCosyInput(null);
      } else {
        setCosyInput({
          name: name.trim(),
          source_audio_asset_id: uploaded.asset_id,
          consent_confirmed: true,
          provider: "cosyvoice"
        });
        setOrderInput(null);
      }
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setUploading(false);
    }
  };

  return (
    <Card animateIn>
      <CardTitle>{renewVoice ? `续期：${renewVoice.name}` : copy.brandVoice.createTitle}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">豆包采用人工订单交付；CosyVoice 创建和使用价格均由服务端报价。</CardSubtitle>

      <div className="mb-[15px]">
        <p className="mb-2 rounded-field border border-line-gold bg-glass-fill px-3 py-2.5 text-[13px] leading-relaxed text-ink-soft">{copy.brandVoice.recordPrompt}</p>
        {recorder.supported ? (
          <div className="flex items-center gap-2">
            {recorder.recording ? (
              <Button variant="primary" size="sm" onClick={recorder.stop}><Square size={14} /> {copy.brandVoice.recordStop}</Button>
            ) : (
              <Button variant="soft" size="sm" onClick={() => void recorder.start()}><Mic size={14} /> {recorder.blob ? copy.brandVoice.recordAgain : copy.brandVoice.recordStart}</Button>
            )}
            {recorder.recording && <span className="text-[12.5px] text-ink-soft">{copy.brandVoice.recording} <span aria-hidden>{recorder.durationSec.toFixed(0)}s</span></span>}
          </div>
        ) : <p className="text-[12.5px] text-ink-soft">{copy.brandVoice.recordUnsupported}</p>}
        {recorder.error && <p role="alert" className="mt-1.5 text-[12.5px] text-error-fg">{recorder.error}</p>}
        {tooShort && <p role="alert" className="mt-1.5 text-[12.5px] text-error-fg">{copy.errors.audioTooShort}</p>}
      </div>

      <div className="mb-[15px]">
        <label className={labelClass}>{copy.brandVoice.orUpload}</label>
        <input ref={uploadInputRef} id="brand-voice-audio" type="file" accept=".wav,.mp3,.m4a,audio/*" className="hidden" onChange={onUpload} />
        <button type="button" onClick={() => uploadInputRef.current?.click()} className="flex w-full items-center justify-center gap-2 rounded-field border border-dashed border-line-gold bg-glass-fill py-4 text-[13px] text-ink-soft hover:bg-glass-hover"><Upload size={16} /> {copy.brandVoice.uploadAudio}</button>
      </div>

      {recorder.url && (
        <div className="mb-[15px] flex items-center gap-3 rounded-field border border-line-gold bg-glass-fill p-2.5">
          <audio controls src={recorder.url} aria-label={copy.brandVoice.previewAria} className="h-9 min-w-0 flex-1" />
          <button type="button" onClick={recorder.reset} aria-label={copy.brandVoice.removeAudio} className="flex h-8 w-8 items-center justify-center text-ink-soft"><Trash2 size={16} /></button>
        </div>
      )}

      <div className="mb-[15px]">
        <label htmlFor="brand-voice-name" className={labelClass}>{copy.brandVoice.nameLabel}</label>
        <Input id="brand-voice-name" value={name} maxLength={30} onChange={(event) => setName(event.target.value.slice(0, 30))} placeholder={copy.brandVoice.namePlaceholder} />
      </div>

      {!renewVoice && (
        <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
          <legend className={labelClass}>{copy.brandVoice.providerSectionLabel}</legend>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {PROVIDERS.map((item) => {
              const locked = item.id === "doubao" && vipLocked;
              return <SelectableOption key={item.id} selected={provider === item.id} disabled={locked} onSelect={() => setProvider(item.id)}>
                <span className="min-w-0 flex-1"><span className="block text-ink">{item.title}</span><span className="block break-words text-[11.5px] text-ink-faint">{locked ? copy.brandVoice.providerVipLocked : item.desc}</span></span>
                {locked && <Lock size={14} aria-hidden />}
              </SelectableOption>;
            })}
          </div>
        </fieldset>
      )}

      <label className="mb-3 flex items-start gap-2.5 text-[12.5px] leading-relaxed text-ink-soft">
        <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} className="mt-0.5 h-4 w-4 accent-gold-deep" />
        <span>我确认拥有本次新录音/上传音频的完整授权，并同意用于本次音色{renewVoice ? "续期" : "开通"}。</span>
      </label>
      {error && <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">{error}</p>}
      {success && <p role="status" className="mb-3 rounded-field bg-success-bg px-3 py-2 text-[13px] leading-5 text-success-fg">{success}</p>}
      <Button className="w-full" size="lg" onClick={() => void onSubmit()} disabled={uploading || !audioReady || !name.trim()}>{uploading ? "正在上传…" : renewVoice ? "提交续期" : "提交开通"}</Button>

      {provider === "doubao" && activeBilling.quote && <p className="sr-only">本次冻结 {activeBilling.quote.payable_credits} 积分</p>}
      <PricingConfirmDialog
        open={pricingOpen}
        phase={activeBilling.phase}
        quote={activeBilling.quote}
        expiresInSeconds={activeBilling.expiresInSeconds}
        errorMessage={activeBilling.errorMessage}
        billing={activeBilling.billing}
        billingQuerying={activeBilling.phase === "querying"}
        onContinueLookup={() => void activeBilling.continueLookup()}
        onEstimate={() => void activeBilling.estimate()}
        onConfirm={() => void activeBilling.confirm()}
        onCancel={() => {
          setPricingOpen(false);
          setOrderInput(null);
          setCosyInput(null);
          activeBilling.reset();
        }}
        confirmLabel={provider === "doubao" ? "确认并提交人工开通" : "确认并创建"}
      />
      {activeBilling.billing && !pricingOpen && activeBilling.phase !== "succeeded" && <BillingStatus summary={activeBilling.billing} querying={activeBilling.phase === "querying"} onContinueLookup={() => void activeBilling.continueLookup()} />}
    </Card>
  );
}
