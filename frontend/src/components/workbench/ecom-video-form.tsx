"use client";

import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useScenePromptGenerate, useScriptGenerate, useUploadProductImage, useVoices } from "@/lib/api/hooks";
import { useGenerateConfirm } from "@/lib/api/use-generate-confirm";
import { useTrackedUpload } from "@/lib/api/use-tracked-upload";
import type { CreateVideoRequest } from "@/lib/api/types";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { AiTextField } from "@/components/workbench/ai-text-field";
import { ConfirmGenerateDialog } from "@/components/workbench/confirm-generate-dialog";
import { Input } from "@/components/ui/input";
import { DurationPicker, isValidDuration } from "@/components/workbench/duration-picker";
import { ImagePicker } from "@/components/workbench/image-picker";
import { MoreSettings } from "@/components/workbench/more-settings";
import { ScriptReview } from "@/components/workbench/script-review";
import { VoicePicker } from "@/components/workbench/voice-picker";
import { AiLabelToggle } from "@/components/label/ai-label-toggle";
import { useLabelTogglePreference } from "@/lib/preferences/label-toggle";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * 电商带货 (seedance_i2v) workbench container — mirrors NewVideoForm but the
 * required asset is a PRODUCT IMAGE uploaded via POST /uploads → image_key (not
 * the avatar's /uploads/images → asset_id), and the submit body carries
 * video_mode:"seedance_i2v" + image_key. The ONLY hooks caller here; the upload,
 * error mapping and race-guard are reused (useTrackedUpload / errorText) so this
 * shares logic with NewVideoForm rather than duplicating it.
 */
export function EcomVideoForm({
  initialScript,
  onPrefillConsumed
}: { initialScript?: string; onPrefillConsumed?: () => void } = {}) {
  const { createAndTrack } = useVideoTasks();
  const scriptGen = useScriptGenerate();
  const scenePromptGen = useScenePromptGenerate();
  const uploadProduct = useUploadProductImage();
  const voices = useVoices();
  const productImage = useTrackedUpload(uploadProduct.mutateAsync, (r) => r.image_key);

  const [topic, setTopic] = useState("");
  // 文案模式「用此文案」一次性 prefill：惰性消费 initialScript，mount 后回调 page 清空。
  const [script, setScript] = useState(() => initialScript ?? "");
  const [scenePrompt, setScenePrompt] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [durationSec, setDurationSec] = useState(30);
  const [speed, setSpeed] = useState(1);
  const [applyLabel, setApplyLabel] = useLabelTogglePreference(); // AI 标识开关（默认关，localStorage 记忆）
  const [error, setError] = useState<string | null>(null);
  const prefillConsumed = useRef(false);
  useEffect(() => {
    if (!prefillConsumed.current && initialScript !== undefined) {
      prefillConsumed.current = true;
      onPrefillConsumed?.();
    }
  }, [initialScript, onPrefillConsumed]);

  // Actual submit — runs only after the 确定生成 confirmation; owns its own errors.
  const submit = async (req: CreateVideoRequest) => {
    setError(null);
    try {
      await createAndTrack(req, req.topic);
    } catch (err) {
      setError(errorText(err));
    }
  };
  const confirm = useGenerateConfirm(submit);

  // Default the voice to the first loaded option (once), without clobbering a pick.
  const voiceList = voices.data;
  useEffect(() => {
    if (!voiceId && voiceList && voiceList.length > 0) setVoiceId(voiceList[0].id);
  }, [voiceId, voiceList]);

  const onGenerateScript = async () => {
    const trimmed = topic.trim();
    if (!trimmed || scriptGen.isPending) return;
    setError(null);
    try {
      const res = await scriptGen.mutateAsync({
        topic: trimmed,
        video_mode: "seedance_i2v",
        duration_sec: durationSec
      });
      setScript(res.script);
    } catch (err) {
      setError(errorText(err));
    }
  };

  // 画面提示词 AI 生成 — decoupled from 口播 (separate endpoint + state).
  const onGenerateScenePrompt = async () => {
    const trimmed = topic.trim();
    if (!trimmed || scenePromptGen.isPending) return;
    setError(null);
    try {
      const res = await scenePromptGen.mutateAsync(trimmed);
      setScenePrompt(res.scene_prompt);
    } catch (err) {
      setError(errorText(err));
    }
  };

  // Run existing validation, then open the confirm dialog instead of submitting.
  const onGenerate = () => {
    const trimmed = topic.trim();
    if (!trimmed || !voiceId || !productImage.value || !isValidDuration(durationSec)) return;
    setError(null);
    confirm.requestConfirm({
      topic: trimmed,
      script: script.trim() || undefined,
      video_mode: "seedance_i2v",
      image_key: productImage.value,
      voice_id: voiceId,
      scene_prompt: scenePrompt.trim() || undefined,
      duration_sec: durationSec,
      speed,
      aspect_ratio: "9:16",
      subtitle_enabled: true,
      apply_visible_label: applyLabel
    });
  };

  const generateDisabled =
    uploadProduct.isPending ||
    !topic.trim() ||
    !voiceId ||
    !productImage.value ||
    !isValidDuration(durationSec);

  // Tell the user which required input is still missing (validation feedback).
  let hint: string | null = null;
  if (!topic.trim()) hint = copy.workbench.ecomTopicRequired;
  else if (!productImage.value) hint = copy.workbench.ecomImageRequired;

  return (
    <Card animateIn>
      <CardTitle>{copy.workbench.ecomTitle}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">{copy.workbench.ecomSubtitle}</CardSubtitle>

      {/* 视频时长置顶：先定时长 → 再写贴合时长的文案（同一 durationSec 驱动文案/视频/字幕）。 */}
      <DurationPicker
        value={durationSec}
        onChange={setDurationSec}
        label={copy.workbench.durationLabelAligned}
      />

      <div className="mb-[15px]">
        <label htmlFor="ecom-topic" className={labelClass}>
          {copy.workbench.ecomTopicLabel}
        </label>
        <Input
          id="ecom-topic"
          name="ecom-topic"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder={copy.workbench.ecomTopicPlaceholder}
        />
      </div>

      <ScriptReview
        script={script}
        onChange={setScript}
        onRegenerate={onGenerateScript}
        loading={scriptGen.isPending}
        speed={speed}
        label={copy.workbench.ecomScriptLabel}
      />

      <AiTextField
        id="scene-prompt"
        label={copy.workbench.scenePromptLabel}
        value={scenePrompt}
        onChange={setScenePrompt}
        onAction={onGenerateScenePrompt}
        actionLabel={copy.workbench.scenePromptGenerate}
        actionIcon="generate"
        loading={scenePromptGen.isPending}
        rows={3}
        placeholder={copy.workbench.scenePromptPlaceholder}
        footer={copy.workbench.scenePromptHint}
      />

      <ImagePicker
        value={productImage.value}
        onChange={productImage.setValue}
        uploading={uploadProduct.isPending}
        onUpload={productImage.onUpload}
        uploadError={productImage.error}
        label={copy.workbench.productImageLabel}
        uploadLabel={copy.workbench.productImageUpload}
        previewAlt={copy.workbench.productImagePreviewAlt}
        inputId="product-image"
      />

      <VoicePicker voices={voiceList ?? []} value={voiceId} onChange={setVoiceId} />

      <MoreSettings speed={speed} onSpeedChange={setSpeed} />

      <AiLabelToggle checked={applyLabel} onChange={setApplyLabel} />

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      {hint && !error && (
        <p className="mb-3 text-[12.5px] text-ink-soft" aria-live="polite">
          {hint}
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
        onConfirm={confirm.confirm}
        onCancel={confirm.cancel}
      />
    </Card>
  );
}
