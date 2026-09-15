import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { BillingQuote, CreateVideoRequest } from "@/lib/api/types";
import type { GenerateConfirmPricing } from "@/lib/api/use-generate-confirm";
import { ConfirmGenerateDialog } from "./confirm-generate-dialog";
import { avatarPolicy } from "@/lib/api/testing/avatar-policy";

const fallback = vi.hoisted(() => ({ mutate: vi.fn(), reset: vi.fn() }));
vi.mock("@/lib/api/hooks", () => ({ useEstimateVideo: () => fallback }));

// Independent product wording: dropping either fee or substituting estimated duration must fail.
const disclosure = "数字人视频最长支持145秒。若实际配音时长超过145秒导致生成失败，本次配音费和视频生成费均不退还。请在提交前确认文案与语速。";
const consent = () => screen.getByRole("checkbox", { name: /我已阅读并同意/ });
const quote: BillingQuote = {
  avatar_duration_policy: avatarPolicy(12),
  pricing_contract: "billing_quote", operation: "video_create", pricing_shape: "composite",
  unit: null, quantity: null, unit_credits: null, rate_scope: null, rate_source: null,
  subtotal_credits: "12", payable_credits: 12, breakdown: [{
    operation: "video_create", capability: "avatar_talk", label: "视频生成", quantity: "10", unit: "秒",
    unit_credits: "1.2", subtotal_credits: "12", rate_scope: "platform_fixed", rate_source: "platform_rate",
    rate_id: null, effective_at: null, policy_key: null, policy_version: null
  }], disclosures: [],
  quote_token: "synthetic-quote-a", expires_at: "2026-09-15T12:00:00Z"
};
function pricing(billed: boolean): GenerateConfirmPricing {
  return {
    estimate: billed ? quote : { pricing_contract: "legacy_estimate", estimated_credits: 12, unit: "credits", avatar_duration_policy: avatarPolicy(12) },
    phase: "ready", error: null, errorMessage: null, billingPhase: "ready",
    billingQuote: billed ? quote : null, billing: null, billingErrorMessage: null,
    expiresInSeconds: 300, prepareBilling: vi.fn(), continueBillingLookup: vi.fn(), retryEstimate: vi.fn()
  };
}

describe.each(["photo", "video"] as const)("145s policy: %s source", (source) => {
  describe.each([false, true])("billing_quote=%s", (billed) => {
    const request: CreateVideoRequest = {
      topic: "短文案", voice_id: "voice-1", speed: 1,
      ...(source === "photo" ? { avatar_asset_id: "photo-1" } : { avatar_video_asset_id: "video-1" })
    };
    const button = () => screen.getByRole("button", { name: billed ? "确认并继续" : "确定" });
    function setup() {
      const props = { open: true, request, submitting: false, pricing: pricing(billed), onConfirm: vi.fn(), onCancel: vi.fn() };
      return { ...render(<ConfirmGenerateDialog {...props} />), props };
    }

    it("fails closed before explicit consent, with both fees disclosed accessibly", async () => {
      const { props } = setup();
      expect(button()).toBeDisabled();
      fireEvent.click(button());
      await act(async () => {});
      expect(props.onConfirm).not.toHaveBeenCalled();
      expect(screen.getByText(disclosure)).toBeVisible();
      expect(consent()).not.toBeChecked();
      expect(consent()).toHaveAccessibleDescription(disclosure);
      expect(consent()).toHaveAttribute("type", "checkbox");
    });

    it("one accepted confirmation cannot double submit while its promise is pending", async () => {
      const view = setup();
      let finish!: () => void;
      view.props.onConfirm.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
      fireEvent.click(consent());
      expect(button()).toBeEnabled();
      const submit = button();
      fireEvent.click(submit);
      fireEvent.click(submit);
      expect(view.props.onConfirm).toHaveBeenCalledTimes(1);
      await act(async () => finish());
    });

    it("cancel and reopen require fresh consent", () => {
      const view = setup();
      fireEvent.click(consent());
      fireEvent.click(screen.getByRole("button", { name: "取消" }));
      expect(view.props.onCancel).toHaveBeenCalledTimes(1);
      view.rerender(<ConfirmGenerateDialog {...view.props} open={false} />);
      view.rerender(<ConfirmGenerateDialog {...view.props} />);
      expect(consent()).not.toBeChecked();
      expect(button()).toBeDisabled();
    });

    it("missing policy token cannot be accepted just because the quote arrived", () => {
      const view = setup();
      const noPolicy = { ...view.props.pricing.estimate!, avatar_duration_policy: undefined };
      view.rerender(<ConfirmGenerateDialog {...view.props} pricing={{ ...view.props.pricing, estimate: noPolicy }} />);
      fireEvent.click(consent());
      expect(button()).toBeDisabled();
    });

    it("rechecks expiry at click time even before the expiry timer renders", async () => {
      const view = setup();
      fireEvent.click(consent());
      const now = vi.spyOn(Date, "now").mockReturnValue(Date.parse("2030-01-01T00:00:01Z"));
      try {
        fireEvent.click(button());
        await act(async () => {});
        expect(view.props.onConfirm).not.toHaveBeenCalled();
      } finally {
        now.mockRestore();
      }
    });

    it.each([
      { speed: 0.8 }, { topic: "新的文案" }, { voice_id: "voice-2" },
      { avatar_asset_id: undefined, avatar_video_asset_id: "changed-video" }
    ])("changing input invalidates accepted consent: %j", (change) => {
      const view = setup();
      fireEvent.click(consent());
      view.rerender(<ConfirmGenerateDialog {...view.props} request={{ ...request, ...change }} />);
      expect(consent()).not.toBeChecked();
      expect(button()).toBeDisabled();
    });

    it("a replaced estimate requires consent even when its amount has not changed", () => {
      const view = setup();
      fireEvent.click(consent());
      const next = pricing(billed);
      if (billed) {
        next.billingQuote = { ...quote, quote_token: "synthetic-quote-b" };
        next.estimate = next.billingQuote;
      }
      view.rerender(<ConfirmGenerateDialog {...view.props} pricing={next} />);
      expect(consent()).not.toBeChecked();
      expect(button()).toBeDisabled();
    });
  });
});

it("does not impose HeyGen's policy on Seedance", () => {
  render(<ConfirmGenerateDialog open request={{ topic: "商品", video_mode: "seedance_i2v" }} submitting={false}
    pricing={pricing(false)} onConfirm={vi.fn()} onCancel={vi.fn()} />);
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.queryByText(disclosure)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确定" })).toBeEnabled();
});
