"use client";

import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import {
  useAvatarPresets,
  useScriptGenerate,
  useUploadImage,
  useVoices
} from "@/lib/api/hooks";
import type { CreateVideoRequest } from "@/lib/api/types";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ImagePicker } from "@/components/workbench/image-picker";
import { MoreSettings } from "@/components/workbench/more-settings";
import { ScriptReview } from "@/components/workbench/script-review";
import { VoicePicker } from "@/components/workbench/voice-picker";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * avatar_talk workbench container — the ONLY hooks caller. Sub-components are
 * pure props; this orchestrates script generation, upload, voice/avatar
 * selection and assembles the CreateVideoRequest. Errors are keyed on the
 * contract's lowercase `err.code` strings, never HTTP status (code-1).
 */
export function NewVideoForm() {
  const { createAndTrack } = useVideoTasks();
  const scriptGen = useScriptGenerate();
  const uploadImg = useUploadImage();
  const voices = useVoices();
  const presets = useAvatarPresets();

  const [topic, setTopic] = useState("");
  const [script, setScript] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [avatarAssetId, setAvatarAssetId] = useState<string | null>(null);
  const [speed, setSpeed] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

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
      const res = await scriptGen.mutateAsync(trimmed);
      setScript(res.script);
    } catch (err) {
      setError(err instanceof ApiError && err.code === "tenant_quota_exceeded" ? copy.errors.quota : copy.errors.generic);
    }
  };

  // Generation counter so a late upload resolution can't repopulate an asset the
  // user already removed/changed (P1 async edge — guards "submit a removed asset").
  const uploadSeq = useRef(0);

  const onUpload = async (file: File) => {
    const seq = (uploadSeq.current += 1);
    setUploadError(null);
    setAvatarAssetId(null);
    try {
      const res = await uploadImg.mutateAsync(file);
      if (seq === uploadSeq.current) setAvatarAssetId(res.asset_id);
    } catch (err) {
      if (seq === uploadSeq.current) {
        setUploadError(err instanceof ApiError ? err.message : copy.errors.generic);
      }
    }
  };

  // Any explicit avatar change (preset pick or remove) supersedes an in-flight
  // upload so its late resolution won't overwrite the user's choice.
  const handleAvatarChange = (id: string | null) => {
    uploadSeq.current += 1;
    setAvatarAssetId(id);
  };

  const onGenerate = async () => {
    const trimmed = topic.trim();
    if (!trimmed || !voiceId || !avatarAssetId || submitting) return;
    setError(null);
    setSubmitting(true);

    const request: CreateVideoRequest = {
      topic: trimmed,
      script: script.trim() || undefined,
      voice_id: voiceId,
      avatar_asset_id: avatarAssetId,
      speed,
      aspect_ratio: "9:16",
      subtitle_enabled: true
    };

    try {
      await createAndTrack(request, trimmed);
    } catch (err) {
      setError(
        err instanceof ApiError && err.code === "tenant_quota_exceeded"
          ? copy.errors.quota
          : copy.errors.generic
      );
    } finally {
      setSubmitting(false);
    }
  };

  const generateDisabled =
    submitting || uploadImg.isPending || !topic.trim() || !voiceId || !avatarAssetId;

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
        value={avatarAssetId}
        onChange={handleAvatarChange}
        presets={presets.data ?? []}
        uploading={uploadImg.isPending}
        onUpload={onUpload}
        uploadError={uploadError}
      />

      <VoicePicker voices={voiceList ?? []} value={voiceId} onChange={setVoiceId} />

      <MoreSettings speed={speed} onSpeedChange={setSpeed} />

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
        <Sparkles size={18} strokeWidth={1.8} /> {submitting ? copy.workbench.generating : copy.workbench.generate}
      </Button>
    </Card>
  );
}
