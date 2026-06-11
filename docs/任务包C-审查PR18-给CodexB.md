# 任务包 C ｜ 审查 PR #18 ｜ `#M2-CONSOLE-POLISH-RV`

> 直接发给 Codex B。自包含。

- **对象**：PR **#18** `feat(console): ... (#M2-CONSOLE-POLISH-FE)` ← `feature/console-polish`（commit `0cbba9b`，8 文件 +327/-109），base=`develop`，当前为 **Draft**。
- **背景**：这是 #17（后端存储，base=develop，也 Draft）配套的前端。**两者必须同批合**——#17 升级了 API 契约，#18 把前端迁到新契约。单独合任一方都破 develop。
- **约束**：本机无 A 的后端（MinIO/presign/Ark），**运行态 e2e（真实播放/hydrate）留到部署后**；本轮审查聚焦**代码级正确性 + 契约一致性**。

## 重点必查

1. **B-契约双映射（核心，P1 根因区）** —— `lib/videos/tasks-context.tsx`：
   - 后端**两套形状不一致**：`GET /videos`/`{id}` 用新 `VideoRead`（小写 `queued/running/done/failed`、progress `0..100`、`playback_url/download_url`）；但 **SSE 仍发旧 worker 帧**（大写 `SUCCESS/PROGRESS`、progress `0..1`、`video_url`）。
   - 查 `mapSseStatus` 是否把大写帧正确映射，且对未来新枚举（`DONE/RUNNING/FAILED`）也兼容。
   - 查 **progress 量纲**：SSE 的 `0..1` 是否正确换算到 `0..100`，无双重缩放/越界。
   - 查 **reconcile 权威性**：流结束/轮询用 `GET /videos/{id}` 取权威 `playback_url`，**不会出现"完成→排队中"回退**（这正是被打回的 P1）。
   - 状态合并优先级：SSE 实时 vs GET 结果谁覆盖谁，是否会把已 `done` 覆盖回低状态。

2. **列表 hydrate（B4）** —— 挂载 `GET /api/v1/videos` 填充 + 按 **id 合并** SSE：无重复、无错位、刷新不丢历史；无后端时优雅降级空状态。

3. **播放器（B3）** —— `task-list.tsx`：`<video>` 用 `playback_url`、下载用 `download_url`、poster 用 `thumbnail_url`；`onError`（URL 过期）**只重拉一次** `GET /videos/{id}` 刷新 URL，**无重试死循环**。

4. **类型一致性** —— `lib/api/types.ts` 的 `VideoRead/VideoListResponse/VideoStatus/VideoEvent` 与 #17 后端 `schemas/videos.py` **逐字段对齐**（字段名、可空性、status 枚举值）；全仓**无残留** `video_url` / `VideoTaskStatus` 旧引用。

5. **B0 object URL** —— `new-video-card.tsx`：ref 仅卸载兜底 `revokeObjectURL`，不破坏现有 clear/替换逻辑，无重复释放副作用。

6. **布局/移动端** —— 模式 pill 无重叠、可选；chip `flex-wrap` 无逐字竖排；生成按钮横排；返回（B2）路由正确；TopBar `<sm` 收起通知/设置、无溢出（回归点：之前 436/390 → NO-OVERFLOW）；其它断点无新溢出。

7. **通用** —— upload/submit/fetch 错误态友好；无 console 报错；无死代码；lint(严格)+build 已绿（复核）。

## 回执（沿用格式）
- 逐项结论 + 必改项分级（P0/P1/P2）。
- 重点给出：双映射/reconcile 是否会回退、类型与后端是否逐字段一致、`onError` 是否只刷一次。
- 明确：哪些项是**代码级已确认**、哪些**必须等部署后 e2e**（播放/hydrate 运行态）。
- 结论建议：可否在"与 #17 同批 Ready + CI 绿"后合并。
