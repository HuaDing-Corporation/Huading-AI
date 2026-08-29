"use client";

import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { isApiError } from "@/lib/api/client";
import {
  useAvatarPresets,
  useBrandVoices,
  useSubtitleTemplates,
  useUploadAvatarVideo,
  useUploadImage,
  useVoices
} from "@/lib/api/hooks";
import { estimateScript, generateScript } from "@/lib/api/scripts";
import { useGenerateConfirm } from "@/lib/api/use-generate-confirm";
import { useTrackedUpload } from "@/lib/api/use-tracked-upload";
import type {
  BillingOperationLookupFor,
  BillingConfirmation,
  CreateVideoRequest,
  ScriptGenerateRequest,
  ScriptGenerateResponse,
  SubtitleStyle,
  VideoEstimateContract
} from "@/lib/api/types";
import { useBillingAction } from "@/lib/billing/use-billing-action";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { ConfirmGenerateDialog } from "@/components/workbench/confirm-generate-dialog";
import { PricingConfirmDialog } from "@/components/billing/pricing-confirm-dialog";
import { Input } from "@/components/ui/input";
import { SelectableOption } from "@/components/ui/selectable-option";
import { ImagePicker } from "@/components/workbench/image-picker";
import { AvatarVideoPicker } from "@/components/workbench/avatar-video-picker";
import { MoreSettings } from "@/components/workbench/more-settings";
import { ScriptReview } from "@/components/workbench/script-review";
import { VoicePicker } from "@/components/workbench/voice-picker";
import { useAuth } from "@/lib/auth/auth-context";
import { canUseVipVoiceClone } from "@/lib/auth/vip";
import { SubtitleStylePicker, isSubtitleStyleValid } from "@/components/workbench/subtitle-style-picker";
import { AiLabelToggle } from "@/components/label/ai-label-toggle";
import { useLabelTogglePreference } from "@/lib/preferences/label-toggle";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * 数字人口播 (avatar_talk) workbench container — the ONLY hooks caller. Sub-
 * components are pure props; this orchestrates script generation, the avatar
 * image upload (race-safe via useTrackedUpload), voice/avatar selection and
 * assembles the CreateVideoRequest. Errors map through the shared errorText so
 * the backend's real message surfaces (keyed on contract err.code, not status).
 */
export function NewVideoForm({
  initialTopic,
  initialScript,
  onPrefillConsumed
}: { initialTopic?: string; initialScript?: string; onPrefillConsumed?: () => void } = {}) {
  const { createAndTrack } = useVideoTasks();
  const uploadImg = useUploadImage();
  const uploadVideo = useUploadAvatarVideo();
  const { session, ready: authReady } = useAuth(); // VIP 门禁（§二之二）：doubao 品牌音色可用性
  const voices = useVoices();
  const brandVoices = useBrandVoices(); // 「选我的音色」：品牌音色(声音复刻)全状态
  const presets = useAvatarPresets();
  const subtitleTemplates = useSubtitleTemplates();
  const avatar = useTrackedUpload(uploadImg.mutateAsync, (r) => r.asset_id);
  const avatarVideo = useTrackedUpload(uploadVideo.mutateAsync, (r) => r.asset_id);
  // 形象来源二选一（AVATAR-VIDEO-SOURCE-UI-0001）：photo=现状默认(承重零回归)，video=本人出镜视频。
  const [source, setSource] = useState<"photo" | "video">("photo");
  // 切换即清空另一源的上传：避免切回残留「已上传但预览丢失」的武装态错位（Review P3），并让二选一互斥更明确。
  const switchSource = (next: "photo" | "video") => {
    if (next === source) return;
    setSource(next);
    if (next === "photo") avatarVideo.setValue(null);
    else avatar.setValue(null);
  };

  // 一次性 prefill：文案「用此文案」注 script；提示词反推「带入」注 topic+script。惰性消费，mount 后回调清空。
  const [topic, setTopic] = useState(() => initialTopic ?? "");
  const [script, setScript] = useState(() => initialScript ?? "");
  const [voiceId, setVoiceId] = useState("");
  const [speed, setSpeed] = useState(1);
  // 字幕样式(ORAL-PROD-UI-0001)：undefined = 不传 subtitle_style → 默认烧入(不回归 0001)
  const [subtitleStyle, setSubtitleStyle] = useState<SubtitleStyle | undefined>(undefined);
  const [applyLabel, setApplyLabel] = useLabelTogglePreference(); // AI 标识开关（默认关，localStorage 记忆）
  const [error, setError] = useState<string | null>(null);
  const [scriptPricingOpen, setScriptPricingOpen] = useState(false);
  const scriptInput: ScriptGenerateRequest | null = topic.trim()
    ? { topic: topic.trim() }
    : null;
  const scriptBilling = useBillingAction<
    ScriptGenerateRequest,
    Awaited<ReturnType<typeof estimateScript>>,
    ScriptGenerateResponse,
    "script_generate"
  >({
    operation: "script_generate",
    input: scriptInput,
    estimate: estimateScript,
    submit: generateScript,
    resultFromLookup: (lookup: BillingOperationLookupFor<"script_generate">) => {
      if (
        lookup.state !== "completed" ||
        lookup.completion_kind !== "succeeded" ||
        lookup.result_type !== "script_generate_result"
      ) {
        return null;
      }
      return { script: lookup.result.script, billing: lookup.billing };
    }
  });
  // WORKBENCH-KEEPALIVE-UI-0001 · prefill 消费时机重设计：工作台面板改为「挂载后常驻」，本表单不再随切 mode 重挂
  // → 上面的 mount-时惰性初始化承接不了**后到**的 prefill（值注不进来，而 effect 照样回调 clearPrefill → 载荷被
  // 当「已消费」丢弃，用户看不到值也无从重试）。故改为同步 props：只写 prefill 真正带来的字段 → 用户已填的其它
  // 输入（音色 / 语速 / 已传形象图…）原样保留。
  // 不重复注入：消费后回调 clearPrefill → 父级清空缓冲 → 下次渲染 props 回落 undefined → 这里 early-return。
  // （原 prefillConsumed ref 闩锁已删：它与组件实例同寿，常驻后永不复位 → 会让父级缓冲再也清不掉。）
  useEffect(() => {
    if (initialTopic === undefined && initialScript === undefined) return;
    if (initialTopic !== undefined) setTopic(initialTopic);
    if (initialScript !== undefined) setScript(initialScript);
    onPrefillConsumed?.();
  }, [initialTopic, initialScript, onPrefillConsumed]);

  const scriptBillingPhase = scriptBilling.phase;
  const scriptBillingResult = scriptBilling.result;
  const resetScriptBilling = scriptBilling.reset;

  useEffect(() => {
    if (scriptBillingPhase !== "succeeded" || !scriptBillingResult) return;
    setScript(scriptBillingResult.script);
    setScriptPricingOpen(false);
    resetScriptBilling();
  }, [resetScriptBilling, scriptBillingPhase, scriptBillingResult]);

  // Actual submit — runs only after the 确定生成 confirmation; owns its own errors.
  const submit = async (
    req: CreateVideoRequest,
    estimate?: VideoEstimateContract,
    confirmation?: BillingConfirmation
  ) => {
    setError(null);
    try {
      if (estimate?.pricing_contract === "billing_quote") {
        if (!confirmation) throw new Error("缺少视频报价确认信息");
        return await createAndTrack(req, req.topic ?? "", { estimate, confirmation });
      }
      if (estimate) return await createAndTrack(req, req.topic ?? "", { estimate });
      await createAndTrack(req, req.topic ?? "");
    } catch (err) {
      setError(errorText(err));
      throw err;
    }
  };
  const confirm = useGenerateConfirm(submit, {
    authoritativePricing: true,
    onEstimateError: (caught) => setError(errorText(caught))
  });
  const confirmPricingError = confirm.pricing?.error;
  const cancelConfirm = confirm.cancel;

  useEffect(() => {
    if (!isApiError(confirmPricingError) || confirmPricingError.code !== "BILLABLE_TEXT_REQUIRED") return;
    cancelConfirm();
    // Let Radix finish closing/restoring focus before moving focus to the
    // actionable field. Do not cancel this callback when close() clears the
    // pricing error on the next render; the DOM lookup is safe after unmount.
    window.setTimeout(() => document.getElementById("video-script")?.focus(), 0);
  }, [cancelConfirm, confirmPricingError]);

  // Default the voice to the first loaded option (once), without clobbering a
  // user's pick.
  const voiceList = voices.data;
  useEffect(() => {
    if (!voiceId && voiceList && voiceList.length > 0) setVoiceId(voiceList[0].id);
  }, [voiceId, voiceList]);

  const onGenerateScript = () => {
    const trimmed = topic.trim();
    if (!trimmed || scriptBilling.phase === "submitting" || scriptBilling.phase === "querying") return;
    setError(null);
    setScriptPricingOpen(true);
  };

  // 当前形象来源对应的 asset 值（照片=avatar_asset_id / 视频=avatar_video_asset_id）。
  const activeSourceValue = source === "photo" ? avatar.value : avatarVideo.value;

  // Run existing validation, then open the confirm dialog instead of submitting.
  const onGenerate = () => {
    const trimmed = topic.trim();
    if (!trimmed || !voiceId || !activeSourceValue) return;
    setError(null);
    confirm.requestConfirm({
      topic: trimmed,
      script: script.trim() || undefined,
      voice_id: voiceId,
      // 二选一互斥：仅带当前来源字段，另一路恒 undefined → JSON.stringify 丢弃。照片=现状（零回归）。
      avatar_asset_id: source === "photo" ? (avatar.value ?? undefined) : undefined,
      avatar_video_asset_id: source === "video" ? (avatarVideo.value ?? undefined) : undefined,
      speed,
      aspect_ratio: "9:16",
      subtitle_enabled: true,
      subtitle_style: subtitleStyle, // 不选 = undefined → JSON.stringify 丢弃 → 不回归 0001
      apply_visible_label: applyLabel
    });
  };

  const generateDisabled =
    uploadImg.isPending ||
    uploadVideo.isPending ||
    !topic.trim() ||
    !voiceId ||
    !activeSourceValue ||
    !isSubtitleStyleValid(subtitleStyle);

  return (
    <Card animateIn>
      <CardTitle>新建视频</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">输入主题，AI 生成文案，选形象与音色一键成片</CardSubtitle>

      <div className="mb-[15px]">
        <label htmlFor="video-topic" className={labelClass}>
          {copy.workbench.topicLabel}
        </label>
        <Input
          id="video-topic"
          name="video-topic"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder={copy.workbench.topicPlaceholder}
        />
      </div>

      <ScriptReview
        script={script}
        onChange={setScript}
        onRegenerate={onGenerateScript}
        loading={scriptBilling.phase === "submitting" || scriptBilling.phase === "querying"}
        speed={speed}
        // FIX1：ScriptReview 不传 id 即走 useId（每实例唯一）。口播这份显式传旧 id —— e2e 的 #video-script
        // 落点断言依赖它（反推「带入 · 数字人口播」）。
        id="video-script"
      />

      {/* 形象来源二选一（AVATAR-VIDEO-SOURCE-UI-0001）：照片=默认(承重零回归) / 本人出镜视频 */}
      <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
        <legend className={labelClass}>{copy.workbench.avatarSourceLabel}</legend>
        <div role="group" aria-label={copy.workbench.avatarSourceLabel} className="grid grid-cols-2 gap-2">
          <SelectableOption selected={source === "photo"} onSelect={() => switchSource("photo")} className="justify-center">
            {copy.workbench.sourcePhoto}
          </SelectableOption>
          <SelectableOption selected={source === "video"} onSelect={() => switchSource("video")} className="justify-center">
            {copy.workbench.sourceVideo}
          </SelectableOption>
        </div>
      </fieldset>

      {source === "photo" ? (
        <ImagePicker
          value={avatar.value}
          onChange={avatar.setValue}
          presets={presets.data ?? []}
          uploading={uploadImg.isPending}
          onUpload={avatar.onUpload}
          uploadError={avatar.error}
        />
      ) : (
        <AvatarVideoPicker
          value={avatarVideo.value}
          onChange={avatarVideo.setValue}
          uploading={uploadVideo.isPending}
          onUpload={avatarVideo.onUpload}
          uploadError={avatarVideo.error}
        />
      )}

      <VoicePicker
        voices={voiceList ?? []}
        value={voiceId}
        onChange={setVoiceId}
        brandVoices={brandVoices.data ?? []}
        brandVoicesLoading={brandVoices.isLoading}
        // 加载态(!ready)不锁，避免 huading 非 admin 用户在 /me 到达前 doubao 音色瞬时误锁（与 create 卡 ready 门对齐）
        canUseVip={!authReady || canUseVipVoiceClone(session)}
      />

      <SubtitleStylePicker
        templates={subtitleTemplates.data ?? []}
        value={subtitleStyle}
        onChange={setSubtitleStyle}
      />

      <MoreSettings speed={speed} onSpeedChange={setSpeed} />

      <AiLabelToggle checked={applyLabel} onChange={setApplyLabel} />

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      <Button
        variant="primary"
        size="lg"
        className="mt-2 w-full"
        onClick={onGenerate}
        disabled={generateDisabled}
      >
        <Sparkles size={18} strokeWidth={1.8} /> {copy.workbench.generate}
      </Button>

      <ConfirmGenerateDialog
        open={confirm.open}
        request={confirm.request}
        submitting={confirm.submitting}
        pricing={confirm.pricing}
        onConfirm={confirm.confirm}
        onCancel={confirm.cancel}
      />

      <PricingConfirmDialog
        open={scriptPricingOpen}
        phase={scriptBilling.phase}
        quote={scriptBilling.quote}
        expiresInSeconds={scriptBilling.expiresInSeconds}
        errorMessage={scriptBilling.errorMessage}
        onEstimate={() => void scriptBilling.estimate()}
        onConfirm={() => void scriptBilling.confirm()}
        onCancel={() => {
          setScriptPricingOpen(false);
          scriptBilling.reset();
        }}
      />
    </Card>
  );
}
