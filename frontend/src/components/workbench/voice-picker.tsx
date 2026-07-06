"use client";

import Link from "next/link";
import { Loader2, Play } from "lucide-react";

import { SelectableOption } from "@/components/ui/selectable-option";
import type { BrandVoice, Voice } from "@/lib/api/types";
import { copy } from "@/lib/copy";

export interface VoicePickerProps {
  voices: Voice[];
  value: string;
  onChange: (id: string) => void;
  /**
   * 「选我的音色」品牌音色（声音复刻）—— **传入即启用富品牌组**：ready 可选、processing 置灰示意复刻中、
   * failed 不出现、空态引导去 /brand-voices、provider 徽标（缺省兼容）。**不传（undefined）则完全退化为历史
   * 行为**（按 voices.source 分组，/voices 注入的 ready 克隆照常显示可选）——保护未接入的消费者（如批量电商
   * common-params）零回归。
   */
  brandVoices?: BrandVoice[];
  brandVoicesLoading?: boolean;
}

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const groupClass = "mb-1.5 mt-2 block text-[11.5px] tracking-[.5px] text-ink-faint first:mt-0";
const gridClass = "grid grid-cols-1 gap-2 sm:grid-cols-2";

function playSample(url: string) {
  try {
    void new Audio(url).play();
  } catch {
    // sample playback is best-effort
  }
}

/** provider → 徽标文案；**未知/缺省返回 undefined（不显徽标、不报错）**，兼容接口暂无 provider。 */
function providerLabel(provider?: string | null): string | undefined {
  if (provider === "doubao") return copy.brandVoice.providerDoubao;
  if (provider === "cosyvoice") return copy.brandVoice.providerCosyvoice;
  return undefined;
}

/** 系统预设音色选项（可试听）。 */
function VoiceOption({ voice, selected, onSelect }: { voice: Voice; selected: boolean; onSelect: () => void }) {
  const meta = [voice.gender, voice.language].filter(Boolean).join(" · ");
  return (
    <SelectableOption selected={selected} onSelect={onSelect}>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-ink">{voice.display_name}</span>
        {meta && <span className="block truncate text-[11.5px] text-ink-faint">{meta}</span>}
      </span>
      {voice.sample_url && (
        <span
          role="button"
          tabIndex={0}
          aria-label={`试听 ${voice.display_name}`}
          onClick={(e) => {
            e.stopPropagation();
            playSample(voice.sample_url as string);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              e.stopPropagation();
              playSample(voice.sample_url as string);
            }
          }}
          className="flex h-7 w-7 flex-none items-center justify-center rounded-mark text-gold-deep hover:bg-glass-hover"
        >
          <Play size={14} strokeWidth={2} />
        </span>
      )}
    </SelectableOption>
  );
}

/** 品牌音色（声音复刻）选项：ready 可选，processing 置灰 + 复刻中；provider 徽标（缺省不显）。 */
function BrandVoiceOption({ voice, selected, onSelect }: { voice: BrandVoice; selected: boolean; onSelect: () => void }) {
  const processing = voice.status === "processing";
  const provider = providerLabel(voice.provider);
  return (
    <SelectableOption selected={selected} disabled={processing} onSelect={onSelect}>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-ink">{voice.name}</span>
        {processing && (
          <span className="mt-0.5 flex items-center gap-1 text-[11.5px] text-ink-faint">
            <Loader2 size={11} strokeWidth={2.2} className="animate-spin" /> {copy.brandVoice.pickerCloning}
          </span>
        )}
      </span>
      {provider && (
        <span className="flex-none rounded-pill border border-line-gold bg-glass-fill px-2 py-0.5 text-[11px] text-ink-soft">
          {provider}
        </span>
      )}
    </SelectableOption>
  );
}

/**
 * Voice picker —— 系统预设音色 + 「选我的音色」品牌音色（声音复刻）。纯 props。
 * 传 brandVoices → 富品牌组（BRAND-VOICE-PICKER-UI-0001）：ready 可选、processing 置灰复刻中、failed 隐藏、
 * 空态引导去创建、provider 徽标缺省兼容；**系统组排除 source==="brand_voice"**（/voices 注入的 ready 克隆），
 * 品牌组一律取自 brandVoices，避免重复渲染。
 * 不传 brandVoices → 完全退化为历史行为（按 voices.source 分组），保护未接入消费者（批量电商）零回归。
 */
export function VoicePicker({ voices, value, onChange, brandVoices, brandVoicesLoading }: VoicePickerProps) {
  const standard = voices.filter((v) => v.source !== "brand_voice");

  // Legacy：不传 brandVoices → 忠实还原历史 VoicePicker（品牌组来自 voices 中 source==="brand_voice" 项）。
  if (brandVoices === undefined) {
    const legacyBrand = voices.filter((v) => v.source === "brand_voice");
    const grouped = legacyBrand.length > 0;
    return (
      <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
        <legend className={labelClass}>{copy.workbench.voiceLabel}</legend>
        {grouped && (
          <>
            <span id="voice-group-brand" className={groupClass}>
              {copy.brandVoice.pickerBrandGroup}
            </span>
            <div className={gridClass} role="group" aria-labelledby="voice-group-brand">
              {legacyBrand.map((voice) => (
                <VoiceOption key={voice.id} voice={voice} selected={value === voice.id} onSelect={() => onChange(voice.id)} />
              ))}
            </div>
            <span id="voice-group-standard" className={groupClass}>
              {copy.brandVoice.pickerStandardGroup}
            </span>
          </>
        )}
        <div className={gridClass} role={grouped ? "group" : undefined} aria-labelledby={grouped ? "voice-group-standard" : undefined}>
          {standard.map((voice) => (
            <VoiceOption key={voice.id} voice={voice} selected={value === voice.id} onSelect={() => onChange(voice.id)} />
          ))}
        </div>
      </fieldset>
    );
  }

  // Rich：品牌组取自 brandVoices（ready 可选 / processing 置灰 / failed 隐藏 / 空态引导）。
  const ready = brandVoices.filter((v) => v.status === "ready");
  const processing = brandVoices.filter((v) => v.status === "processing");
  const hasBrandOptions = ready.length + processing.length > 0;

  return (
    <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
      <legend className={labelClass}>{copy.workbench.voiceLabel}</legend>

      <span id="voice-group-brand" className={groupClass}>
        {copy.brandVoice.pickerBrandGroup}
      </span>
      {/* 三态同包一个持久 group+live 容器：分组语义在 loading/空态/选项态一致，加载→结果切换对读屏可播报。 */}
      <div role="group" aria-labelledby="voice-group-brand" aria-live="polite">
        {brandVoicesLoading ? (
          <p className="text-[12.5px] text-ink-soft">{copy.brandVoice.pickerBrandLoading}</p>
        ) : !hasBrandOptions ? (
          <p className="text-[12.5px] text-ink-soft">
            <span>{copy.brandVoice.pickerBrandEmpty}</span>
            {" · "}
            <Link href="/brand-voices" className="text-gold-deep underline underline-offset-2 hover:text-ink">
              {copy.brandVoice.pickerBrandCreate}
            </Link>
          </p>
        ) : (
          <div className={gridClass}>
            {ready.map((v) => (
              <BrandVoiceOption key={v.id} voice={v} selected={value === v.id} onSelect={() => onChange(v.id)} />
            ))}
            {processing.map((v) => (
              <BrandVoiceOption key={v.id} voice={v} selected={false} onSelect={() => undefined} />
            ))}
          </div>
        )}
      </div>

      <span id="voice-group-standard" className={groupClass}>
        {copy.brandVoice.pickerStandardGroup}
      </span>
      <div className={gridClass} role="group" aria-labelledby="voice-group-standard">
        {standard.map((voice) => (
          <VoiceOption key={voice.id} voice={voice} selected={value === voice.id} onSelect={() => onChange(voice.id)} />
        ))}
      </div>
    </fieldset>
  );
}
