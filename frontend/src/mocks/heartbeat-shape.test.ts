import { describe, expect, it } from "vitest";

import { getEcomReplicateJob, planEcomReplicate, confirmEcomReplicate } from "@/lib/api/ecom-replicate";

// GEN-HEARTBEAT-UI-0001 · FIX2 · **mock 形状 = BE 真形状** 承重（真打 MSW，不 mock adapter）。
//
// 为什么值得单独一条网：#220 刚吃过大亏——两个用户可见缺陷**全部**由「mock 形状与 BE 不同」结构性遮住
// （一个是 BE 恒发 null 键而 mock 用"整键不出现"，一个是 mock 步长 +2 恰好跳过所有奇数）。形状不对，
// 上面所有测试都可能是在测一个**不存在的后端**。
//
// BE 真形状（#222 实测，非契约转述）：
//   值：Python `datetime.now(UTC).isoformat()` → "2026-07-25T13:16:56.439672+00:00"
//       = 6 位微秒 + `+00:00` 偏移。**不是** JS `toISOString()` 的 "...951Z"（3 位毫秒 + Z），
//       **更不是** epoch 毫秒（冻结 §四 写的"ISO8601 或 epoch ms"二选一，实际只有前者）。
//   通道②（详情图 GET）：schema `str | None` 默认 None → **键恒存在**，降级时值为 null（HTTP 仍 200）。
//
// 🔴 期望值**手写正则**，不 import mock 里的 beHeartbeatNow——否则那个函数改成什么，期望值就跟着变成什么，
//    等于没有网（本项目认过的、最容易掩盖问题的动作）。
const BE_ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$/;

async function drivenToGenerating() {
  const plan = await planEcomReplicate({
    reference_image_asset_ids: ["ref-1"],
    product_image_asset_ids: ["prod-1"],
    product_info: { name: "保温杯" },
    selling_points: ["锁温"],
    output_mode: "main"
  });
  await confirmEcomReplicate(plan.job_id);
  return getEcomReplicateJob(plan.job_id);
}

describe("mock 的 heartbeat_at 与 BE 逐字同形（FIX2 真联调）", () => {
  it("详情图轮询：键恒存在，生成中时值是 BE 形状（6 位微秒 + +00:00，非 Z 非 epoch）", async () => {
    localStorage.removeItem("hd_mock_heartbeat_null");
    const job = await drivenToGenerating();

    expect("heartbeat_at" in job).toBe(true); // 键恒在（与通道① 的"整键不出现"相反）
    expect(typeof job.heartbeat_at).toBe("string"); // 不是 number → 不存在 epoch ms 这一支
    expect(job.heartbeat_at as string).toMatch(BE_ISO);
    // 反断言：绝不能退回 JS toISOString() 的形状。
    expect(job.heartbeat_at as string).not.toMatch(/Z$/);
    expect(job.heartbeat_at as string).not.toMatch(/\.\d{3}\+/); // 3 位毫秒也不行，BE 是 6 位
  });

  it("详情图轮询 · Redis 不可用降级：键仍在、值为 null、HTTP 仍 200（业务轮询不受影响）", async () => {
    localStorage.setItem("hd_mock_heartbeat_null", "1");
    try {
      const job = await drivenToGenerating(); // 不抛 = 仍 200
      expect("heartbeat_at" in job).toBe(true);
      expect(job.heartbeat_at).toBeNull();
    } finally {
      localStorage.removeItem("hd_mock_heartbeat_null");
    }
  });
});
