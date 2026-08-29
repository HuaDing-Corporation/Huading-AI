import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "@/mocks/server";

import { estimateScript, generateScript } from "./scripts";

const API = "http://localhost:8000";

function scriptQuote() {
  return {
    pricing_contract: "billing_quote" as const,
    pricing_shape: "simple" as const,
    operation: "script_generate",
    unit: "request",
    quantity: "1",
    unit_credits: "1",
    subtotal_credits: "1",
    payable_credits: 1,
    rate_scope: "platform_fixed" as const,
    rate_source: "fixed_policy" as const,
    breakdown: [] as [],
    disclosures: [],
    quote_token: "script-quote-token",
    expires_at: new Date(Date.now() + 60_000).toISOString()
  };
}

// ECOM-VIDEO-SCENE-DURATION-FIX-UI-0001 · FIX2（CB P1 · 机制）：「AI生成文案」路径此前漏 isValidDuration 门，真发
// {duration_sec:5.5} 到 BE，而 BE ScriptGenerateRequest.duration_sec 是 int → 422；scripts mock 又没校验该字段 = 假绿。
// 本测真走 apiFetch→全局 MSW：整数正常、小数 422——让任何未加门的发送路径在测试里响亮失败，而非悄悄假绿。
describe("generateScript · POST /scripts/generate（apiFetch 真走 MSW · FIX2 机制）", () => {
  it("estimates first and sends the exact billing confirmation headers on generate", async () => {
    const submit = vi.fn();
    server.use(
      http.post(`${API}/api/v1/scripts/estimate`, () =>
        HttpResponse.json({ data: scriptQuote(), error: null, request_id: "estimate" })
      ),
      http.post(`${API}/api/v1/scripts/generate`, async ({ request }) => {
        submit({
          quote: request.headers.get("X-Huading-Quote"),
          key: request.headers.get("Idempotency-Key"),
          body: await request.json()
        });
        return HttpResponse.json({
          data: {
            script: "服务端文案",
            billing: {
              operation_id: "script-operation",
              idempotency_key: "11111111-1111-4111-8111-111111111111",
              status: "settled",
              requested_credits: 1,
              held_credits: 0,
              settled_credits: 1,
              released_credits: 0
            }
          },
          error: null,
          request_id: "submit"
        });
      })
    );

    const input = { topic: "保温杯" };
    await expect(estimateScript(input)).resolves.toMatchObject({
      operation: "script_generate",
      payable_credits: 1
    });
    await expect(
      generateScript(input, {
        quote_token: "script-quote-token",
        idempotency_key: "11111111-1111-4111-8111-111111111111"
      })
    ).resolves.toMatchObject({ script: "服务端文案", billing: { status: "settled" } });
    expect(submit).toHaveBeenCalledWith({
      quote: "script-quote-token",
      key: "11111111-1111-4111-8111-111111111111",
      body: input
    });
  });

  it("合法整数 duration_sec(10) → 正常返回 script", async () => {
    const input = { topic: "保温杯", video_mode: "seedance_i2v" as const, duration_sec: 10, length_tier: "medium" as const };
    const quote = await estimateScript(input);
    const res = await generateScript(input, {
      quote_token: quote.quote_token,
      idempotency_key: "33333333-3333-4333-8333-333333333333"
    });
    expect(res.script).toBeTruthy();
  });

  it("防假绿：小数 duration_sec(5.5) → 422（BE int，scripts mock 现拒非整数）", async () => {
    await expect(
      estimateScript({ topic: "保温杯", duration_sec: 5.5 })
    ).rejects.toThrow();
  });
});
