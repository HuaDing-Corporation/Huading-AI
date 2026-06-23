"use client";

import { useState } from "react";

import type { CreateVideoRequest } from "@/lib/api/types";

export interface GenerateConfirm {
  open: boolean;
  request: CreateVideoRequest | null;
  submitting: boolean;
  /** Open the confirm dialog with the built request (call after validation passes). */
  requestConfirm: (req: CreateVideoRequest) => void;
  /** Run submit(request); guarded against double-submit. */
  confirm: () => Promise<void>;
  /** Close without submitting (no-op while a submit is in flight). */
  cancel: () => void;
}

/**
 * Shared "confirm before generate" lifecycle for both workbench forms: a click on
 * 生成视频 (after the form's validation) opens a confirm dialog with the built
 * request instead of submitting; 确定 runs submit(request); 取消 closes without
 * submitting. `submit` handles its own errors (the form surfaces them); this owns
 * only the dialog open/submitting state and the double-submit guard.
 */
export function useGenerateConfirm(
  submit: (req: CreateVideoRequest) => Promise<void>
): GenerateConfirm {
  const [open, setOpen] = useState(false);
  const [request, setRequest] = useState<CreateVideoRequest | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const requestConfirm = (req: CreateVideoRequest) => {
    setRequest(req);
    setOpen(true);
  };

  const cancel = () => {
    if (!submitting) setOpen(false);
  };

  const confirm = async () => {
    if (!request || submitting) return;
    setSubmitting(true);
    try {
      await submit(request);
    } finally {
      setSubmitting(false);
      setOpen(false);
    }
  };

  return { open, request, submitting, requestConfirm, confirm, cancel };
}
