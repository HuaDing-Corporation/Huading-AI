import { defineConfig, devices } from "@playwright/test";

/**
 * 交互冒烟（ECOM-FIXES-0001 ③「根本堵漏」）。生产构建(next start)下真点交互，抓 vitest 测不出的
 * 运行时 #130（prod tree-shaken）+ /api/api 双前缀。webServer 启已构建的 next start；构建须以
 * NEXT_PUBLIC_USE_MOCK=1 完成（MSW 供数据，CI 无真后端）。
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "list" : "list",
  use: {
    baseURL: "http://localhost:3100",
    // 🔴 PRICING-UI-0001-FIX1 §六：`on-first-retry` 有个正中要害的盲区 —— 它只在**重试**时录，
    //    而本地 `retries: 0`，于是「第一次就失败、没有重试」这条最常见的路径**什么都不录**。
    //    本包就栽在这上面：一次 aibrain smoke 失败，重跑三次全绿，手里只有一份 error-context.md，
    //    无法归因。改成 retain-on-failure：只要失败就留 trace，CI 与本地都覆盖，绿的时候不产生文件。
    trace: "retain-on-failure",
    screenshot: "only-on-failure"
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    {
      name: "mobile-workbench",
      testMatch: /pricing-workbench-doc\.smoke\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 } }
    }
  ],
  webServer: {
    command: "npx next start -p 3100",
    url: "http://localhost:3100",
    timeout: 120_000,
    reuseExistingServer: !process.env.CI
  }
});
