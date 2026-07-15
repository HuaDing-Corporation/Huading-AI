import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

// Next.js 16：@next/eslint-plugin-next / eslint-config-next 改为原生 ESLint Flat Config 导出
// （不再走 @eslint/eslintrc 的 FlatCompat 包装——旧写法会触发 circular structure 报错）。
// 直接 spread 官方 flat 数组，等价于旧的 extends("next/core-web-vitals", "next/typescript")。
// next lint 已在 16 移除，lint 脚本改 `eslint src`（src 即 next lint 对本项目的原有效扫描范围）。
const eslintConfig = [
  ...coreWebVitals,
  ...typescript,
  {
    // eslint-config-next 16 捆绑的新版 eslint-plugin-react-hooks 新增 set-state-in-effect 规则，
    // 命中 12 处既有合法模式（如依赖变更时重置 state）。本包是纯依赖安全升级、边界禁止改业务逻辑，
    // 关闭该新规则以保持升级前的 lint 契约不变；这 12 处的 setState-in-effect 另开包评估（见回执）。
    rules: {
      "react-hooks/set-state-in-effect": "off"
    }
  }
];

export default eslintConfig;
