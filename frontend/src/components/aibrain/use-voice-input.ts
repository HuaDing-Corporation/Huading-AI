"use client";

// 华鼎AI智脑 · 语音输入（浏览器原生 Web Speech API，零后端 —— D6）。
// ⚠️ Safari 支持差 → 不支持时**优雅降级**（supported=false，调用方隐藏/禁用按钮 + 说明），**绝不报错**（任务包 §4）。

import { useCallback, useEffect, useRef, useState } from "react";

// Web Speech API 未进标准 lib.dom 的最小类型声明（只声明我们用到的字段，避免 any）。
interface SpeechRecognitionAlternativeLike {
  transcript: string;
}
interface SpeechRecognitionResultLike {
  0: SpeechRecognitionAlternativeLike;
  isFinal: boolean;
}
interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: { length: number; [i: number]: SpeechRecognitionResultLike };
}
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: SpeechRecognitionEventLike) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export interface VoiceInput {
  /** 浏览器是否支持（不支持 → 调用方隐藏按钮 + 说明，不报错）。 */
  supported: boolean;
  listening: boolean;
  /** 当前累计识别文本。 */
  transcript: string;
  /** 开始监听（识别到的文本通过 onText 增量回调）。 */
  start: () => void;
  stop: () => void;
}

/**
 * @param onText 每次识别出（含中间态）文本时回调 —— 调用方把它并进输入框。
 */
export function useVoiceInput(onText: (text: string) => void): VoiceInput {
  const [supported] = useState<boolean>(() => getCtor() !== null);
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const onTextRef = useRef(onText);
  useEffect(() => {
    onTextRef.current = onText;
  }, [onText]);

  const stop = useCallback(() => {
    recRef.current?.stop();
    setListening(false);
  }, []);

  const start = useCallback(() => {
    const Ctor = getCtor();
    if (!Ctor) return; // 不支持 → 静默降级
    try {
      const rec = new Ctor();
      rec.lang = "zh-CN";
      rec.continuous = false;
      rec.interimResults = true;
      rec.onresult = (e) => {
        let text = "";
        for (let i = e.resultIndex; i < e.results.length; i++) text += e.results[i][0].transcript;
        setTranscript(text);
        onTextRef.current(text);
      };
      rec.onerror = () => setListening(false); // 失败静默降级，不抛
      rec.onend = () => setListening(false);
      recRef.current = rec;
      rec.start();
      setListening(true);
    } catch {
      setListening(false); // 任何异常都不冒泡到 UI
    }
  }, []);

  useEffect(() => () => recRef.current?.stop(), []);

  return { supported, listening, transcript, start, stop };
}
