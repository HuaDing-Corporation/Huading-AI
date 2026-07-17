import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    // e2e/ 是 Playwright 交互冒烟（*.spec.ts），不归 vitest 跑（ECOM-FIXES-0001 ③）。
    exclude: ["**/node_modules/**", "**/dist/**", "e2e/**"],
    // ADMIN-MOCK-STORE-RESET-0001：全仓开随机顺序（文件 + 文件内用例都洗）。顺序依赖 = 模块级 mock 态
    // 跨测试泄漏，人扫不可靠（同一份代码换 seed 红 1~6 条不等，「红几条」本身就不稳定）；shuffle 让它
    // 自己变红，不再靠人记「谁必须放最后」的座位表。seed 默认 = Date.now()（随机，每次 CI 不同）——比固定
    // seed 好：固定 seed 只测一个排列。失败时 vitest 打印 seed，本地 `--sequence.seed=<seed>` 逐字复现。
    // 反推域(#183)、admin(本次)均已 shuffle-clean，先行两域进 CI。
    sequence: { shuffle: true },
    // shuffle 的配套：重排执行顺序会改变并发负载分布，默认 5s 下个别 React 渲染测试（render + MSW 往返）
    // 在高负载窗口偶发超时——那是**负载噪声、非顺序依赖**（单跑无限稳定绿）。实测 5s 下全仓 shuffle 约 1/6
    // seed 撞一次；15s 在本地最坏负载（多 worktree 并发）下 4/4 seed 零超时。放宽只增宽容、不会让任何测试
    // 变红，避免 shuffle 把一个既有的负载抖动变成别人 PR 的无故红。
    testTimeout: 15000
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") }
  }
});
