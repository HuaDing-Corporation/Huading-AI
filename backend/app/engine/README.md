<!--
Copyright (C) 2026 Huading

This documentation describes the Huading video engine, which is derived from
Pixelle-Video (Copyright (C) 2025 AIDC-AI, licensed under Apache-2.0).
See ./LICENSE and ./NOTICE.
-->

# 华鼎视频引擎（Huading Video Engine）模块说明

本模块是从 **Pixelle-Video**（AIDC-AI，Apache-2.0）蒸馏而来的视频生成核心。
已剥离 Streamlit/Web 展示层与单机 `config.yaml`，改为 **平台密钥托管 + 运行参数入参**
的接口形态：平台构造一个 `EngineConfig`（填入托管密钥与运行选项）并调用
`create_engine()`，引擎不再从磁盘读取任何配置。

> 合规：本目录保留上游 `LICENSE`/`NOTICE`，所有华鼎新增/修改文件均按 Apache §4(b)
> 标注修改声明。详见文末「合规说明」。

---

## 1. 目录结构

```
backend/app/engine/
├── __init__.py            # 对外入口：导出 EngineConfig / create_engine / configure_runtime
├── config.py              # EngineConfig（平台注入配置）+ to_pixelle_config() 映射
├── factory.py             # create_engine() / configure_runtime()（设置 PIXELLE_VIDEO_ROOT + 注入单例）
├── LICENSE / NOTICE       # 上游 Apache-2.0 许可与声明（保留）
├── runtime/               # 资源根（PIXELLE_VIDEO_ROOT 指向此处）
│   ├── templates/         # HTML 帧模板（static_* / image_* / video_*）
│   ├── bgm/               # 背景音乐
│   ├── workflows/         # ComfyUI / RunningHub 工作流
│   └── output/            # 生成产物（已 .gitignore）
└── pixelle_video/         # vendored 引擎包（绝对导入，作为顶层包解析）
    ├── service.py         # PixelleVideoCore：装配所有服务 + 注册 pipeline
    ├── config/            # PixelleVideoConfig schema + config_manager 单例
    ├── pipelines/         # base / linear / standard / custom / asset_based
    ├── services/          # llm / tts / media / api_media / video / frame_processor / persistence ...
    │   └── api_services/  # 直连各家 API 的 client（GPT/Gemini/DeepSeek/Qwen/Kling/Seedream/Seedance...）
    ├── models/            # storyboard / media / progress 数据模型
    ├── prompts/           # LLM 提示词
    └── utils/             # os_util / template_util / content_generators 等
```

---

## 2. 调用入口

```python
from app.engine import EngineConfig, create_engine

cfg = EngineConfig(
    llm_api_key="sk-...",                 # 必填（平台托管）
    llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",  # 必填
    llm_model="qwen-max",                 # 必填
    default_template="1080x1920/static_default.html",  # static_* 无需 ComfyUI
)

engine = await create_engine(cfg)         # 返回已 initialize 的 PixelleVideoCore
try:
    result = await engine.generate_video(
        text="如何提高学习效率",
        pipeline="standard",
        mode="generate",
        n_scenes=3,
        frame_template="1080x1920/static_default.html",
    )
    print(result.video_path, result.file_size, result.duration)
finally:
    await engine.cleanup()
```

`create_engine(cfg)` 内部：
1. `configure_runtime(cfg)`：设置 `PIXELLE_VIDEO_ROOT` 指向 `runtime/`，并通过
   `config_manager.configure_from_config(cfg.to_pixelle_config())` 注入全局配置；
2. 导入并实例化 `PixelleVideoCore`，`await core.initialize()` 装配服务与 pipeline。

> 说明：上游 `config_manager` 是 **导入期创建的全局单例**。`configure_runtime()`
> 必须在导入 `service.py` **之前** 注入配置，`factory.py` 已保证此顺序，调用方无需关心。

### EngineConfig 字段（`config.py`）

| 分类 | 字段 | 默认 | 说明 |
|------|------|------|------|
| LLM（必填） | `llm_api_key` / `llm_base_url` / `llm_model` | — | OpenAI 兼容接口 |
| 直连 Provider | `openai_*` / `dashscope_*` / `deepseek_*` / `gemini_*` / `ark_*` | 见 schema | 无 ComfyUI 时的图/视频/VLM |
| Kling | `kling_access_key` / `kling_secret_key` / `kling_base_url` | — | AK/SK 鉴权 |
| 代理 | `provider_local_proxy` | `""` | 部分 provider 走本地代理 |
| ComfyUI/RunningHub | `comfyui_url` / `comfyui_api_key` / `runninghub_api_key` / `runninghub_concurrent_limit` / `runninghub_instance_type` | 见 schema | 媒体生成后端 |
| 工作流 | `image_workflow` / `video_workflow` / `tts_workflow` | `None` | 默认工作流文件名 |
| TTS | `tts_inference_mode`(`local`) / `tts_voice` / `tts_speed` | edge-tts | 本地语音默认 |
| 模板 | `default_template` | `1080x1920/static_default.html` | `static_*` 无需 ComfyUI |
| 运行根 | `runtime_root` | `None`→`runtime/` | 资源根目录 |

> 多租户暂不接入（M2）：每个 `EngineConfig` 为一次构建期输入；接口形态已便于后续叠加
> 租户级密钥解析而无需改调用点。

---

## 3. 四条 Pipeline 契约

统一通过 `engine.generate_video(text=..., pipeline=<name>, **kwargs)` 调用
（`asset_based` 例外，见下）。返回 `VideoGenerationResult`：

```python
VideoGenerationResult(video_path: str, storyboard: Storyboard, duration: float, file_size: int, created_at: datetime)
```

模板类型由文件名前缀决定（`utils/template_util.get_template_type`）：
- `static_*`：纯文本帧，**不**生成媒体，无需 ComfyUI（最快、最省、验收默认）
- `image_*`：经 ComfyUI/直连 API 生成图片
- `video_*`：经 ComfyUI/直连 API 生成视频

### 3.1 `standard`（`pipelines/standard.py`）
通用「主题/固定脚本 → 短视频」。基于 `LinearVideoPipeline` 模板方法。

- **输入**：`text`（主题或脚本）
- **关键 kwargs**：`mode`(`generate`|`fixed`)、`n_scenes`(=5)、`title`、
  `frame_template`、`template_params`、`min/max_narration_words`、
  `min/max_image_prompt_words`、`prompt_prefix`、`video_fps`(=30)、
  `tts_inference_mode`/`tts_voice`/`tts_speed`/`voice_id`/`tts_workflow`/`ref_audio`、
  `media_width`/`media_height`/`media_workflow`/`api_video_params`、
  `content_metadata`、`bgm_path`/`bgm_volume`/`bgm_mode`、`output_path`、
  `progress_callback`
- **流程**：生成/拆分文案 → 生成标题 → (按模板)生成图片提示 → 建 storyboard →
  逐帧 TTS+媒体+合成 → 拼接(+BGM) → 持久化元数据
- **输出**：`VideoGenerationResult`

### 3.2 `LinearVideoPipeline`（`pipelines/linear.py`，基类）
线性视频生成的模板方法基类，定义 8 个生命周期步骤供子类覆写：
`setup_environment → generate_content → determine_title → plan_visuals →
initialize_storyboard → produce_assets → post_production → finalize`。
通过 `PipelineContext` 在步骤间传递状态。`standard` 即其子类。**不直接对外调用**。

### 3.3 `custom`（`pipelines/custom.py`）
扩展 `BasePipeline` 的参考/模板实现，演示如何自定义工作流（复制改造用）。

- **输入**：`text`
- **关键 kwargs**：`custom_param_example` + 与 standard 同类的标准参数
  （`tts_*`、`frame_template`、`video_fps`、`bgm_*`、`output_path`、`progress_callback`）
- **默认逻辑**：按行拆分 `text` 为 narrations，LLM 生成标题，按模板条件生成图片提示
- **输出**：`VideoGenerationResult`

### 3.4 `asset_based`（`pipelines/asset_based.py`）
「用户素材 → 视频」。**不经 `generate_video` 包装**，需直接调用 pipeline 实例：

```python
pipe = engine.pipelines["asset_based"]
ctx = await pipe(assets=[...], video_title="...", intent=None, duration=30,
                 source="runninghub", bgm_path=None, bgm_volume=0.2,
                 bgm_mode="loop", progress_callback=None)
# 返回 PipelineContext，final_video_path 在其上
```

- **输入**：`assets: List[str]`（图片/视频路径）、`video_title`、`intent`、
  `duration`(=30)、`source`(`runninghub`|`selfhost`)、`bgm_*`、`progress_callback`
- **流程**：分析素材建索引 → LLM 结构化输出生成脚本并把素材分配到场景 → 逐帧合成 → 拼接
- **输出**：`PipelineContext`（含 `final_video_path` / `task_id`）

---

## 4. 服务层契约（`pixelle_video/services/`）

由 `PixelleVideoCore.initialize()` 装配，pipeline 通过 `self.core.<svc>` 访问。

| 服务 | 调用 | 输入 → 输出 |
|------|------|-------------|
| `llm` (`LLMService`) | `await core.llm(prompt, model=None, temperature=0.7, max_tokens=2000, response_type=None)` | 文本 → `str`；传 `response_type`(Pydantic) → 结构化实例。OpenAI 兼容 SDK |
| `tts` (`TTSService`) | `await core.tts(text, voice=None, speed=None, inference_mode=None, workflow=None, ...)` | 文本 → 音频文件路径。默认本地 edge-tts |
| `media` (`MediaService`) | `await core.media(prompt, workflow=None, media_type="image", width, height, duration, ...)` | 提示 → `MediaResult`（经 ComfyKit 工作流） |
| `api_media` (`APIProviderMediaService`) | `await core.api_media(prompt, workflow, media_type="image", width, height, duration, image_path, ...)` | 提示 → `MediaResult`（直连各家 API） |
| `video` (`VideoService`) | `core.video.concat_videos(videos, output, method="demuxer", bgm_path, bgm_volume=0.2, bgm_mode="loop")` | 片段列表 → 合并 mp4 路径（ffmpeg） |
| `frame_processor` (`FrameProcessor`) | `await core.frame_processor(frame, storyboard, config, total_frames, progress_callback)` | 单帧 → 处理后 `StoryboardFrame`（TTS+媒体+合成+片段） |
| `persistence` (`PersistenceService`) | `await core.persistence.save_task_metadata(task_id, metadata)` / `save_storyboard(task_id, storyboard)` | 落盘任务元数据/storyboard 到 `output/` |
| `history` (`HistoryManager`) | 基于 persistence 的历史检索 | — |
| `image_analysis` / `video_analysis` / `api_asset_analysis` | 分析服务 | 素材/媒体 → 分析结构（供 asset_based 等） |

> 渲染依赖：`services/frame_html.py` 用 Playwright Chromium 把 HTML 模板渲染为帧图，
> 通过 `chromium.launch()` 启动。需先 `playwright install chromium`。

---

## 5. api_services 直连客户端契约（`services/api_services/`）

均为同步调用，封装各家 REST/SDK。下表列出主入口（详见各文件）：

### LLM / 文本
| Client | 主入口 | 输入 → 输出 |
|--------|--------|-------------|
| `LLM`（`llm_client.py`，路由器） | `query(prompt, image_urls=[], model="qwen3.6-max-preview", safe_content=True, task_id=None, web_search=False)` | 按 model 名路由到下方 client → 文本 |
| `GPT`（`llm_gpt.py`） | `query(prompt, image_urls=[], model="", web_search=False)` | OpenAI 系 → 文本 |
| `Gemini`（`llm_gemini.py`） | `query(prompt, image_urls=[], model="gemini-2.5-flash")` | Google → 文本 |
| `DeepSeek`（`llm_deepseek.py`） | `query(prompt, image_urls=[], model="deepseek-chat", web_search=False)` | DeepSeek → 文本 |
| `QwenLLM`（`llm_dashscope.py`） | `query(prompt, image_urls=None, model="qwen3.5-vl", web_search=False)` | DashScope Generation → 文本 |

### VLM（图文多模态）
| Client | 主入口 |
|--------|--------|
| `VLM`（`vlm_client.py`，路由器） | `query(...)` |
| `QwenVLClient`（`vlm_dashscope.py`） | `chat(text, images: List[str], model, stream=False, parameters=None, **kwargs)` |
| `GeminiVLClient`（`vlm_gemini.py`） | `chat(text, images, model="gemini-2.5-flash-image", ...)` |
| `GPTVLClient`（`vlm_gpt.py`） | `chat(text, images, model="gpt-5.4", ...)` |

### 图片生成
| Client | 主入口 | 输出 |
|--------|--------|------|
| `ImageClient`（`image_client.py`，路由器） | `generate_image(...)` | 本地图片路径 |
| `ImageGPT`（`image_gpt.py`） | `generate_image(prompt, size="1024x1024", quality="high", model="gpt-image-2", ...)` / `generate_images(...)` | 图片路径 |
| `DashScopeClient`（`image_dashscope.py`） | `generate_image(prompt, model="wan2.7-image", size="1024*1024", n=1, ...)` / `edit_image(prompt, image_urls, ...)` | 图片路径 |
| `SeedreamClient`（`image_seedream.py`） | `generate_image(...)` | 图片路径 |
| `ImageProcessor`（`image_processor.py`） | 下载/缩放/拼接/去黑边/OSS 上传等工具 | 处理后文件/URL |

### 视频生成
| Client | 主入口 | 输出 |
|--------|--------|------|
| `VideoClient`（`video_client.py`，路由器） | `generate_video(...)`（内部分发 wan/kling/seedance） | 本地视频路径 |
| `KlingVideoClient`（`video_kling.py`） | `generate_video(...)`（AK/SK 鉴权，提交+轮询+下载） | 视频路径 |
| `SeedanceVideoClient`（`video_seedance.py`） | `generate_video(...)`（提交+轮询+下载） | 视频路径 |
| `DashscopeVideoClient`（`video_dashscope.py`） | `generate_video(...)`（文生/图生/参考生/视频编辑） | 视频路径 |

> 密钥来源：上述 client 经 `services/api_services/config.py` 的 `Config` 读取，
> 其值由 `EngineConfig.to_pixelle_config()` 注入的 `api_providers.*` 提供。

---

## 6. 验收

静态模板链路（无需 ComfyUI）：LLM(文案) + edge-tts(本地语音) + Playwright(帧渲染) + ffmpeg(拼接)。

```bash
cd backend
set HUADING_LLM_API_KEY=sk-...
set HUADING_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
set HUADING_LLM_MODEL=qwen-max
python scripts/run_standard_pipeline.py
```

前置：`ffmpeg` 在 PATH；已执行 `playwright install chromium`。
脚本断言生成的 `result.video_path` 存在且非空。

---

## 7. 合规说明

- 本目录保留上游 `LICENSE`（Apache-2.0）与 `NOTICE`。
- 华鼎新增文件（`__init__.py` / `config.py` / `factory.py`）与修改文件
  （`pixelle_video/config/manager.py` 新增 `configure_from_config()`）均在文件头部
  按 Apache License §4(b) 标注「本文件系对 Pixelle-Video 的修改/衍生」声明。
- 未改动的上游源文件保留其原始版权头（`Copyright (C) 2025 AIDC-AI`）。
- 已剥离 Streamlit/Web 层，依赖中明确不含 `streamlit`。
