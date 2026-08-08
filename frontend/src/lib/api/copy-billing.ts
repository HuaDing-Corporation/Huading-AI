// 文案生成失败时，**前端能不能对用户陈述"这一项没扣钱"**（PRICING-UI-0001-FIX6 · P1-2）。
//
// ══ 这条修的是什么 ═══════════════════════════════════════════════════════════════════════
// FIX2 起 `copy.ts` 一直写着「未成功的那一项**不计费**」，而 `copywriting-form.tsx` 把
// **任何 Promise rejection** 都当成失败。于是：**服务端已经 settle/commit、只是响应在网络里丢了**
// 的时候，界面照样说「该项未计费」—— 实际已扣。
//
// 🔴 根因是**观测范围错位**（CB 的定位，Cowork 承认是它给依据时没写清楚层次）：
//    「`_generate_billed_copy` 的 except 分支调 `release_copy_quota`」这句**成立于服务端的观测范围**，
//    而展示发生在**客户端的观测范围**。前端知道的是「**我的请求失败了**」，
//    不是「**服务端没扣我钱**」。这两件事之间隔着一整条网络。
//
// ══ 判据：不看 outcome 的内容，看它**在不在** ══════════════════════════════════════════════
// 先核过了（这是本轮必须回答的问题）：BE `schemas/response.py`
//     class OperationOutcome(BaseModel):
//         operation: str
//         status: Literal["succeeded", "failed"]
// **只有成败，没有任何计费结论字段** —— 所以 CB 说的"路 B：据 outcome 陈述计费"按字面并不成立。
//
// 但它仍然是可用的判据，只是理由换一个：
//   `error.outcome` 由 `core/exceptions.py:_copy_generation_error_outcome` 在**服务端的异常处理里**
//   生成。它出现 ⟺ 请求到达了应用层、FastAPI 的 exception handler 跑了。
//   网关 504、反代 502、连接中断**都伪造不出这个字段**。
//
// ⚠️🔴 **但只有 outcome 还不够** —— 核 `_copy_generation_error_outcome` 的实现发现：
//    它**只按 URL 后缀判断**（`path.endswith("/titles")` 之类），**完全不看错误的性质**。
//    于是 `/copy/titles` 上的**任何**异常都带 outcome，**包括 500 未处理异常**。
//    再核 `services/copy.py:_generate_billed_copy` 的实际结构：
//        reserve + commit          ← try **之外**（失败时压根没预留，安全）
//        try: invoke → transform → settle → commit; return
//        except BaseException: rollback → release → commit → re-raise / 转 502 COPY_GEN_FAILED
//    `except` 覆盖到 settle+commit，所以受控异常都安全。**唯一的空档是 `return` 之后**
//    ——路由返回到响应序列化之间若抛异常，钱已 settle，却仍走 500 handler、**照样带 outcome**。
//    所以判据必须再排除 500（`INTERNAL_SERVER_ERROR`，`exceptions.py:unhandled_exception_handler`）。
//
// ══ 最终判据 ═════════════════════════════════════════════════════════════════════════════
//    released ⟸ 有 failed outcome **且** HTTP 状态**不是 5xx-未处理**（即 status !== 500）
//               —— 这三类受控异常（AppError / HTTPException / RequestValidationError）
//                  要么在 reserve 之前抛出，要么由那条 except 在 release 之后重抛。
//    unknown  ⟸ 其余一切。**默认站在不承诺那一侧**，将来新增没想到的失败形态也自动落到安全侧。
//
// ⚠️ 这条推理的**边界**（写清楚，免得下一个人把它当成更强的保证）：
//    它证明的是"服务端走到了 release 那一步"，**不是**"release 一定提交成功"，
//    也**不是**从 outcome 的内容读出来的计费结论 —— 是从三段源码推出来的。
//    BE 只要改动 `_copy_generation_error_outcome` 的生成时机，这个推理就要重来。
//    **所以已在回执里向 CA 提了把计费结论直接放进 outcome 的请求**
//    （`billing: "released" | "charged" | "unknown"`），届时这里换成读字段，删掉整段推理。

/** 失败项的计费可陈述性。`released` = 服务端明确失败、可说未计费；`unknown` = 不许承诺。 */
export type CopyFailureBilling = "released" | "unknown";

/**
 * BE `schemas/copy.py`：`CopyGenerationOperation = Literal["rewrite", "titles", "topics"]`，
 * 且 `CopyGenerationOutcome.operation` 是**必填**。
 * 🔴 校验它而不只校验 status（PRICING-UI-0003 · CB P2-2）：这是一条**资金判据**，
 *    形状校验就该覆盖 BE schema 保证的**全部**约束，而不是只覆盖"我这次用得到的那个字段"。
 *    真实链路上 operation 恒合法，所以补它当前不改变任何行为 —— 但判据的防御面不该按
 *    "当前会不会出问题"来划，该按"BE 承诺了什么"来划。少验一个字段，就是给
 *    `{ status: "failed" }` 这种半份 outcome 开了一道能说出「未计费」的门。
 *    （同一条纪律在 402 那边叫「半份 detail 不许拼」，见 `aibrain/types.ts:parseInsufficientDetail`。）
 */
const COPY_OPERATIONS = ["rewrite", "titles", "topics"] as const;

function hasFailedOutcome(outcome: unknown): boolean {
  if (typeof outcome !== "object" || outcome === null) return false;
  const { operation, status } = outcome as { operation?: unknown; status?: unknown };
  // operation 必须是 BE 承诺的三个字面量之一 —— 缺失 / 拼错 / 是别的端点，一律不认。
  if (typeof operation !== "string" || !(COPY_OPERATIONS as readonly string[]).includes(operation)) return false;
  // 只认 "failed"。"succeeded" 却走到失败分支属于契约异常，按"说不准"处理而不是当成未计费。
  return status === "failed";
}

/**
 * 🔴 唯一判据函数。**故意收得很紧**，两个条件都满足才放行。
 * 变异①：去掉 `status !== 500` → 「500 未处理异常不许说未计费」会红。
 * 变异②：改成 `err.status >= 400 ? "released" : "unknown"` → 「网关 504 不许说未计费」会红。
 * 变异③：整个函数恒返回 "released" → 「网络中断」那条会红（任务包点名的那个变异）。
 */
export function copyFailureBilling(reason: unknown): CopyFailureBilling {
  if (typeof reason !== "object" || reason === null) return "unknown";
  const err = reason as { outcome?: unknown; status?: unknown };
  if (!hasFailedOutcome(err.outcome)) return "unknown";
  // 500 = `unhandled_exception_handler`，是唯一可能发生在 settle+commit **之后**的类别。
  return err.status === 500 ? "unknown" : "released";
}
