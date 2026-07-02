"use client";

import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import {
  useAvatarPresets,
  useScriptGenerate,
  useSubtitleTemplates,
  useUploadImage,
  useVoices
} from "@/lib/api/hooks";
import { useGenerateConfirm } from "@/lib/api/use-generate-confirm";
import { useTrackedUpload } from "@/lib/api/use-tracked-upload";
import type { CreateVideoRequest, SubtitleStyle } from "@/lib/api/types";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { ConfirmGenerateDialog } from "@/components/workbench/confirm-generate-dialog";
import { Input } from "@/components/ui/input";
import { ImagePicker } from "@/components/workbench/image-picker";
import { MoreSettings } from "@/components/workbench/more-settings";
import { ScriptReview } from "@/components/workbench/script-review";
import { VoicePicker } from "@/components/workbench/voice-picker";
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
  initialScript,
  onPrefillConsumed
}: { initialScript?: string; onPrefillConsumed?: () => void } = {}) {
  const { createAndTrack } = useVideoTasks();
  const scriptGen = useScriptGenerate();
  const uploadImg = useUploadImage();
  const voices = useVoices();
  const presets = useAvatarPresets();
  const subtitleTemplates = useSubtitleTemplates();
  const avatar = useTrackedUpload(uploadImg.mutateAsync, (r) => r.asset_id);

  const [topic, setTopic] = useState("");
  // 文案模式「用此文案」一次性 prefill：惰性消费 initialScript，mount 后回调 page 清空。
  const [script, setScript] = useState(() => initialScript ?? "");
  const [voiceId, setVoiceId] = useState("");
  const [speed, setSpeed] = useState(1);
  // 字幕样式(ORAL-PROD-UI-0001)：undefined = 不传 subtitle_style → 默认烧入(不回归 0001)
  const [subtitleStyle, setSubtitleStyle] = useState<SubtitleStyle | undefined>(undefined);
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

  // Default the voice to the first loaded option (once), without clobbering a
  // user's pick.
  const voiceList = voices.data;
  useEffect(() => {
    if (!voiceId && voiceList && voiceList.length > 0) setVoiceId(voiceList[0].id);
  }, [voiceId, voiceList]);

  const onGenerateScript = async () => {
    const trimmed = topic.trim();
    if (!trimmed || scriptGen.isPending) return;
    setError(null);
    try {
      const res = await scriptGen.mutateAsync({ topic: trimmed });
      setScript(res.script);
    } catch (err) {
      setError(errorText(err));
    }
  };

  // Run existing validation, then open the confirm dialog instead of submitting.
  const onGenerate = () => {
    const trimmed = topic.trim();
    if (!trimmed || !voiceId || !avatar.value) return;
    setError(null);
    confirm.requestConfirm({
      topic: trimmed,
      script: script.trim() || undefined,
      voice_id: voiceId,
      avatar_asset_id: avatar.value,
      speed,
      aspect_ratio: "9:16",
      subtitle_enabled: true,
      subtitle_style: subtitleStyle, // 不选 = undefined → JSON.stringify 丢弃 → 不回归 0001
      apply_visible_label: applyLabel
    });
  };

  const generateDisabled =
    uploadImg.isPending || !topic.trim() || !voiceId || !avatar.value || !isSubtitleStyleValid(subtitleStyle);

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
        loading={scriptGen.isPending}
        speed={speed}
      />

      <ImagePicker
        value={avatar.value}
        onChange={avatar.setValue}
        presets={presets.data ?? []}
        uploading={uploadImg.isPending}
        onUpload={avatar.onUpload}
        uploadError={avatar.error}
      />

      <VoicePicker voices={voiceList ?? []} value={voiceId} onChange={setVoiceId} />

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
        onConfirm={confirm.confirm}
        onCancel={confirm.cancel}
      />
    </Card>
  );
}
