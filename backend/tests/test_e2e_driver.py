from scripts.e2e_distributed_driver import _file_url_to_path


def test_decodes_non_ascii_percent_encoding() -> None:
    # 华鼎 -> %E5%8D%8E%E9%BC%8E must be restored.
    url = "file:///home/user/%E5%8D%8E%E9%BC%8E/videos/final.mp4"
    assert _file_url_to_path(url, is_windows=False) == "/home/user/华鼎/videos/final.mp4"


def test_windows_drive_letter() -> None:
    url = "file:///C:/Users/Administrator/%E5%8D%8E%E9%BC%8E/final.mp4"
    assert _file_url_to_path(url, is_windows=True) == "C:/Users/Administrator/华鼎/final.mp4"


def test_windows_unc_host_share() -> None:
    url = "file://server/share/videos/final.mp4"
    assert _file_url_to_path(url, is_windows=True) == r"\\server\share\videos\final.mp4"


def test_posix_plain_path() -> None:
    url = "file:///var/data/videos/final.mp4"
    assert _file_url_to_path(url, is_windows=False) == "/var/data/videos/final.mp4"
