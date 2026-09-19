# Stryker 变异测试 · 试点报告（STRYKER-PILOT-0001）

> **一句话结论**：**值得按 Codex B 的路径继续试**（PR 只跑改动文件 + report-only）。
> **成败判据（Q2）通过**：Stryker 抓到了 #187 那个真事故——同一个 mutant 在缺陷 fixture 下从 Killed 翻成 Survived。
> **但有两条硬约束**：① PR 单次有 ~2 分钟固定开销（全量 dry-run），做**阻断门**偏重、做**报告**可接受；② 有一类**系统性等价变异**（React 依赖数组）必须常设排除，否则污染分数。

- **分支**：`chore/stryker-pilot`（base = `develop@77911c73`）
- **只加**：`stryker.config.mjs` + 本报告 + `.gitignore` 两行 + `package.json`/lockfile 的两个 devDep。**未改任何产品代码或既有测试。**
- **工具**：`@stryker-mutator/core` + `@stryker-mutator/vitest-runner` `9.6.1`，复用既有 `vitest.config.ts`（**没碰它**，#191 还在审）。
- **环境**：Node 24.15、pnpm 9.15、vitest 2.1.9、并发 4。

---

## 一、跑了哪些目标

| 目标 | 为什么选 | 总 mutant | Killed | Survived | NoCov | Error | score(covered) |
|---|---|---|---|---|---|---|---|
| `lib/media/use-media-url-refresh.ts` | 今天栽 3 次的那个 hook（develop 上是收敛前的单 URL 版）| 17 | 15 | 2 | 0 | 0 | **88.24%** |
| `components/ui/copyable-block.tsx` | 有真变异门（「不谎报」承重）| 14 | 11 | 1 | 1 | 1 | **91.67%** |
| `components/history/history-set-dialog.tsx` | #187 事故现场 + 覆盖薄 | 37 | 19 | 17 | 1 | 0 | **52.78%** |
| **合计（一次跑）** | | **68** | **45** | **20** | **2** | **1** | **69.23%** |

`history-set-dialog.tsx` 52.78% 这个低分不是坏事——它**如实反映了**该组件的 meta/状态/分类/空态/loading/error 分支大片没测（见 §四分类）。这正是我们想要的信号。

---

## 二、Q2（成败判据）：能不能抓到 #187 那个真事故？——**能**

**事故回放**：`history-set-dialog.tsx` 有一行 `prompt={set?.meta?.prompt ?? set?.meta?.extra_prompt ?? null}`。修复前测试 fixture 是 `meta: {}`（空），所以**删掉整行、测试仍 26/26 全绿**——fixture 里没有的东西，测试永远测不到。

**做法**：同一个目标文件 `history-set-dialog.tsx` 跑两次 Stryker，**只换测试文件**：
- **fixed**：develop 当前测试（7 例，含提示词来源断言、fixture 给真 meta）。
- **deficient**：`git show 67bda53d:…test.tsx`（修复前，2 例，`meta: {}`，无提示词断言）——先跑 vitest 确认它在当前组件上 **2 例全绿**（事故态复现）。

**决定性对比**（prompt 行同两个 mutant）：

| prompt 行的 mutant（行为 ≈「接线没接上」）| fixed 测试 | deficient 测试 |
|---|---|---|
| `(set?.meta?.prompt ?? set?.meta?.extra_prompt) && null`（强制成 null）| **Killed** | **Survived** |
| `set?.meta?.prompt && set?.meta?.extra_prompt`（第一个 `??`→`&&`）| **Killed** | **Survived** |

fixture 从「有 meta」退回「`meta:{}`」，**同两个 mutant 从 Killed 翻成 Survived** → Stryker 把这个缺口标了出来。整体 score 也从 52.78% 掉到 44.44%，数值上同样反映缺口。

**Codex B 的前置条件（「mutator 得覆盖该表达式」）——成立且值得记**：Stryker **没有**「删整个 JSX 属性」这种 mutator。它是靠 `LogicalOperator`（`??`→`&&`、`A ?? B` → `(A ?? B) && null`）**碰巧**造出了一个行为等效于「删接线」的 mutant，才抓到的。**换句话说：信号真实，但不是任何 fixture 缺口都保证被造出对应 mutant。** 这条事故的形状恰好落在 `??` 上、被 LogicalOperator 覆盖到了；若缺口落在一个 Stryker 不变异的表达式上，就会漏。**是强信号，不是完备证明。**

---

## 三、Q3：多慢？

分阶段拆解（合并跑 3 文件、68 mutant、并发 4）：

| 阶段 | 耗时 | 说明 |
|---|---|---|
| **初始 dry-run**（全量测试建 perTest 覆盖率）| **~1 分 47 秒** | 其中真测试才 24s，**插桩/vitest 启动开销 ~83s** —— 这是**每次跑的固定成本**，与改几个文件无关 |
| **变异阶段**（68 mutant）| **~3 分 11 秒** | 平均每 mutant 跑 6 个覆盖到它的测试；perTest 把测试集缩到了最小 |
| **合计** | **~5 分钟** | |

- **单文件**：copyable ~119s / hook ~112s / hsd ~149s —— 单文件也躲不掉那 ~2 分钟 dry-run，所以单文件和 3 文件差别不大（**dry-run 摊薄**）。
- **典型 PR（3–5 文件）**：**~5–6 分钟**。做 **report-only 注解**可接受；做**阻断门**，这 2 分钟固定开销 + 网络波动会让人烦。
- **全仓 nightly 估算**：`src` 下 **180 个可变异文件、~21,800 LOC**；样本 253 LOC → 68 mutant ≈ **0.27 mutant/LOC** → 全仓约 **5,000–6,000 mutant**。按变异阶段 ~2.8s/mutant（并发 4）粗估 **~4–6 小时**（dry-run 全仓插桩会更久）。**nightly 跑得完，进不了 per-PR 阻断。**

---

## 四、surviving mutant 人工分类（Q1：等价变异有多少）

**逐个看完 20 个 survivor + 2 个 NoCoverage。结论：严格等价变异极少（≈1 个），其余全是真覆盖缺口。**

### 真漏网 / 真覆盖缺口（**能被更好的测试杀死**）——绝大多数

- **`copyable-block.tsx:48`（1 survived + 1 nocov）**：`setTimeout(() => setCopied(false), 1500)` 的 **1.5s 自动复位**。契约里写了「1.5s 后复位」，但没有测试用假定时器推进 1.5s 去验证 → mutant 存活。**这正是「我扫了一遍」会漏、Stryker 标出来的东西。**
- **`use-media-url-refresh.ts:58`（1 survived）**：`if (!url) return` → `if (false)`。要 url 从有值转 null 后再 onError 才触发，边角、低危，但**非等价**——一条 url→null 的序列测试能杀掉它。
- **`history-set-dialog.tsx` 的 15/17 survivor**：全是没测到的分支——
  - `:95` retry 按钮 onClick（**NoCoverage**：没有 isError fixture，error 分支根本没渲染过）
  - `:99` `set && set.items.length > 0`（空态 / 无 set 分支没测）
  - `:101` `set.status === "partial_failed"` 的提示（没有 partial_failed fixture）
  - `:60` 四个 OptionalChaining（`set?.meta`→`set.meta`）：**loading 态**（set=undefined）没被渲染过，故去掉 `?.` 不抛错 → 存活。**非等价**，一条 loading fixture 能杀掉。
  - `:35/:37` 分类标签回退分支、`:40` `item!==null` 的 open 判断——同类，未测分支。

### 严格等价变异（**任何测试都杀不掉，必须人工排除/基线化**）——只 1 个，但**系统性**

- **`use-media-url-refresh.ts:69`**：`}, [])` → `}, ["Stryker was here"])`（`ArrayDeclaration` 变 onLoad 的 **useCallback 依赖数组**）。该 callback 只闭包稳定的 `useRef`，依赖数组填什么都不改变行为 → **真等价、不可杀**。
  - ⚠️ **这不是一次性噪音，是一个模式**：Stryker 的 `ArrayDeclaration` mutator 会在**每个** React 组件/hook 的 `[]` 依赖数组上复发。本试点才 3 个文件就中一个；全仓 180 个文件会累积成稳定的假 survivor 流。**上门禁前必须常设排除**（例如对依赖数组位置禁用 `ArrayDeclaration`，或把这类基线化），否则分数被系统性拉低。**但按任务包 §三：不为追分数改生产代码，只排除工具噪音。**

### 归类小注

- `copyable-block.tsx:44` 的 **RuntimeError**（`navigator.clipboard?.writeText` 去掉 `?.`）：被「不谎报」那条承重测试**侦测到了**（clipboard 缺失 → 抛错 → 测试失败），只是 Stryker 把「mutant 让 runner 抛错」归成 `RuntimeError` 而非 `Killed`。**不是漏网**，读分数时要把 RuntimeError 计入「已侦测」。

**Q1 小结**：本样本等价变异占比 **~1/68 ≈ 1.5%**，信噪比**目前很好**；但那 1 个来自一个**会随文件数线性增长**的系统性源（React 依赖数组），需要一条常设排除规则来摁住。

---

## 五、Q4：值不值得进 CI —— 我的判断

**值得，按 Codex B 的路径，且我加两条基于实测的收紧：**

1. **先 report-only、PR 只跑改动文件、2–4 周**（照 Codex B）。理由：Q2 证明它**正好命中我们反复栽的那个失败模式**（fixture 缺口 → surviving mutant）——这是「变异由人挑会漏」的直接解药，今天四次同款事故里至少 #187 那次它能兜住。
2. **别做阻断门（先）**：那 ~2 分钟固定 dry-run 开销 + vitest 启动，做每次 PR 的硬门槛体感差；report-only 注解让 reviewer 在改动文件上读 survivor 列表，性价比最高。
3. **上线前先落一条等价变异排除**：把 React 依赖数组的 `ArrayDeclaration` 噪音摁住（见 §四），否则分数会被系统性污染，重蹈「仪表盘没人信」。
4. **门槛（若将来上）照 Codex B**：普通改动 changed-code ≥75%、资金/权限/租户安全 ≥85%、无未解释的高风险 surviving mutant、等价变异人工确认排除、不追 100%、不为分数改生产设计。
5. **全仓放 nightly**（~4–6h，跑得完），PR 只跑 changed-files。

### 一条必须写下的诚实边界

Q2 能过，是因为事故形状（`??` 链）**恰好**被 Stryker 的 LogicalOperator 覆盖到了。**它不保证抓到每一种 fixture 缺口**——若缺口落在 Stryker 不变异的表达式/结构上（例如某些 JSX 结构、某些 map 渲染），一样会漏。所以它是**给「人挑变异」加一层网、而不是替代**：它能兜住我们栽过的那类，但「它绿了」≠「没缺口」。**这条不写清楚，就会变成又一个「装好了没人质疑的仪表盘」。**

---

## 六、全门（带退出码）

均在 `chore/stryker-pilot`、只加配置+报告的状态下跑：

| 门 | 命令 | 退出码 |
|---|---|---|
| 既有测试套件（未动，确认没被带坏）| `vitest run` | `EXIT=0`（778 passed / 117 files）|
| 类型 | `tsc --noEmit` | `EXIT=0` |
| Lint | `eslint src` | `EXIT=0` |
| Stryker 冒烟（管道通）| `stryker run --mutate copyable-block.tsx` | `EXIT=0`（score 91.67%）|

（Stryker 的 `thresholds.break` 设为 0 → 试点期永不因分数失败退出。）

---

## 七、如何复跑

```bash
cd frontend
pnpm install
# 单文件：
npx stryker run --mutate src/lib/media/use-media-url-refresh.ts
# 多文件（典型 PR）：
npx stryker run --mutate "src/a.tsx,src/b.ts"
# 报告：reports/mutation/mutation.html（人看）/ mutation.json（脚本统计）
```

Q2 复现（事故态）：把 `git show 67bda53d:frontend/src/components/history/history-set-dialog.test.tsx` 覆盖到当前测试文件上，跑 `--mutate src/components/history/history-set-dialog.tsx`，看 `:60` 的 LogicalOperator mutant 从 Killed 翻成 Survived；跑完 `git restore` 还原。
