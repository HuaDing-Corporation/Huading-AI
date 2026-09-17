"""Explicit consent handshake for pre-existing avatar workflow regression tests."""

import subprocess


def write_decodable_audio(path, seconds=1):
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            str(seconds),
            "-c:a",
            "libmp3lame",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def accept_policy(payload, estimate):
    offer = estimate.get("avatar_duration_policy")
    if offer is None:
        return payload
    return {
        **payload,
        "avatar_duration_policy": offer["version"],
        "avatar_duration_policy_token": offer["token"],
    }


def post_avatar_with_policy(client, url, *, json, headers):
    assert url == "/api/v1/videos"
    estimate = client.post("/api/v1/videos/estimate", json=json, headers=headers)
    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["data"]["pricing_contract"] == "legacy_estimate"
    return client.post(url, headers=headers, json=accept_policy(json, estimate.json()["data"]))
