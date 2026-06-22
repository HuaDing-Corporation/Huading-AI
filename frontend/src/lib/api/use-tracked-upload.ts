"use client";

import { useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { copy } from "@/lib/copy";

export interface TrackedUpload {
  /** The stored key/id (avatar asset_id or i2v image_key), or null when none/removed. */
  value: string | null;
  /** Upload-specific error message, or null. */
  error: string | null;
  /** Handler for a validated file: uploads it and stores the extracted key. */
  onUpload: (file: File) => Promise<void>;
  /** Explicit set (preset pick / remove) — supersedes any in-flight upload. */
  setValue: (next: string | null) => void;
}

/**
 * Two-step image upload with a sequence guard: a late-resolving upload can never
 * repopulate a value the user already removed or changed in the meantime (P1
 * async edge). Shared by both workbench forms — 数字人口播 (asset_id) and 电商带货
 * i2v (image_key) — so the race-safe logic lives in exactly one place.
 *
 * `mutateAsync` is the upload mutation's runner; `extractKey` pulls the stored
 * key out of its result. The in-flight flag stays with the caller's mutation
 * (`isPending`) so the picker can render its uploading state.
 */
export function useTrackedUpload<T>(
  mutateAsync: (file: File) => Promise<T>,
  extractKey: (result: T) => string
): TrackedUpload {
  const [value, setValueState] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  const onUpload = async (file: File) => {
    const current = (seq.current += 1);
    setError(null);
    setValueState(null);
    try {
      const result = await mutateAsync(file);
      if (current === seq.current) setValueState(extractKey(result));
    } catch (err) {
      if (current === seq.current) {
        setError(err instanceof ApiError ? err.message : copy.errors.generic);
      }
    }
  };

  const setValue = (next: string | null) => {
    seq.current += 1;
    setValueState(next);
    if (next === null) setError(null);
  };

  return { value, error, onUpload, setValue };
}
