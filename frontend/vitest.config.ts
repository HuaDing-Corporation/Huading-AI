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
    //
    // ⚠️ 给未来某个想开 `--no-isolate` 的人（FIX1 · Codex B）：本仓依赖 Vitest 2.1.9 的**默认 `isolate: true`**——
    // 每个测试文件独立模块实例，模块级 mock store（handlers.ts 里的 Map/数组/计数器）**跨文件**天然隔离，
    // 所以 batch/ecom/publish 等**尚未挂 reset** 的域在全仓 shuffle 下不红。但 isolate **只挡跨文件、不挡文件内**——
    // 文件内的 it 顺序依赖仍要靠 shuffle 探测 + reset 根治（reverse/admin 两域已做）。**一旦改为 `--no-isolate`，
    // 模块态会跨文件共享，必须重新审计并给所有模块级 mock store 补 reset**，否则顺序依赖会以跨文件的形式回来。
    sequence: { shuffle: true }
    // 全局 testTimeout **保持默认 5s**（FIX1 · Codex B #191 P1）：早先把全局放宽到 15s 是错的——它会让
    // 全部 778 条测试里**任何真实的慢测**在 5–15s 区间静默变绿，用一个远超必要的宽容度去处理一个局部噪声。
    // 真正需要放宽的只有 retry-disclosure 那三条 React 渲染测试（render + MSW 往返在 shuffle 高负载窗口偶发
    // 5s 超时——**负载噪声、非顺序依赖**，单跑无限稳定绿）。改为在那个 describe 上挂**定向** scoped timeout
    // 15s（见 retry-disclosure.test.tsx）。vitest 2.1.9 原生支持 `describe(name, { timeout }, fn)` 且 suite
    // option 合并进子测试——已用受控探针实测（scoped 300ms 覆盖全局 15s、300<600 时如期超时）。
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") }
  }
});
