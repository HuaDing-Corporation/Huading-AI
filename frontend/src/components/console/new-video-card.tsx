"use client";

import { type ReactNode, useState } from "react";
import { Check, Sparkles } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { Input } from "@/components/ui/input";
import { templateOptions, voiceSizeOptions } from "@/lib/mock";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="mb-[15px]">
      <label htmlFor={htmlFor} className={labelClass}>
        {label}
      </label>
      {children}
    </div>
  );
}

function FieldGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{label}</legend>
      {children}
    </fieldset>
  );
}

export function NewVideoCard() {
  const { createAndTrack } = useVideoTasks();

  const [topic, setTopic] = useState("秋冬新款羊绒大衣 · 卖点种草");
  // Template/voice are UI affordances for now; only the topic drives the API
  // (pipeline=standard). Wiring them to engine params is a follow-up.
  const [template, setTemplate] = useState(templateOptions[0]);
  const [options, setOptions] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleOption = (value: string) =>
    setOptions((prev) =>
      prev.includes(value) ? prev.filter((item) => item !== value) : [...prev, value]
    );

  const onGenerate = async () => {
    const trimmed = topic.trim();
    if (!trimmed || submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await createAndTrack(
        { topic: trimmed, pipeline: "standard", mode: "generate", n_scenes: 3 },
        trimmed
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "提交失败，请重试。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card animateIn>
      <CardTitle>新建视频</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">支持主题生成 / 商品驱动 / 自定义脚本</CardSubtitle>

      <Field label="视频主题" htmlFor="video-topic">
        <Input
          id="video-topic"
          name="video-topic"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="输入视频主题，如：秋冬新款羊绒大衣 · 卖点种草"
        />
      </Field>

      <FieldGroup label="视觉模板">
        <div className="grid grid-cols-3 gap-3">
          {templateOptions.map((option) => (
            <Chip key={option} selected={template === option} onClick={() => setTemplate(option)}>
              {option}
              {template === option && <Check size={16} strokeWidth={2} />}
            </Chip>
          ))}
        </div>
      </FieldGroup>

      <FieldGroup label="语音 / 尺寸">
        <div className="grid grid-cols-3 gap-3">
          {voiceSizeOptions.map((option) => (
            <Chip key={option} selected={options.includes(option)} onClick={() => toggleOption(option)}>
              {option}
              {options.includes(option) && <Check size={16} strokeWidth={2} />}
            </Chip>
          ))}
        </div>
      </FieldGroup>

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
        disabled={submitting || !topic.trim()}
      >
        <Sparkles size={18} strokeWidth={1.8} /> {submitting ? "提交中…" : "生成视频"}
      </Button>
    </Card>
  );
}
