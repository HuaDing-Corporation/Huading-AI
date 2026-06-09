"use client";

import { type ReactNode, useState } from "react";
import { Check, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { Input } from "@/components/ui/input";
import { templateOptions, voiceSizeOptions } from "@/lib/mock";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mb-[15px]">
      <label className="mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft">{label}</label>
      {children}
    </div>
  );
}

export function NewVideoCard() {
  const [template, setTemplate] = useState(templateOptions[0]);
  const [options, setOptions] = useState<string[]>([]);

  const toggleOption = (value: string) =>
    setOptions((prev) =>
      prev.includes(value) ? prev.filter((item) => item !== value) : [...prev, value]
    );

  return (
    <Card animateIn>
      <CardTitle>新建视频</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">支持主题生成 / 商品驱动 / 自定义脚本</CardSubtitle>

      <Field label="视频主题">
        <Input defaultValue="秋冬新款羊绒大衣 · 卖点种草" />
      </Field>

      <Field label="视觉模板">
        <div className="grid grid-cols-3 gap-3">
          {templateOptions.map((option) => (
            <Chip
              key={option}
              selected={template === option}
              onClick={() => setTemplate(option)}
            >
              {option}
              {template === option && <Check size={16} strokeWidth={2} />}
            </Chip>
          ))}
        </div>
      </Field>

      <Field label="语音 / 尺寸">
        <div className="grid grid-cols-3 gap-3">
          {voiceSizeOptions.map((option) => (
            <Chip key={option} selected={options.includes(option)} onClick={() => toggleOption(option)}>
              {option}
              {options.includes(option) && <Check size={16} strokeWidth={2} />}
            </Chip>
          ))}
        </div>
      </Field>

      <Button variant="primary" size="lg" className="mt-2 w-full">
        <Sparkles size={18} strokeWidth={1.8} /> 生成视频
      </Button>
    </Card>
  );
}
