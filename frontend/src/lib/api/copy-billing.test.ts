import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch } from "./client";
import { copyFailureBilling } from "./copy-billing";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ── PRICING-UI-0001-FIX6 · P1-2 ·「前端能不能说这一项没扣钱」──────────────────────────────
// 原缺陷：`copywriting-form.tsx` 把**任何** Promise rejection 当成"失败"，文案照说「该项未计费」。
// 服务端已经 settle、只是响应在网络里丢了的时候，这句话是**假的**。
//
// 🔴 这一组门守的性质是**单向的**：
//    「说了未计费」必须有服务端凭据；「没说」永远安全。
//    所以每条断言都写成"**这种情形必须是 unknown**"，而不是"这种情形应该是 released" ——
//    默认值站在不承诺那一侧，将来新增一种没想到的失败形态也自动落到安全侧。

describe("copyFailureBilling · 只有服务端自己判的失败才允许陈述「未计费」", () => {
  /**
   * 🔴 唯一放行的形态：`error.outcome` 由 `core/exceptions.py:_copy_generation_error_outcome`
   * 在服务端异常处理里生成 —— 它出现就意味着请求到达了应用层、走完了那条 release 分支。
   * 变异：`copyFailureBilling` 恒返回 "unknown"（等于全走路 A）→ 本条红。
   */
  it("有服务端 failed outcome（业务失败，如 502 上游拒绝）→ released，可以说未计费", () => {
    const err = new ApiError("上游失败", "PROVIDER_FAILED", 502, null, { operation: "titles", status: "failed" });
    expect(copyFailureBilling(err)).toBe("released");
  });

  /**
   * 🔴🔴 **本轮 P1-2 的核心门**：网络中断压根没有响应 —— 服务端可能已经 settle 并扣了钱。
   * 变异（任务包点名的那条）：把"无响应"也走成"未计费"，即
   *      `copyFailureBilling` 改成 `return "released"` / 或改成看 `err instanceof ApiError`
   *      → 本条红。
   */
  it("🔴 网络中断（NETWORK_ERROR / status 0，无响应体）→ unknown，一个字的资金承诺都不给", () => {
    const err = new ApiError("网络连接失败，请检查后端服务是否在线。", "NETWORK_ERROR", 0);
    expect(copyFailureBilling(err)).toBe("unknown");
  });

  /**
   * 🔴🔴 **有 status 却仍然不许承诺**的那一类 —— 这条是选"看 outcome"而不是"看 status===0"的理由。
   * 网关 504 / 反代 502 由**中间层**产生：HTTP 状态码有，`error.outcome` 没有
   * （中间层不知道 `_copy_generation_error_outcome` 的存在，伪造不出来），
   * 而后端此刻可能已经跑完并 settle 了。
   * 变异：判据换成 `err.status === 0 ? "unknown" : "released"` → 本条红。
   */
  it("🔴 网关 504 / 反代 502（有 status，但没有服务端 outcome）→ unknown", () => {
    expect(copyFailureBilling(new ApiError("请求失败（504）", "HTTP_ERROR", 504))).toBe("unknown");
    expect(copyFailureBilling(new ApiError("请求失败（502）", "HTTP_ERROR", 502))).toBe("unknown");
  });

  /**
   * 🔴🔴 **本轮自己核源码核出来的那条**（任务包没写、CB 也没提）：
   * `_copy_generation_error_outcome` **只按 URL 后缀生成 outcome，不看错误性质**
   * —— 于是 500 未处理异常**照样带 outcome**。而 `_generate_billed_copy` 的 `except` 只覆盖到
   * `settle + commit`，**`return` 之后（路由返回→响应序列化）抛异常就绕过了它**：钱已扣，
   * 响应却是带 outcome 的 500。所以 500 必须排除。
   * 变异：去掉 `copyFailureBilling` 里的 `err.status === 500` 判断 → 本条红。
   */
  it("🔴 500 INTERNAL_SERVER_ERROR + failed outcome → 仍然 unknown（settle 之后出错也长这样）", () => {
    const err = new ApiError("Internal server error.", "INTERNAL_SERVER_ERROR", 500, null, {
      operation: "titles",
      status: "failed"
    });
    expect(copyFailureBilling(err)).toBe("unknown");
  });

  /** 受控异常（配额 403 在 reserve 之前抛、502 由 except 在 release 之后转抛）→ released。 */
  it("受控异常 403 / 422 带 outcome → released（reserve 之前抛出，压根没预留）", () => {
    const mk = (status: number, code: string) =>
      new ApiError("x", code, status, null, { operation: "topics", status: "failed" });
    expect(copyFailureBilling(mk(403, "TENANT_QUOTA_EXCEEDED"))).toBe("released");
    expect(copyFailureBilling(mk(422, "VALIDATION_ERROR"))).toBe("released");
  });

  /** JSON 解析失败（HTML 错误页）→ 同样没有 outcome。 */
  it("响应体不是 JSON（HTML 错误页）→ unknown", () => {
    expect(copyFailureBilling(new ApiError("请求失败（500）", "HTTP_ERROR", 500))).toBe("unknown");
  });

  /**
   * 🔴 形状不符一律当没有 —— 与 402 `detail` 的「半份不许拼」同一条纪律。
   * 变异：`hasFailedOutcome` 改成 `outcome != null` （只判存在不判 status）→ 前两条红。
   */
  it("outcome 形状不符（非对象 / status 不是 failed / 缺 status）→ unknown", () => {
    // ⚠️ 这里的 HTTP 状态**必须用 502 而不是 500**：500 本身就无条件 unknown，
    //    用它当载体会让整条门塌缩成"500 → unknown"，测不出 `hasFailedOutcome` 的任何逻辑。
    //    （这正是本轮 CB 指出的那种"门守不到它要守的属性" —— 写完门要反过来问一遍。）
    const mk = (outcome: unknown) => new ApiError("x", "E", 502, null, outcome);
    expect(copyFailureBilling(mk({ operation: "titles" }))).toBe("unknown");
    expect(copyFailureBilling(mk({ operation: "titles", status: "succeeded" }))).toBe("unknown");
    expect(copyFailureBilling(mk("failed"))).toBe("unknown");
    expect(copyFailureBilling(mk(null))).toBe("unknown");
    expect(copyFailureBilling(mk(undefined))).toBe("unknown");
  });

  /** 非 ApiError 的 rejection（组件内抛的 TypeError、abort 等）→ unknown。 */
  it("不是 ApiError 的 rejection → unknown", () => {
    expect(copyFailureBilling(new TypeError("boom"))).toBe("unknown");
    expect(copyFailureBilling("boom")).toBe("unknown");
    expect(copyFailureBilling(undefined)).toBe("unknown");
    expect(copyFailureBilling(null)).toBe("unknown");
  });
});

describe("client.ts 把 error.outcome 透传到 ApiError（没有这一步，上面的判据永远是 unknown）", () => {
  /**
   * 🔴 承重：FIX6 之前 `ApiError` **不带** outcome，BE 发了也丢在解包那一层。
   * 变异：删掉 `client.ts` 里 `err?.outcome` 这个实参 → 本条红。
   * ⚠️ 这里直接构造 ApiError 只能证明字段存在；"真的从响应里读出来"由
   *    `copywriting-form.billing.test.tsx` 走 msw 全链路证。
   */
  it("ApiError 保留 outcome 字段", () => {
    const err = new ApiError("x", "E", 502, { a: 1 }, { operation: "topics", status: "failed" });
    expect(err.outcome).toEqual({ operation: "topics", status: "failed" });
    expect(err.detail).toEqual({ a: 1 });
  });

  /**
   * 🔴🔴 **端到端那一段**：从 HTTP 响应体一路读到判据。上一条只证明字段存在，这条证明它**被填进去了**。
   * 变异：删掉 `client.ts` 里 `err?.outcome` 实参 → 本条红（拿到的是 undefined → "unknown"）。
   * ⚠️ 与上一条落在不同 `file:line`，且上一条不会因此红 —— 两条互不塌缩。
   */
  it("🔴 502 + error.outcome 的真实响应体 → apiFetch 抛出的 ApiError 带 outcome，判据得到 released", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            data: null,
            error: {
              code: "PROVIDER_FAILED",
              message: "上游失败",
              request_id: "r1",
              detail: null,
              outcome: { operation: "titles", status: "failed" }
            },
            request_id: "r1"
          }),
          { status: 502, headers: { "Content-Type": "application/json" } }
        )
      )
    );
    const reason = await apiFetch("/api/v1/copy/titles", { method: "POST", body: {} }).catch((e) => e);
    expect(reason).toBeInstanceOf(ApiError);
    expect(copyFailureBilling(reason)).toBe("released");
  });

  /** 🔴 对照：同样 502，响应体**没有** outcome（中间层产生的错误页形态）→ unknown。 */
  it("🔴 502 但响应体不带 outcome → 判据得到 unknown（这就是网关错误与业务失败的分界）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ data: null, error: { code: "HTTP_ERROR", message: "bad gateway" }, request_id: null }), {
          status: 502,
          headers: { "Content-Type": "application/json" }
        })
      )
    );
    const reason = await apiFetch("/api/v1/copy/titles", { method: "POST", body: {} }).catch((e) => e);
    expect(copyFailureBilling(reason)).toBe("unknown");
  });
});
