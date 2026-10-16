"""输出校验：时长误差 < 1 秒，有视频流和音频流，编码正确。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..ffrun import probe


@dataclass
class VerifyResult:
    ok: bool
    problems: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)


def check(info: dict, expected_duration: float, tolerance: float = 1.0,
          expect_size: tuple[int, int] | None = None) -> VerifyResult:
    problems = []
    if not info.get("has_video"):
        problems.append("没有视频流")
    if not info.get("has_audio"):
        problems.append("没有音频流")
    dur = float(info.get("duration") or 0)
    if abs(dur - expected_duration) >= tolerance:
        problems.append(f"时长 {dur:.2f} 秒与预期 {expected_duration:.2f} 秒相差过大")
    if info.get("has_video") and info.get("vcodec") not in ("h264",):
        problems.append(f"视频编码为 {info.get('vcodec')}，不是 H.264")
    if info.get("has_audio") and info.get("acodec") not in ("aac",):
        problems.append(f"音频编码为 {info.get('acodec')}，不是 AAC")
    if expect_size and info.get("has_video") and (info.get("width"), info.get("height")) != expect_size:
        problems.append(f"分辨率 {info.get('width')}x{info.get('height')} 与预期 "
                        f"{expect_size[0]}x{expect_size[1]} 不符")
    return VerifyResult(not problems, problems, info)


def verify_output(path: str | Path, expected_duration: float, tolerance: float = 1.0,
                  expect_size: tuple[int, int] | None = None) -> VerifyResult:
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return VerifyResult(False, ["输出文件不存在或为空"])
    try:
        info = probe(p)
    except Exception as e:
        return VerifyResult(False, [f"无法读取输出文件：{e}"])
    return check(info, expected_duration, tolerance, expect_size)
