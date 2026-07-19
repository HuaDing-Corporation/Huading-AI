"use client";

import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useBrandVoices, useScenePromptGenerate, useScriptGenerate, useUploadProductImage, useVoices } from "@/lib/api/hooks";
import { useGenerateConfirm } from "@/lib/api/use-generate-confirm";
import type { CreateVideoRequest, ScriptLengthTier, VideoGenResolution } from "@/lib/api/types";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { AiTextField } from "@/components/workbench/ai-text-field";
import { ConfirmGenerateDialog } from "@/components/workbench/confirm-generate-dialog";
import { Input } from "@/components/ui/input";
import { DurationPicker, isValidDuration } from "@/components/workbench/duration-picker";
import { IMAGE_COUNT_MAX, isValidImageCount, ProductImageCountPicker } from "@/components/workbench/product-image-count-picker";
import { ReferenceImagesPicker } from "@/components/workbench/reference-images-picker";
import { MoreSettings } from "@/components/workbench/more-settings";
import { ResolutionPicker } from "@/components/workbench/resolution-picker";
import { ScriptLengthPicker } from "@/components/workbench/script-length-picker";
import { ScriptReview } from "@/components/workbench/script-review";
import { VoicePicker } from "@/components/workbench/voice-picker";
import { useAuth } from "@/lib/auth/auth-context";
import { canUseVipVoiceClone } from "@/lib/auth/vip";
import { AiLabelToggle } from "@/components/label/ai-label-toggle";
import { useLabelTogglePreference } from "@/lib/preferences/label-toggle";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * 电商带货 (seedance_i2v) workbench container — mirrors NewVideoForm 的编排，但必填资产是**产品图**：
 * 经 POST /uploads → image_key（非数字人的 /uploads/images → asset_id）。ECOM-VIDEO-OPTIMIZE-UI-0001 起
 * 产品图单图→多图（复用 ReferenceImagesPicker 的多图能力，注入产品图上传器），提交体带
 * video_mode:"seedance_i2v" + product_image_keys[]；主题去必填（新下限=产品图≥1+音色）。errorText 错误映射
 * 与 NewVideoForm 共享，逻辑不重复。
 */
export function EcomVideoForm({
  initialTopic,
  initialScenePrompt,
  initialScript,
  onPrefillConsumed
}: {
  initialTopic?: string;
  initialScenePrompt?: string;
  initialScript?: string;
  onPrefillConsumed?: () => void;
} = {}) {
  const { createAndTrack } = useVideoTasks();
  const scriptGen = useScriptGenerate();
  const scenePromptGen = useScenePromptGenerate();
  const uploadProduct = useUploadProductImage();
  const voices = useVoices();
  const { session, ready: authReady } = useAuth(); // VIP 门禁（§二之二）：doubao 品牌音色可用性
  const brandVoices = useBrandVoices(); // 「选我的音色」：品牌音色(声音复刻)全状态

  // 一次性 prefill：文案「用此文案」注 script；提示词反推「带入」注 topic + scene_prompt(+script)。惰性消费。
  const [topic, setTopic] = useState(() => initialTopic ?? "");
  const [script, setScript] = useState(() => initialScript ?? "");
  const [scenePrompt, setScenePrompt] = useState(() => initialScenePrompt ?? "");
  // ECOM-VIDEO-OPTIMIZE-UI-0001：产品图单图→多图（product_image_keys）；张数选择器定上限（默认 1，保底=至少 1 张）；
  // 负面提示词（可选，「AI生成画面」自动填入）；文案字数档位（默认中）。
  const [productKeys, setProductKeys] = useState<string[]>([]);
  const [imageCount, setImageCount] = useState(1);
  const [negativePrompt, setNegativePrompt] = useState("");
  const [scriptLength, setScriptLength] = useState<ScriptLengthTier>("medium");
  const [voiceId, setVoiceId] = useState("");
  const [durationSec, setDurationSec] = useState(30);
  // 分辨率（ECOM-RESOLUTION-UI-0001）：默认 720p 与后端缺省一致，不选时行为不变。
  const [resolution, setResolution] = useState<VideoGenResolution>("720p");
  const [speed, setSpeed] = useState(1);
  const [applyLabel, setApplyLabel] = useLabelTogglePreference(); // AI 标识开关（默认关，localStorage 记忆）
  const [error, setError] = useState<string | null>(null);
  // WORKBENCH-KEEPALIVE-UI-0001 · prefill 消费时机重设计（详见 new-video-form.tsx 同处注释）：面板常驻后本表单
  // 不再重挂 → 改为同步 props；只写 prefill 带来的字段，用户已填的其它输入原样保留；消费后回调 clearPrefill →
  // props 回落 undefined → 下次 early-return，不重复注入（原 prefillConsumed ref 闩锁已删，它永不复位）。
  useEffect(() => {
    if (initialTopic === undefined && initialScenePrompt === undefined && initialScript === undefined) return;
    if (initialTopic !== undefined) setTopic(initialTopic);
    if (initialScenePrompt !== undefined) setScenePrompt(initialScenePrompt);
    if (initialScript !== undefined) setScript(initialScript);
    onPrefillConsumed?.();
  }, [initialTopic, initialScenePrompt, initialScript, onPrefillConsumed]);

  // Actual submit — runs only after the 确定生成 confirmation; owns its own errors.
  const submit = async (req: CreateVideoRequest) => {
    setError(null);
    try {
      // topic 现可空（req1）→ 任务展示标题用 topic 兜底为通用名（createAndTrack 的 title 需非空字符串）。
      await createAndTrack(req, req.topic || copy.workbench.ecomTitle);
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

  const hasProductImage = productKeys.length > 0; // req1/决策2：产品图是唯一必填/保底判据
  // 已上传产品图数 > 所选张数 → 明确越限（决策2/req2：不静默丢图，沿用 ECOM-REF-LIMIT 先例，让用户删减或调高张数）。
  // 仅在张数合法时判越限：否则清空自定义输入(Number("")===0)会误报「超过所选 0 张」——非法张数由 picker 就地提示 +
  // canGenerate 的 isValidImageCount 兜住，不走越限文案。
  const imagesOverLimit = isValidImageCount(imageCount) && productKeys.length > imageCount;
  // 生成前置条件：产品图≥1 且未越限、张数合法、已选音色、时长合法。onGenerate 与 generateDisabled 共用，杜绝判据漂移。
  const canGenerate =
    hasProductImage && !imagesOverLimit && isValidImageCount(imageCount) && !!voiceId && isValidDuration(durationSec);

  const onGenerateScript = async () => {
    const trimmed = topic.trim();
    // 需卖点/主题（BE topic 必填）+ 时长合法（FIX2：BE ScriptGenerateRequest.duration_sec 也是 int，小数会 422——不发非法时长）。
    if (!trimmed || !isValidDuration(durationSec) || scriptGen.isPending) return;
    setError(null);
    try {
      const res = await scriptGen.mutateAsync({
        topic: trimmed,
        video_mode: "seedance_i2v",
        duration_sec: durationSec,
        length_tier: scriptLength // req3：字数档位随请求传（默认 medium）
      });
      setScript(res.script);
    } catch (err) {
      setError(errorText(err));
    }
  };

  // 画面提示词 AI 生成（契约 §4.2/req7）：发产品图 keys（≥1，luna 多模态读图）+ 文案 + topic；同产出负面提示词，自动填入。
  const onGenerateScenePrompt = async () => {
    // 必须带产品图（BE 会 422）+ 时长合法（FIX1：BE duration_sec 是 int，小数会 422——不发非法时长的 scene-prompt）。
    if (!hasProductImage || !isValidDuration(durationSec) || scenePromptGen.isPending) return;
    setError(null);
    try {
      const res = await scenePromptGen.mutateAsync({
        topic: topic.trim() || undefined,
        script: script.trim() || undefined,
        product_image_keys: productKeys,
        duration_sec: durationSec // SCENE-DURATION-FIX：带当前选中时长（含自定义值），让画面提示词秒数随选择变化（BE 夹取 [5,120]）
      });
      // ?? "" 防御：契约保证二者恒为 string，但真 BE 若漏字段返 undefined 会把受控 textarea 翻成非受控（React 告警）。
      setScenePrompt(res.scene_prompt ?? "");
      setNegativePrompt(res.negative_prompt ?? ""); // req6：负面提示词自动填入（用户可再改）
    } catch (err) {
      setError(errorText(err));
    }
  };

  // Run existing validation, then open the confirm dialog instead of submitting.
  const onGenerate = () => {
    if (!canGenerate) return; // req1/决策2：产品图≥1+未越限+张数合法+音色+合法时长（与 generateDisabled 同判据）
    setError(null);
    confirm.requestConfirm({
      topic: topic.trim() || undefined, // req1：可选，空则不带
      script: script.trim() || undefined,
      video_mode: "seedance_i2v",
      product_image_keys: productKeys, // req2：单图 image_key → 多图 product_image_keys
      voice_id: voiceId,
      scene_prompt: scenePrompt.trim() || undefined,
      negative_prompt: negativePrompt.trim() || undefined, // req6：可选，空则不带
      duration_sec: durationSec,
      resolution, // ECOM-RESOLUTION-UI-0001：后端 #117 存 params.resolution 按档出片&计费
      speed,
      aspect_ratio: "9:16",
      subtitle_enabled: true,
      apply_visible_label: applyLabel
    });
  };

  const generateDisabled = uploadProduct.isPending || !canGenerate;

  // Tell the user which required input is still missing (validation feedback).
  let hint: string | null = null;
  if (!hasProductImage) hint = copy.workbench.ecomImageRequired;
  else if (imagesOverLimit) hint = copy.workbench.productImagesExceed(productKeys.length, imageCount);

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

      {/* 分辨率（ECOM-RESOLUTION-UI-0001）：与视频生成同款三档，位置参照其时长→分辨率顺序。 */}
      <ResolutionPicker value={resolution} onChange={setResolution} />

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

      {/* 产品图前置（req1 后它是唯一必填/保底；且「AI生成画面」依赖它）：张数选择器定上限 → 多图 picker 上传。 */}
      <ProductImageCountPicker value={imageCount} onChange={setImageCount} />

      <ReferenceImagesPicker
        onChange={setProductKeys}
        max={isValidImageCount(imageCount) ? imageCount : IMAGE_COUNT_MAX}
        inputId="product-image"
        label={copy.workbench.productImagesLabel}
        uploadLabel={copy.workbench.productImagesUpload}
        overLimitError={copy.workbench.productImagesOverLimit}
        // 产品图走 /uploads→image_key（非参考图的 /uploads/images→asset_id）：注入产品图上传器复用多图能力。
        uploadFile={(f) => uploadProduct.mutateAsync(f).then((r) => r.image_key)}
      />

      {/* 文案字数档位（req3）：短/中/长，随「AI生成文案」传 length_tier；紧邻文案区。 */}
      <ScriptLengthPicker value={scriptLength} onChange={setScriptLength} />

      <ScriptReview
        script={script}
        onChange={setScript}
        onRegenerate={onGenerateScript}
        loading={scriptGen.isPending}
        speed={speed}
        label={copy.workbench.ecomScriptLabel}
        actionLabel={copy.workbench.ecomScriptGenerate} // req3：「重写文案」→「AI生成文案」（口播共享组件不传→仍「重写文案」）
        actionDisabled={!topic.trim() || !isValidDuration(durationSec)} // 主题空 / 时长非整数(FIX2) 禁「AI生成文案」（消死点击 + 不发小数时长）
        // KEEPALIVE：面板常驻后与口播的 ScriptReview 同存于 DOM → id 必须区分（否则 label[for] 错指隐藏面板）。
        id="ecom-script"
      />

      <AiTextField
        id="scene-prompt"
        label={copy.workbench.scenePromptLabel}
        value={scenePrompt}
        onChange={setScenePrompt}
        onAction={onGenerateScenePrompt}
        actionLabel={copy.workbench.scenePromptGenerate}
        actionIcon="generate"
        actionDisabled={!hasProductImage || !isValidDuration(durationSec)} // req7：无产品图 / 时长非整数(FIX1) 禁点（BE 会 422，前端友好拦）
        loading={scenePromptGen.isPending}
        rows={3}
        placeholder={copy.workbench.scenePromptPlaceholder}
        footer={hasProductImage ? copy.workbench.scenePromptHint : copy.workbench.sceneNeedProductImage}
      />

      {/* 负面提示词（req6）：可选、无字数限制；「AI生成画面」返回的 negative_prompt 自动填入，用户可再改。纯文本域（无 onAction）。 */}
      <AiTextField
        id="ecom-negative-prompt"
        label={copy.workbench.negativePromptLabel}
        value={negativePrompt}
        onChange={setNegativePrompt}
        rows={2}
        placeholder={copy.workbench.negativePromptPlaceholder}
        footer={copy.workbench.negativePromptHint}
      />

      <VoicePicker
        voices={voiceList ?? []}
        value={voiceId}
        onChange={setVoiceId}
        brandVoices={brandVoices.data ?? []}
        brandVoicesLoading={brandVoices.isLoading}
        // 加载态(!ready)不锁，避免 huading 非 admin 用户在 /me 到达前 doubao 音色瞬时误锁（与 create 卡 ready 门对齐）
        canUseVip={!authReady || canUseVipVoiceClone(session)}
      />

      {/* FIX1：MoreSettings 不传 id → 内部 useId 生成实例唯一 id，与口播那份天然不撞（无 e2e selector 依赖）。 */}
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
