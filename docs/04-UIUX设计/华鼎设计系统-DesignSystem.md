# 华鼎 AI · 设计系统（Design System v1.0）

> 对应 SOP-4（UI UX Pro Max）定稿。视觉方向已确认：**暖香槟鎏金 × 苹果液态玻璃**，浅色、桌面优先。
> 本文件是 Frontend-design（SOP-5）实现控制台的唯一视觉依据。配套预览：`华鼎-控制台-视觉方向-LiquidGlass.html`、`华鼎-Logo-磨砂玻璃方案.html`。

---

## 1. 品牌

- **名称**：华鼎 AI（HUADING）· VIDEO ENGINE
- **气质**：高级、克制、有底蕴（"鼎"=青铜重器）；延续华鼎集团香槟鎏金质感。
- **Logo**：
  - **主视觉/启动页/官网 hero** → AI 渲染的「磨砂玻璃三足鼎」（近白半透 + 三足晕金 + 柔光悬浮），用 PNG/高清图。
  - **控制台 UI / favicon / 导航** → 矢量「几何鼎」标（双耳+鼎身+双足），古铜金渐变 `#caa55f → #6e521f`，嵌入磨砂玻璃徽。
  - Logo 不可拉伸变形、不可换非品牌色、留白≥徽宽 25%。

---

## 2. 色彩 Token

### 主色（暖香槟鎏金）
| Token | 值 | 用途 |
|---|---|---|
| `--grad-gold` | `linear-gradient(135deg,#ecd9aa 0%,#c8a563 52%,#a37f3f 100%)` | 主按钮、激活态、缩略图、进度条、头像 |
| `--gold` | `#bd9a59` | 金色文字/图标 |
| `--gold-deep` | `#8c6f3a` | 选中态文字、强调金字 |
| `--bronze` | `#6e521f` | Logo 深色端、压重 |
| `--champ` | `#e6d3a3` | 香槟高光、虹彩 |
| `--logo-grad` | `#caa55f → #6e521f` | Logo 渐变 |

### 中性 / 文字
| Token | 值 | 用途 |
|---|---|---|
| `--ink` | `#3a3320` | 主文字 |
| `--ink-soft` | `#6f6650` | 次要文字 |
| `--ink-faint` | `#a89c80` | 提示/占位 |
| `--bg-base` | `#f5efe2` | 页面底色 |

### 页面背景（暖香槟光晕）
```css
background:#f5efe2;
background-image:
  radial-gradient(1200px 820px at 50% -12%,rgba(255,255,255,.95),transparent 62%),
  radial-gradient(940px 720px at 88% 8%,rgba(229,210,162,.46),transparent 60%),
  radial-gradient(880px 720px at 10% 96%,rgba(238,224,189,.52),transparent 60%),
  radial-gradient(700px 600px at 70% 100%,rgba(213,182,122,.3),transparent 58%);
```

### 边框
| Token | 值 | 用途 |
|---|---|---|
| `--bd-gold` | `rgba(189,154,89,.64)` | **所有输入框/选择框/筛选项的淡金边** |
| `--bd-glass` | `rgba(255,255,255,.78)` | 玻璃面板边 |
| `--bd-sel` | `rgba(168,134,62,.9)` | 选中态金边（加重） |

### 语义色
| 状态 | 底 / 字 |
|---|---|
| 成功 | `rgba(106,166,127,.2)` / `#447d5f`（渐变 `#a7cba0→#6aa67f`） |
| 进行中 | `rgba(189,154,89,.2)` / `--gold-deep` |
| 排队/禁用 | `rgba(155,128,62,.15)` / `--ink-faint` |

---

## 3. 液态玻璃材质（核心）

所有面板/卡片用 `.glass`：
```css
.glass{
  position:relative;
  background:linear-gradient(158deg,rgba(255,255,255,.60) 0%,rgba(255,255,255,.50) 60%,rgba(231,210,162,.30) 100%);
  backdrop-filter:blur(34px) saturate(1.32);-webkit-backdrop-filter:blur(34px) saturate(1.32);
  border:1px solid var(--bd-glass);
  box-shadow:
    8px 18px 48px -16px rgba(150,118,52,.26),  /* 右下悬浮投影 */
    0 0 28px rgba(231,210,162,.24),            /* 边缘柔光 */
    inset 0 1px 0 rgba(255,255,255,.92);        /* 顶部斜面高光 */
}
```
要点：近白半透磨砂主体 + **底部香槟虹彩渐变** + 边缘柔光 + 右下悬浮投影 + 顶部高光、圆润无硬边。

---

## 4. 字体与排版

- **字体栈**：`-apple-system, "SF Pro Display", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif`
- **字重**：常规 400、中 500、半粗 600（标题/Logo）。
- **阶梯**：
  | 角色 | 字号/字重 |
  |---|---|
  | 页面大标题 H1 | 27px / 600，字距 1px |
  | 卡片标题 H3 | 16px / 600 |
  | 正文 | 14px / 400 |
  | 次要/标签 | 12.5–13px / 400–500 |
  | 字母小标（英文/编号） | 11–12px，字距 3px，`--ink-faint` |
- **大小写**：句式大小写；英文小标可全大写带宽字距（如 `VIDEO ENGINE` / `HUADING`）。

---

## 5. 组件规范

> **金底文字规则（无障碍）**：浅香槟金渐变上**一律用深墨字/图标 `--ink (#3a3320)`，不用白字**（白字对比度仅 1.4–2.6:1，不可读）。深墨字在金底上像金箔深雕，清晰且高级。适用：主按钮、激活导航、头像、任务缩略图图标等所有金底元素。

| 组件 | 规范 |
|---|---|
| **主按钮** | `--grad-gold` 底、**深墨字 `--ink`**、圆角 17px、`box-shadow 0 13px 30px rgba(156,124,62,.4)+inset高光`、按下 scale .98 |
| **输入框/选择框** | 白半透底 `rgba(255,255,255,.42)`、**`--bd-gold` 淡金边**、圆角 16px、聚焦金边加重 + 柔光环 |
| **选择 chip** | 同输入框；选中态 `--bd-sel` 金边 + 香槟底 + `--gold-deep` 字 |
| **卡片** | `.glass`、圆角 26px、内距 24–26px |
| **侧栏导航** | 普通项透明、hover 白半透；激活项 `--grad-gold` 底 + **深墨字 `--ink`** + 投影 |
| **状态徽** | 语义色底+字、圆角 11px、12px/500 |
| **头像** | 圆形、`--grad-gold` 底、**深墨字 `--ink`** |
| **进度条** | 轨 `rgba(155,128,62,.16)`、条 `--grad-gold` |
| **图标** | lucide-react 描述性图标，18–20px；金底上的图标用 `--ink` |

---

## 6. 布局 / 圆角 / 间距 / 动效

- **布局**：桌面优先，最大宽 1280px；左侧栏 248px + 主区；顶部条通栏。
- **圆角阶梯**：徽/小件 13–16px｜面板/卡片 20–26px｜按钮 17px。
- **间距**：模块间 18–20px；卡片内 12–16px。
- **动效**：克制——hover 背景渐显、按下 scale .98、玻璃面板入场轻微淡入上浮（≤200ms，ease-out）；不要花哨。
- **深浅色**：默认浅色；深色模式 M2 后续再补（预留 token 切换）。

---

## 7. 给 Frontend-design 的实现要求

1. 技术栈：Next.js + TypeScript + Tailwind；把以上 token 落进 Tailwind theme / CSS 变量。
2. 抽成可复用组件：`Glass`（容器）、`Button`、`Input`、`Select/Chip`、`Card`、`SidebarNav`、`StatusBadge`、`Avatar`、`Progress`、`Logo`。
3. `.glass` 的 `backdrop-filter` 注意性能与浏览器兼容（加 `-webkit-`，低端降级为高白不透底）。
4. 严格用 token，不硬编码颜色；浅色优先、预留深色。
5. 首屏对齐预览稿：顶部条（Logo+搜索+操作）/ 侧栏导航 / 工作台（新建视频卡 + 生成任务列表）。
