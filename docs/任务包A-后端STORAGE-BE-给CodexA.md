# 任务包 A ｜ 后端：对象存储 + 成片播放 ｜ `#M2-STORAGE-BE`

> 直接发给 Codex A。自包含，无需看别的文档。

- **负责**：Codex A（后端）
- **分支**：`feature/video-storage-minio` ← **从 `develop` 切**（不是 main！）。⚠️ Codex A 沙箱不能建分支，**用户先建好**。
- **合并目标**：`develop`
- **依赖**：无。本包先落地，定义接口契约给前端 `#M2-CONSOLE-POLISH-FE` 对接。

## 背景
成片现在落在本地 `file://` 路径，浏览器打不开 → 用户在控制台看不到生成的视频。决策：上对象存储（MinIO/S3）+ 预签名 URL，B 端企业正路。

## 范围（In scope）
1. **对象存储抽象层**：boto3，S3 兼容；MinIO 走 **path-style + 签名 v4**。
2. **worker 出片后上传**：moviepy 渲染完 → 上传 mp4（及封面/缩略图）到 bucket，按租户隔离 key；写回 DB 的 `storage_key`，替换 `file://`；标记 done。大文件用 multipart。
3. **Alembic 迁移**：`videos` 表新增 `storage_bucket / storage_key / thumbnail_key / content_type / size_bytes / duration_sec`（均可空，保留旧本地路径列向后兼容）。
4. **租户级接口**（同时清掉"视频列表持久化"backlog）：
   - `GET /api/v1/videos`（租户作用域列表，分页）
   - `GET /api/v1/videos/{id}`（详情；**跨租户/不存在统一 404**，防枚举）
5. **预签名 URL**：仅对调用方租户拥有的视频生成 `playback_url`(GET)、`download_url`(GET + `response-content-disposition=attachment`)、`thumbnail_url`；TTL 默认 3600s。
6. **compose 加 MinIO**：minio 服务 + 初始化容器建 bucket 设私有；backend/worker 注入 `ENGINE_S3_*`。

## 接口契约（前端按此对接，务必稳定）
```jsonc
// GET /api/v1/videos 与 GET /api/v1/videos/{id}（items 内单项）
{
  "id": "uuid",
  "title": "string",
  "prompt": "string",
  "mode": "static | seedance_t2v | seedance_i2v",
  "status": "queued | running | done | failed",
  "progress": 0,                 // 0-100
  "created_at": "ISO8601",
  "duration_sec": 12.5,          // 可空
  "thumbnail_url": "presigned|null",
  "playback_url":  "presigned|null",  // done 前为 null
  "download_url":  "presigned|null",  // attachment，done 前为 null
  "error": "string|null"
}
// 列表外层：{ "items": [...], "next_cursor": "string|null" }
```

## Key 布局
```
s3://{ENGINE_S3_BUCKET}/tenants/{tenant_id}/videos/{video_id}/output.mp4
                                                          .../thumbnail.jpg
```

## 安全要点（审查会盯）
- 预签名只对**本租户拥有**的视频签发；跨租户 id → 404（防 IDOR）。
- bucket **私有**，绝不开 public-read；访问全靠预签名 + 短 TTL。
- 不在日志/响应回显密钥或原始 key；内部 endpoint 不暴露给前端。
- 签名 v4，addressing=path-style。

## ⚠️ MinIO 预签名主机坑（最易翻车）
- 内部读写用 `ENGINE_S3_ENDPOINT=http://minio:9000`（compose 网内）。
- **给浏览器的预签名 URL 必须用 `ENGINE_S3_PUBLIC_ENDPOINT=http://localhost:9000`** —— 否则浏览器解析不了 `minio` 主机名。做法：用一个指向 public endpoint 的 boto3 client 专门生成预签名；内部存取另用 internal client（共享同一组凭据）。
- `<video>` seek 靠 S3 Range，预签名 GET 原生支持；用 fetch 下载/跨域时给 bucket 配 **CORS allow `http://localhost:3000`**。

## 新增 env（写进 infra/.env.example）
```env
ENGINE_S3_ENDPOINT=http://minio:9000
ENGINE_S3_PUBLIC_ENDPOINT=http://localhost:9000
ENGINE_S3_ACCESS_KEY=huading
ENGINE_S3_SECRET_KEY=<32+随机>
ENGINE_S3_BUCKET=huading-videos
ENGINE_S3_REGION=us-east-1
ENGINE_S3_SECURE=false
ENGINE_S3_PRESIGN_TTL=3600
MINIO_ROOT_USER=huading
MINIO_ROOT_PASSWORD=<同上或独立>
```
> 保持不变：`ENGINE_SEEDANCE_*`（是这个前缀，不是 SEEDANCE_）、`BACKEND_PORT=8080`、`NEXT_PUBLIC_API_BASE_URL=http://localhost:8080`、LLM 仍 DeepSeek（`ENGINE_LLM_`）、`JWT_SECRET_KEY`。

## 验收标准（AC）
- worker 出片后成片在 MinIO 可见，DB 记录 `storage_key`，不再有 `file://`。
- `GET /api/v1/videos` 只返回本租户视频；`playback_url/download_url` done 后可播、可下载、可 seek。
- 跨租户取他人视频 id → 404。
- Alembic up/down 干净。

## 回执（沿用任务包/回执格式）
分支 + commit/PR 号、迁移文件名、改动文件清单、新增 env 列表、跨租户 404 自测说明。
