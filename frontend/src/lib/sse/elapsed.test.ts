import { describe, expect, it } from "vitest";

import { HARD_CAP_MS, STALL_MS } from "./constants";
import { formatElapsed } from "./elapsed";

// GEN-HEARTBEAT-UI-0001 · 格式化器 + 阈值未变承重。期望值手写。

describe("formatElapsed · 诚实计时（宁可少报一秒，不许多报）", () => {
  it("秒 / 分秒 / 时分秒 三档", () => {
    expect(formatElapsed(0)).toBe("0 秒");
    expect(formatElapsed(20_000)).toBe("20 秒");
    expect(formatElapsed(200_000)).toBe("3 分 20 秒"); // 冻结文档里的例子
    expect(formatElapsed(3_600_000)).toBe("1 小时 0 分 0 秒");
    expect(formatElapsed(3_800_000)).toBe("1 小时 3 分 20 秒");
  });

  it("向下取整到秒（不四舍五入）：低报等待时间属于不诚实的那一侧", () => {
    expect(formatElapsed(19_600)).toBe("19 秒"); // 不许进位成 20 秒
    expect(formatElapsed(59_999)).toBe("59 秒"); // 不许进位成 1 分 0 秒
  });

  it("负数（时钟回拨 / 基准比现在晚）夹到 0，不吐 '-1 秒'", () => {
    expect(formatElapsed(-5_000)).toBe("0 秒");
  });
});

// 🔴 门5：**本包不动看门狗阈值**（冻结 H5：收窄的前提是"心跳已覆盖所有链路"，那是需要真机验证的
// 事实，不是写完代码就成立的假设；哪条链路漏发心跳，收窄当天就是大规模误杀）。
// 这条是**逐字锁值**的防误改网——变异：把 STALL_MS 改成任何别的值 → 本条必红。
// （既有的 tasks-context.watchdog.test.tsx 只断言 "> BE 1500s"，收窄到 1501s 也能过；这里补精确值。）
describe("看门狗阈值未被本包顺手改（GEN-HEARTBEAT-UI-0001 §三.3）", () => {
  it("STALL_MS 仍是 1_620_000（27min），HARD_CAP_MS 仍是 1_800_000（30min）", () => {
    expect(STALL_MS).toBe(1_620_000);
    expect(HARD_CAP_MS).toBe(1_800_000);
  });
});
