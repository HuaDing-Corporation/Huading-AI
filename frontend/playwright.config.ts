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
    trace: "on-first-retry"
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npx next start -p 3100",
    url: "http://localhost:3100",
    timeout: 120_000,
    reuseExistingServer: !process.env.CI
  }
});
