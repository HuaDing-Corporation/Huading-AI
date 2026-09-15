import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.workers.avatar_talk import _video_duration_sec
from app.workers.heygen_avatar import _align_source


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="requires local ffmpeg")
@pytest.mark.parametrize("duration", [1.5, 8.0])
def test_alignment_trims_or_loops_three_second_video_without_paid_generation(tmp_path, duration):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x96:rate=24",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    ctx = SimpleNamespace(task_id="alignment-task", duration_sec=duration, work_dir=tmp_path)
    data = _align_source(ctx, source.read_bytes())
    assert len(data) > 100
    assert abs(_video_duration_sec(tmp_path / "heygen-aligned.mp4") - duration) < 0.1
