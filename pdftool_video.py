"""
pdftool_video.py — video/audio helpers used by app.py

Provides:
  - VideoToolError
  - extract_audio(in_path, out_path, bitrate="192k")
  - download_video(url, job_dir, fmt="mp4", quality="best")
"""

import os
import shutil
import subprocess
from pathlib import Path

class VideoToolError(Exception):
    """Raised for expected/user-facing video/audio processing failures."""
    pass


def _ffmpeg_bin():
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VideoToolError(
            "ffmpeg is not installed on the server. "
            "Make sure ffmpeg is available in the deploy environment (e.g. nixpacks.toml)."
        )
    return ffmpeg


def extract_audio(video_path: str, mp3_output_path: str, bitrate: str | None = None, **kwargs) -> str:
    """Stream audio directly to MP3 via ffmpeg without decoding video frames."""
    video_path = str(video_path)
    mp3_output_path = str(mp3_output_path)

    if not os.path.exists(video_path):
        raise VideoToolError("Input video file not found.")

    Path(mp3_output_path).parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

    cmd = [
        ffmpeg,
        "-y",
        "-i", video_path,
        "-vn",                   # Skip video decoding for massive speed/RAM savings
        "-acodec", "libmp3lame",
        "-q:a", "4",             # High-speed VBR encoding
        mp3_output_path,
    ]

    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        raise VideoToolError("Audio extraction timed out.")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        stderr = getattr(e, "stderr", None)
        err_msg = stderr.decode(errors="ignore")[-2000:] if stderr else str(e)
        raise VideoToolError(f"ffmpeg failed to extract audio: {err_msg}")

    return mp3_output_path
import pdftool

def video_to_images(in_path: str, out_dir: str, fmt: str = "png", quality: int = 85,
                    max_frames: int | None = None, fps: int | None = None, max_dimension: int | None = None) -> list[str]:
    """Extract video frames to images using pdftool.video_to_frames.

    Wraps any OSError from ffmpeg/I/O with a user‑friendly VideoToolError.
    Returns list of image file paths.
    """
    try:
        return pdftool.video_to_frames(
            str(in_path), str(out_dir), fmt=fmt, quality=quality,
            max_frames=max_frames, fps=fps, max_dimension=max_dimension
        )
    except OSError as e:
        raise VideoToolError(f"Failed to process video due to I/O error: {e}")
    except Exception as e:
        # Re‑raise known VideoToolError or wrap unexpected errors
        if isinstance(e, VideoToolError):
            raise
        raise VideoToolError(str(e))
