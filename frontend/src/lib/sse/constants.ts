// Centralized, configurable SSE / progress / script constants.
export const STALL_MS = 120_000; // no-progress window before client forces failed
export const HARD_CAP_MS = 15 * 60_000; // absolute in-flight cap from queued
export const POLL_MS = 2_000; // poll-fallback interval
export const MAX_SCRIPT_SECONDS = 60; // OmniHuman audio ≤60s
export const CPS = 5; // chinese chars/second estimate
export const estSeconds = (script: string, speed = 1) =>
  Math.min(MAX_SCRIPT_SECONDS, Math.ceil(script.length / CPS / (speed || 1)));
