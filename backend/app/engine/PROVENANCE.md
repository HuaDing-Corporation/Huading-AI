# Engine provenance & vendoring (compliance)

This documents the origin and license compliance of the vendored video engine,
using a **source-exception list** rather than adding a copyright header to every
upstream file. This keeps the vendored tree byte-for-byte close to upstream
(minimal drift, easy re-sync) while remaining auditable.

## Vendored tree

The entire **`backend/app/engine/pixelle_video/`** tree, plus the runtime
resources under **`backend/app/engine/runtime/`** (`templates/`, `bgm/`,
`workflows/`), is **vendored from [AIDC-AI/Pixelle-Video](https://github.com/AIDC-AI/Pixelle-Video),
licensed under Apache-2.0**.

The upstream license and attribution notices are preserved verbatim:

- `backend/app/engine/LICENSE` — Apache License 2.0
- `backend/app/engine/NOTICE` — upstream `Copyright (C) 2025 AIDC-AI` + third-party notices

Per Apache-2.0 §4(c), the upstream copyright/attribution is retained at the
package (tree) level here. Some individual upstream source files do **not** carry
a per-file copyright header; they are unmodified upstream works and are covered
by this tree-level provenance together with the preserved `LICENSE`/`NOTICE`. We
intentionally do **not** add headers to unmodified upstream files, to avoid
diverging from upstream and to keep the "what did Huading change" set small and
obvious (see below).

## Huading-modified upstream files (Apache-2.0 §4(b))

These vendored files were changed by Huading and each carries a prominent
"Modifications … Huading" notice in its header stating what changed:

| File | Change |
|------|--------|
| `pixelle_video/config/manager.py` | Added `configure_from_config()` for platform runtime key/config injection |
| `pixelle_video/services/frame_html.py` | Support a system browser channel (`HUADING_BROWSER_CHANNEL`) with fallback to bundled Chromium |
| `pixelle_video/services/api_services/image_processor.py` | Removed a hard-coded DashScope API key default; key injected at runtime / from env |
| `pixelle_video/services/api_services/image_client.py` | Pass the injected DashScope key + DashScope-specific proxy into `ImageProcessor` |

To re-verify this list:

```bash
grep -rl "Huading" backend/app/engine/pixelle_video
```

## Huading-authored new files (not derived from a single upstream file)

These are original Huading code (interface/adapter layer), Apache-2.0, each with
a Huading copyright header:

- `backend/app/engine/__init__.py`
- `backend/app/engine/config.py` — `EngineConfig` (platform key hosting + run params)
- `backend/app/engine/factory.py` — `create_engine()` / `configure_runtime()`

## Streamlit/web layer

The upstream Streamlit/web display layer was **not** vendored (stripped during
distillation); `streamlit` is not a dependency.
