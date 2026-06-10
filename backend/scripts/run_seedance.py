# Copyright (C) 2026 Huading
#
# Verification script for Doubao-Seedance (Ark) text-to-video / image-to-video.
# Derived-work usage of Pixelle-Video (AIDC-AI, Apache-2.0).

"""
Run a real Seedance generation with your own Ark key.

Keys come from the environment (platform key-hosting style):

    set SEEDANCE_API_KEY=...                                  # required
    set SEEDANCE_BASE_URL=https://ark.cn-beijing.volces.com/api/v3   # optional
    set SEEDANCE_MODEL=doubao-seedance-2-0-260128            # optional

Text-to-video:
    python scripts/run_seedance.py --mode t2v --prompt "一只柯基在草地奔跑，电影质感"

Image-to-video (local file or http(s) URL; omit --image to use a generated sample):
    python scripts/run_seedance.py --mode i2v --image ./product.jpg --prompt "镜头缓慢推近，柔光"
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Put backend/ and backend/app/engine on sys.path so `app` and the vendored
# `pixelle_video` package resolve when this runs as a standalone script. Must
# happen before importing the client below.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ENGINE_DIR = _BACKEND_DIR / "app" / "engine"
for _p in (str(_BACKEND_DIR), str(_ENGINE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pixelle_video.services.api_services.video_seedance import (  # noqa: E402
    SeedanceVideoClient,
)


def _make_sample_image(path: str) -> str:
    """Generate a simple sample product image so i2v runs without external files."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (768, 768), (236, 217, 170))
    draw = ImageDraw.Draw(img)
    draw.rectangle([158, 158, 610, 610], fill=(200, 165, 99))
    draw.ellipse([300, 300, 468, 468], fill=(110, 82, 31))
    draw.text((300, 640), "SAMPLE", fill=(58, 51, 32))
    img.save(path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Seedance (Ark) t2v / i2v verification")
    parser.add_argument("--mode", choices=["t2v", "i2v"], required=True)
    parser.add_argument("--prompt", default="一只柯基犬在草地上奔跑，阳光明媚，电影质感")
    parser.add_argument("--image", default=None, help="i2v: local image path or http(s) URL")
    parser.add_argument("--out", default=None, help="output mp4 path")
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--ratio", default="16:9")
    args = parser.parse_args()

    if not (os.environ.get("SEEDANCE_API_KEY") or os.environ.get("ARK_API_KEY")):
        print("ERROR: set SEEDANCE_API_KEY (or ARK_API_KEY)", file=sys.stderr)
        return 2

    out = args.out or f"seedance_{args.mode}_output.mp4"
    image = image_path = None
    if args.mode == "i2v":
        src = args.image
        if not src:
            src = _make_sample_image("seedance_sample_input.png")
            print(f"[i2v] no --image given, generated sample: {src}")
        if src.startswith(("http://", "https://", "data:")):
            image = src
        else:
            image_path = src

    client = SeedanceVideoClient()  # reads SEEDANCE_* / ARK_* env
    print(f"[seedance] mode={args.mode} model={client.model} base={client.base_url}")
    started = time.time()
    try:
        result = client.generate_video(
            args.prompt,
            image=image,
            image_path=image_path,
            save_path=out,
            duration=args.duration,
            resolution=args.resolution,
            ratio=args.ratio,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    size = os.path.getsize(out) if os.path.exists(out) else 0
    print(f"OK: task={result.task_id}")
    print(f"    url={result.video_url}")
    print(f"    saved {out} ({size} bytes) in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
