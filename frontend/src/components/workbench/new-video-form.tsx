"use client";

import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import {
  useAvatarPresets,
  useScriptGenerate,
  useSubtitleTemplates,
  useUploadAvatarVideo,
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
import { SelectableOption } from "@/components/ui/selectable-option";
import { ImagePicker } from "@/components/workbench/image-picker";
import { AvatarVideoPicker } from "@/components/workbench/avatar-video-picker";
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
  initialTopic,
  initialScript,
  onPrefillConsumed
}: { initialTopic?: string; initialScript?: string; onPrefillConsumed?: () => void } = {}) {
  const { createAndTrack } = useVideoTasks();
  const scriptGen = useScriptGenerate();
  const uploadImg = useUploadImage();
  const uploadVideo = useUploadAvatarVideo();
  const voices = useVoices();
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
  const prefillConsumed = useRef(false);
  useEffect(() => {
    if (!prefillConsumed.current && (initialTopic !== undefined || initialScript !== undefined)) {
      prefillConsumed.current = true;
      onPrefillConsumed?.();
    }
  }, [initialTopic, initialScript, onPrefillConsumed]);

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
        loading={scriptGen.isPending}
        speed={speed}
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
