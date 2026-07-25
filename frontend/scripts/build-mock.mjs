// mock 构建（CI「Build (mock, for interactive smoke)」那一步的本地等价物）。
//
// 为什么要一个脚本而不是 `NEXT_PUBLIC_USE_MOCK=1 next build`：那个写法在 Windows 的 cmd/PowerShell 下
// 不生效，而本项目的开发机就是 Windows —— 于是「本地跑的门」和「CI 跑的门」会悄悄变成两回事，
// 正是 REVERSE-DEEP-UI-0001-FIX3 要堵的那类洞。用 node spawn 显式注入环境变量，跨平台一致，且零新依赖。
import { spawnSync } from "node:child_process";

const result = spawnSync("next", ["build", "--webpack"], {
  stdio: "inherit",
  shell: true,
  env: { ...process.env, NEXT_PUBLIC_USE_MOCK: "1" }
});

process.exit(result.status ?? 1);
