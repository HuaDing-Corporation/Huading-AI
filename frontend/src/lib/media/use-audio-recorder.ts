"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { copy } from "@/lib/copy";

export interface AudioRecorder {
  /** 浏览器是否支持录音(getUserMedia + MediaRecorder)；否则引导改用上传。 */
  supported: boolean;
  recording: boolean;
  /** 录得 / 上传载入的音频 Blob，可用于试听与提交。 */
  blob: Blob | null;
  /** Blob 的 object URL（试听用）；reset/卸载时释放。 */
  url: string | null;
  /** 已录制秒数（用于 ≥5s 校验与计时显示）。 */
  durationSec: number;
  error: string | null;
  start: () => Promise<void>;
  stop: () => void;
  /** 外部载入一个 Blob（上传路径复用同一试听/提交状态）。 */
  setExternal: (blob: Blob) => void;
  reset: () => void;
}

const isSupported = (): boolean =>
  typeof window !== "undefined" &&
  typeof navigator !== "undefined" &&
  !!navigator.mediaDevices &&
  typeof navigator.mediaDevices.getUserMedia === "function" &&
  typeof MediaRecorder !== "undefined";

/**
 * 浏览器原生录音 hook（BRAND-VOICE-UI-0001）。封装 getUserMedia + MediaRecorder：权限/启停/
 * 计时/Blob+试听 URL/清理。不支持时 supported=false（UI 引导改用上传）。录音与上传共用 blob/url
 * 状态，故试听/提交逻辑单一。所有流轨道、object URL、计时器在 stop/reset/卸载时释放，防泄漏。
 */
export function useAudioRecorder(): AudioRecorder {
  const [supported] = useState(isSupported);
  const [recording, setRecording] = useState(false);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [durationSec, setDurationSec] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAtRef = useRef(0);
  const urlRef = useRef<string | null>(null);
  // 启动幂等锁：覆盖 getUserMedia await 窗口期（其间 recording 仍为 false），防二次点击启第二路流。
  const startingRef = useRef(false);

  useEffect(() => {
    urlRef.current = url;
  }, [url]);

  const stopTracks = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const applyBlob = useCallback((next: Blob) => {
    setUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(next);
    });
    setBlob(next);
  }, []);

  const start = useCallback(async () => {
    if (!isSupported()) {
      setError(copy.brandVoice.recordUnsupported);
      return;
    }
    // 幂等守卫：防权限提示/慢设备下 await 窗口期二次点击启动第二路流 → 旧轨道/计时器泄漏(占麦克风)。
    if (startingRef.current || recorderRef.current?.state === "recording") return;
    startingRef.current = true;
    setError(null);
    try {
      stopTracks(); // 兜底回收任何残留 stream/timer，再获取新流
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];
      recorder.ondataavailable = (e: BlobEvent) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const type = recorder.mimeType || "audio/webm";
        applyBlob(new Blob(chunksRef.current, { type }));
        stopTracks();
        setRecording(false);
      };
      // 录新一段：清掉旧产物
      setBlob(null);
      setUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
      setDurationSec(0);
      startedAtRef.current = Date.now();
      recorder.start();
      setRecording(true);
      timerRef.current = setInterval(() => {
        setDurationSec((Date.now() - startedAtRef.current) / 1000);
      }, 250);
    } catch {
      stopTracks();
      setRecording(false);
      setError(copy.brandVoice.recordPermissionDenied);
    } finally {
      startingRef.current = false;
    }
  }, [applyBlob, stopTracks]);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      setDurationSec((Date.now() - startedAtRef.current) / 1000);
      recorder.stop(); // onstop 落 blob + 停轨道
    }
  }, []);

  const setExternal = useCallback(
    (next: Blob) => {
      setError(null);
      setDurationSec(0); // 上传文件时长由外部校验
      applyBlob(next);
    },
    [applyBlob]
  );

  const reset = useCallback(() => {
    // 若仍在录音：先摘 onstop/ondataavailable 防其落 blob，再 stop + 停轨道，真正回到干净态
    // （与 JSDoc「stop/reset/卸载释放轨道/计时器」承诺一致，防麦克风/计时器泄漏）。
    const rec = recorderRef.current;
    if (rec && rec.state !== "inactive") {
      rec.onstop = null;
      rec.ondataavailable = null;
      try {
        rec.stop();
      } catch {
        // ignore
      }
    }
    stopTracks();
    setRecording(false);
    setBlob(null);
    setUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    setDurationSec(0);
    setError(null);
  }, [stopTracks]);

  // 卸载清理：停轨道/计时器 + 释放残留试听 URL。
  useEffect(
    () => () => {
      stopTracks();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [stopTracks]
  );

  return { supported, recording, blob, url, durationSec, error, start, stop, setExternal, reset };
}
