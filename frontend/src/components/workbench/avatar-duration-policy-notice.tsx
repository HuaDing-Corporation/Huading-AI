"use client";

import { useId } from "react";
import { TriangleAlert } from "lucide-react";
import { copy } from "@/lib/copy";

/** Local consent UI only. Wire policy/version must come from the published backend contract. */
export function AvatarDurationPolicyNotice({ accepted, disabled, onChange }: {
  accepted: boolean;
  disabled: boolean;
  onChange: (accepted: boolean) => void;
}) {
  const descriptionId = useId();
  return (
    <div className="min-w-0 space-y-3">
      <p id={descriptionId} className="flex items-start gap-2 rounded-field bg-error-bg px-3 py-2.5 text-[13px] leading-6 text-error-fg">
        <TriangleAlert aria-hidden size={16} className="mt-1 flex-none" />
        <span>{copy.confirm.avatarDurationPolicy}</span>
      </p>
      <label className="flex min-h-11 cursor-pointer items-start gap-3 text-[13px] leading-6 text-ink">
        <input type="checkbox" checked={accepted} disabled={disabled}
          aria-describedby={descriptionId}
          onChange={(event) => onChange(event.target.checked)}
          className="mt-1 size-4 flex-none accent-gold-deep focus-visible:outline-none focus-visible:shadow-focus-gold" />
        <span>{copy.confirm.avatarDurationConsent}</span>
      </label>
    </div>
  );
}
