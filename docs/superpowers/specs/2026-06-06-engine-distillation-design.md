# 引擎导入与蒸馏设计（任务包 #002）

> 版本 v1.0 ｜ 日期 2026-06-06 ｜ 阶段 M1 / EN-1 ｜ 分支 feature/engine
> 目标：把 Pixelle-Video 生成内核引入 `backend/`，剥离 Streamlit/Web 展示层与单机专用逻辑，
> 改造为"平台密钥托管 + 运行参数入参"的接口形态，并保留版权合规标注。

## 1. 背景

源项目 **Pixelle-Video**（AIDC-AI，Apache-2.0）是 `FastAPI + Streamlit + ComfyUI/直连 API` 的单机短视频生成工具。
华鼎平台只需要其**生成内核**，不需要展示层与单机 YAML 配置。

源码位置：`C:\Users\Administrator\Documents\Codex\2026-06-05\github-pixelle-video-aidc-ai-pixelle\Pixelle-Video`

### 源码结构盘点

| 目录 | 内容 | 处置 |
|---|---|---|
| `pixelle_video/config/` | Pydantic schema + YAML loader + 单例 manager | 导入，改造注入接口 |
| `pixelle_video/models/` | Storyboard / Progress / Media 数据模型 | 原样导入 |
| `pixelle_video/pipelines/` | base / linear / standard / custom / asset_based | 原样导入 |
| `pixelle_video/services/` | LLM / TTS / Media / FrameProcessor / Persistence 等 | 原样导入 |
| `pixelle_video/services/api_services/` | OpenAI / DashScope / Kling / Seedream / Seedance 等直连客户端 | 原样导入 |
| `pixelle_video/prompts/` | 各类 LLM prompt 模块 | 原样导入 |
| `pixelle_video/utils/` | os_util / template_util / content_generators 等 | 原样导入 |
| `pixelle_video/service.py` | `PixelleVideoCore` 全局单例 | 导入，改造初始化 |
| `pixelle_video/llm_presets.py`, `tts_voices.py` | 预设常量 | 原样导入 |
| `templates/` | HTML 帧模板（1080x1920 等三种比例） | 导入到 runtime 资源目录 |
| `bgm/` | 背景音乐 | 导入到 runtime 资源目录 |
| `workflows/` | ComfyUI/RunningHub 工作流 JSON | 导入到 runtime 资源目录 |
| `web/` | Streamlit UI（2 个 page） | **剥离，不导入** |
| `api/` | FastAPI routers（content/tasks/tts/video…） | **本任务不导入**（留给任务包 #003 FastAPI 骨架） |

## 2. 关键技术约束（源码探查结论）

1. **配置是全局单例**：`pixelle_video/config/__init__.py` 在 import 时即创建
   `config_manager = ConfigManager()`，并被 `service.py`、`standard.py` 等多处直接 `from pixelle_video.config import config_manager` 引用。
   → 配置注入**不能只改 `service.py` 构造函数**，必须提供让外部覆盖该单例的入口。

2. **路径依赖 `PIXELLE_VIDEO_ROOT` 环境变量**：`utils/os_util.py` 用该环境变量定位项目根，
   并在 `<root>/templates`、`<root>/bgm`、`<root>/workflows`、`<root>/output` 下解析资源与写产物。
   → templates/bgm/workflows 必须放在同一个 root 目录下，初始化时设置该环境变量。

3. **硬依赖第三方库**：`service.py` 顶层 `from comfykit import ComfyKit`；
   TTS 默认走本地 `edge-tts`；模板渲染走 `playwright`；视频合成走 `ffmpeg-python` + `moviepy`。
   → 需要把这些依赖（剔除 `streamlit`）加入 `backend/pyproject.toml`。

## 3. 目标结构

```
backend/app/engine/
├── __init__.py              # 暴露 create_engine() / EngineConfig
├── config.py                # EngineConfig（平台注入的运行时配置，不读文件）
├── factory.py               # create_engine(cfg) → 已初始化的 PixelleVideoCore
├── NOTICE                   # 复制自源项目（第三方组件版权）
├── LICENSE                  # 复制自源项目（Apache-2.0 全文）
├── pixelle_video/           # 源 pixelle_video/ 包（逐字复制，最小改动）
│   ├── config/
│   │   ├── schema.py        # 不改
│   │   ├── loader.py        # 不改（保留 YAML 回退能力）
│   │   ├── manager.py       # +configure_from_config() 注入方法
│   │   └── __init__.py      # 不改
│   ├── models/ pipelines/ prompts/ services/ utils/   # 全部不改
│   ├── service.py           # __init__ 支持 engine_config 注入
│   ├── llm_presets.py / tts_voices.py                  # 不改
│   └── __init__.py          # 不改
└── runtime/                 # PIXELLE_VIDEO_ROOT 指向此目录
    ├── templates/           # 复制自源 templates/
    ├── bgm/                 # 复制自源 bgm/
    ├── workflows/           # 复制自源 workflows/
    └── output/              # 运行产物（gitignore）

backend/scripts/
└── run_standard_pipeline.py # 验收脚本：脱离 Streamlit 调用 standard pipeline

docs/engine/
└── ENGINE.md                # 交付物：四套 pipeline 与各 api_services 输入输出契约
```

## 4. 配置改造：EngineConfig

新增 `backend/app/engine/config.py`，作为平台密钥与运行参数的**唯一注入点**：

```python
class EngineConfig(BaseModel):
    # LLM（必填）
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    # 直连 API 供应商（按需）
    dashscope_api_key: str = ""
    openai_api_key: str = ""
    ark_api_key: str = ""
    kling_access_key: str = ""
    kling_secret_key: str = ""
    # ComfyUI / RunningHub（可选）
    comfyui_url: str = "http://127.0.0.1:8188"
    comfyui_api_key: str = ""
    runninghub_api_key: str = ""
    runninghub_concurrent_limit: int = 1
    # 模板与产物
    default_template: str = "1080x1920/static_simple.html"
    runtime_root: str | None = None   # 默认指向 engine/runtime
    def to_pixelle_config(self) -> PixelleVideoConfig: ...
```

注入流程（`factory.create_engine`）：
1. 解析 `runtime_root`（默认 `engine/runtime`），设置 `os.environ["PIXELLE_VIDEO_ROOT"]`。
2. 调用 `config_manager.configure_from_config(cfg.to_pixelle_config())` 覆盖全局单例。
3. `core = PixelleVideoCore()`；`await core.initialize()`；返回 `core`。

**多租户不在本任务范围**：`EngineConfig` 是单次构建的入参，每次调用 `create_engine` 即一套配置；
多租户隔离（按租户切换密钥/单例）留待 M2。

## 5. 最小改动清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `config/manager.py` | +`configure_from_config(cfg)` | 用传入的 `PixelleVideoConfig` 替换单例 `self.config` |
| `service.py` | `__init__(engine_config=None)` | 有则用之，否则回退原 `config_manager`（向后兼容） |
| 其余 `pixelle_video/**` | **零改动** | 逐字复制，降低引入 bug 风险；保留 Apache 头注释 |

新增文件（`config.py` / `factory.py` / `__init__.py` / 脚本）头部加"衍生自 Pixelle-Video (AIDC-AI, Apache-2.0)"声明。

## 6. 依赖变更

`backend/pyproject.toml` 增加引擎运行依赖（**剔除 streamlit**）：
`edge-tts`、`ffmpeg-python`、`moviepy`、`pillow`、`openai`、`comfykit`、`beautifulsoup4`、
`playwright`、`dashscope`、`pyjwt`、`numpy`、`loguru`、`pyyaml`、`requests`。

## 7. 验收路径

验收标准：脱离 Streamlit，用脚本调用一次 **standard pipeline** 成功生成产物。

最小可行链路（**静态模板，免 ComfyUI**）：
- 模板选 `static_*.html` → `plan_visuals` 检测为 static，跳过图像/视频生成；
- 只需 LLM（文案）+ edge-tts（本地配音）+ playwright（渲染帧）+ ffmpeg（合成）；
- 脚本 `scripts/run_standard_pipeline.py` 用真实 LLM key 跑 `n_scenes=3`，断言产物 mp4 存在且 size > 0。

## 8. 交付物

1. `backend/app/engine/`：可被调用的引擎模块（`create_engine` + `EngineConfig`）。
2. `docs/engine/ENGINE.md`：四套 pipeline（standard/linear/custom/asset_based）与各
   `api_services` 客户端的输入输出契约说明。
3. `backend/scripts/run_standard_pipeline.py`：验收脚本。
4. 版权合规：`engine/LICENSE`、`engine/NOTICE` + 各源文件保留原 Apache 头。

## 9. 风险与遗留

- **依赖体积大**：comfykit/moviepy/playwright 等较重；playwright 需 `playwright install chromium`。
- **验收需真实 LLM key**：本地需有可用 LLM 配置才能端到端验证。
- **api/ 未导入**：FastAPI 路由层留给 #003；本任务只交付可被 import 的引擎核。
- **config 单例**：当前为进程级单例，多租户场景需在 M2 重构为按请求注入。
