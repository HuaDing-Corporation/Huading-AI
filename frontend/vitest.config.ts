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
    exclude: ["**/node_modules/**", "**/dist/**", "e2e/**"]
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") }
  }
});
