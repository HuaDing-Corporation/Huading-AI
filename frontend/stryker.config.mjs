// @ts-check
/**
 * Stryker 变异测试 · 试点配置（STRYKER-PILOT-0001）
 *
 * 🔴 本配置只服务「试点」——不进 CI、不设门槛、不为分数改任何生产代码/测试。
 *
 * 设计约束（任务包 §五）：
 *  - **不改 `vitest.config.ts`**（它被 #191 动过、还在审）—— 这里用 `vitest.configFile` 复用它，只读不写。
 *  - 默认不圈定 `mutate` 目标：试点按文件逐个 `--mutate <file>` 跑，便于分别计时 + 分别读报告。
 *
 * 复用 vitest 的全套设置（jsdom / globals / setupFiles / `@` alias）——Stryker 的 vitest runner
 * 直接加载 `vitest.config.ts`，所以 msw、testing-library、Radix 的 polyfill 全部照常生效。
 */
export default {
  testRunner: "vitest",
  // pnpm 的严格 node_modules 布局下，Stryker 默认的 `@stryker-mutator/*` 插件发现扫不到 runner
  // （symlink 在顶层 node_modules/@stryker-mutator/ 但 glob 解析不到）→ 显式声明。
  plugins: ["@stryker-mutator/vitest-runner"],
  vitest: {
    // 复用既有配置，不复制、不修改。
    configFile: "vitest.config.ts"
  },

  // perTest：先做一次带覆盖率的 dry-run，之后每个 mutant 只跑「覆盖到它的那些测试」——
  // 对单文件目标能把每个 mutant 的测试集缩到最小，是本试点测速的关键（任务包 §四.3）。
  coverageAnalysis: "perTest",

  // 默认目标（可被 CLI `--mutate` 覆盖）。试点逐个跑，这里给个稳妥缺省。
  mutate: [
    "src/lib/media/use-media-url-refresh.ts",
    "src/components/ui/copyable-block.tsx"
  ],

  // 报告：html 给人看，json 给脚本统计 surviving/equivalent，clear-text + progress 给终端。
  reporters: ["html", "json", "clear-text", "progress"],
  jsonReporter: { fileName: "reports/mutation/mutation.json" },
  htmlReporter: { fileName: "reports/mutation/mutation.html" },

  // 并发：留 2 核给系统（本机 vitest 单跑就吃满）。试点先保守，测速以此为准。
  concurrency: 4,

  // waitFor / Radix 动画在 jsdom 下偶尔偏慢，给足超时避免把「慢」误判成「杀死」。
  timeoutMS: 20000,
  timeoutFactor: 2,

  // Stryker 会给沙箱里的 TS 关类型检查（插桩后的中间态本就不合法）；vitest 本身也不做 tsc。
  disableTypeChecks: true,

  tempDirName: ".stryker-tmp",
  cleanTempDir: true,

  // 试点阶段不设门槛（任务包 §三：不定 score）。给 break:0 让它永不因分数失败退出。
  thresholds: { high: 80, low: 60, break: 0 }
};
