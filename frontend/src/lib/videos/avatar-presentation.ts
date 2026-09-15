import { copy } from "@/lib/copy";

/** Read-only task snapshots. Never infer a historic model from today's form selection. */
export function avatarModelLabel(task: {
  mode?: string | null;
  avatar_provider?: string | null;
  avatar_model?: string | null;
}): string | null {
  if (task.mode !== "avatar_talk") return null;
  if (task.avatar_provider == null || task.avatar_provider === "omnihuman") return "OmniHuman";
  if (task.avatar_provider !== "heygen") return null;
  if (task.avatar_model === "avatar_iv") return copy.workbench.avatarPhotoModel;
  if (task.avatar_model === "lipsync_precision") return copy.workbench.avatarVideoModel;
  return "HeyGen"; // Known provider, no verified model snapshot.
}

/** These running states retain the original task; never offer a new paid submission. */
export function avatarRecovery(code?: string | null) {
  if (code === "HEYGEN_PENDING") return {
    label: copy.tasks.avatarPending,
    note: copy.tasks.avatarPendingNote
  };
  if (code === "HEYGEN_REVIEW_REQUIRED") return {
    label: copy.tasks.avatarReview,
    note: copy.tasks.avatarReviewNote
  };
  return null;
}
