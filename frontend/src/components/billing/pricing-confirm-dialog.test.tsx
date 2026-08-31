import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { BillingQuote } from "@/lib/api/types";

import { PricingConfirmDialog } from "./pricing-confirm-dialog";

function quote(): BillingQuote {
  return {
    pricing_contract: "billing_quote",
    operation: "voice_clone",
    pricing_shape: "simple",
    unit: "字符",
    quantity: "123",
    unit_credits: "0.3",
    rate_scope: "tenant_overridable",
    rate_source: "tenant_rate",
    subtotal_credits: "36.9",
    payable_credits: 37,
    breakdown: [],
    disclosures: [
      {
        key: "voice.rounding",
        rendered_text: "应付积分由服务端按当前计费规则确定",
        copy_version: 1,
        unit: "字符",
        rate_scope: "tenant_overridable",
        rate_source: "tenant_rate",
        rate_id: "rate-1",
        effective_at: "2026-08-29T10:00:00Z",
        policy_key: null,
        policy_version: null,
        reference_unit_credits: "0.3"
      }
    ],
    quote_token: "token",
    expires_at: "2026-08-29T10:05:00Z"
  };
}

function renderDialog(overrides: Partial<Parameters<typeof PricingConfirmDialog>[0]> = {}) {
  const props = {
    open: true,
    phase: "ready" as const,
    quote: quote(),
    expiresInSeconds: 42,
    errorMessage: null,
    onEstimate: vi.fn(),
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
    ...overrides
  };
  return { ...render(<PricingConfirmDialog {...props} />), props };
}

describe("PricingConfirmDialog", () => {
  it("shows only server-owned price values and one authoritative primary action", () => {
    renderDialog();
    expect(screen.getByRole("dialog", { name: "确认价格并继续" })).toBeInTheDocument();
    expect(screen.getByText("123 字符")).toBeInTheDocument();
    expect(screen.getByText("0.3 积分 / 字符")).toBeInTheDocument();
    expect(screen.getByText("36.9 积分")).toBeInTheDocument();
    expect(screen.getByText("37 积分")).toBeInTheDocument();
    expect(screen.getByText("报价有效期：42 秒")).toBeInTheDocument();
    expect(screen.getByText("应付积分由服务端按当前计费规则确定")).toBeInTheDocument();
    expect(screen.queryByText(/按实际结算/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "确认并继续" })).toHaveLength(1);
  });

  it("keeps confirmation disabled when estimate fails", () => {
    const { props } = renderDialog({ phase: "failed", quote: null, errorMessage: "offline" });
    expect(screen.getByText("暂时无法获取价格，请稍后重试")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认并继续" }));
    expect(screen.getByRole("button", { name: "确认并继续" })).toBeDisabled();
    expect(props.onConfirm).not.toHaveBeenCalled();
  });

  it("prevents closing or starting a new estimate while the result is querying", () => {
    const view = renderDialog({ phase: "querying" });
    const cancel = screen.getByRole("button", { name: "取消" });
    expect(cancel).toBeDisabled();
    fireEvent.click(cancel);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(view.props.onCancel).not.toHaveBeenCalled();

    view.rerender(
      <PricingConfirmDialog
        {...view.props}
        phase="querying"
        expiresInSeconds={0}
      />
    );
    const reestimate = screen.getByRole("button", { name: "重新获取价格" });
    expect(reestimate).toBeDisabled();
    fireEvent.click(reestimate);
    expect(view.props.onEstimate).not.toHaveBeenCalled();
  });

  it("recognizes loading, submitting, and expiry without changing the accessible action name", () => {
    const view = renderDialog({ phase: "estimating", quote: null });
    expect(screen.getByText("正在获取价格…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认并继续" })).toBeDisabled();

    view.rerender(
      <PricingConfirmDialog
        {...view.props}
        phase="submitting"
        quote={quote()}
      />
    );
    expect(screen.getByText("提交中…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认并继续" })).toBeDisabled();

    view.rerender(
      <PricingConfirmDialog
        {...view.props}
        phase="expired"
        quote={quote()}
        expiresInSeconds={0}
      />
    );
    expect(screen.getByText("报价已过期，请重新获取价格")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新获取价格" }));
    expect(view.props.onEstimate).toHaveBeenCalledTimes(1);
  });

  it("renders composite line items in a responsive, non-overflowing structure", () => {
    const composite: BillingQuote = {
      ...quote(),
      pricing_shape: "composite",
      unit: null,
      quantity: null,
      unit_credits: null,
      rate_scope: null,
      rate_source: null,
      breakdown: [
        {
          operation: "voice_clone",
          capability: "voice.clone",
          unit: "次",
          quantity: "1",
          unit_credits: "20",
          subtotal_credits: "20",
          rate_scope: "platform_fixed",
          rate_source: "fixed_policy",
          rate_id: null,
          effective_at: null,
          policy_key: "clone_base",
          policy_version: 1,
          label: "基础处理"
        },
        {
          operation: "voice_clone",
          capability: "voice.sample",
          unit: "秒",
          quantity: "17",
          unit_credits: "1",
          subtotal_credits: "17",
          rate_scope: "platform_fixed",
          rate_source: "fixed_policy",
          rate_id: null,
          effective_at: null,
          policy_key: "sample_duration",
          policy_version: 1,
          label: "样本时长"
        }
      ]
    };
    renderDialog({ quote: composite });
    expect(screen.getByText("基础处理")).toBeInTheDocument();
    expect(screen.getByText("样本时长")).toBeInTheDocument();
    expect(screen.getByTestId("billing-price-layout")).toHaveClass("min-w-0");
    expect(screen.getByTestId("billing-price-layout")).toHaveClass("overflow-hidden");
  });
});
