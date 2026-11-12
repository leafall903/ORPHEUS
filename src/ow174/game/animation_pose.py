"""Decode and sample the sparse local bone tracks in 104319 .006 clips."""

import json
import math
import struct
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache

from ow174.game.props import (
    BoneTransform,
    ModelPose,
    actor_model_shapes,
    model_hardpoints,
    model_reference_bones,
)

_TRACK_HEADER = struct.Struct("<4H6i")
_CHANNEL_FORMATS = (struct.Struct("<3H"), struct.Struct("<3f"), struct.Struct("<3H"))


def _unpack_value(channel: int, raw: tuple) -> tuple:
    if channel == 0:
        return tuple(1.0 + (1.0 - axis / 1024.0) / 1024.0 for axis in raw)
    if channel == 1:
        return raw
    a, b, c = raw
    missing = (a >> 15) | ((b >> 15) << 1)
    values = [
        1.41421 * ((a & 0x7FFF) - 0x4000) / 0x8000,
        1.41421 * ((b & 0x7FFF) - 0x4000) / 0x8000,
        1.41421 * (c - 0x8000) / 0x10000,
    ]
    values.insert(missing, math.sqrt(max(0.0, 1.0 - sum(axis * axis for axis in values))))
    return tuple(values)


def _slerp(first: tuple, second: tuple, fraction: float) -> tuple:
    cosine = sum(a * b for a, b in zip(first, second, strict=True))
    if cosine < 0:
        second = tuple(-axis for axis in second)
        cosine = -cosine
    if cosine > 0.9995:
        value = tuple(a + (b - a) * fraction for a, b in zip(first, second, strict=True))
        length = math.hypot(*value)
        return tuple(axis / length for axis in value)
    angle = math.acos(cosine)
    divisor = math.sin(angle)
    left = math.sin((1.0 - fraction) * angle) / divisor
    right = math.sin(fraction * angle) / divisor
    return tuple(left * a + right * b for a, b in zip(first, second, strict=True))


@dataclass(frozen=True)
class _Track:
    frames: tuple[int, ...]
    values: tuple[tuple, ...]

    def sample(self, frame: float, fallback: tuple, *, rotation: bool = False) -> tuple:
        if not self.frames:
            return fallback
        after = bisect_right(self.frames, frame)
        if after == 0:
            return self.values[0]
        before = after - 1
        if after == len(self.frames) or self.frames[before] == frame:
            return self.values[before]
        fraction = (frame - self.frames[before]) / (self.frames[after] - self.frames[before])
        first, second = self.values[before], self.values[after]
        if rotation:
            return _slerp(first, second, fraction)
        return tuple(a + (b - a) * fraction for a, b in zip(first, second, strict=True))


def _read_track(data: bytes, row: int, channel: int, count: int, indices: int,
                values: int, frame_count: int) -> _Track:
    if not count:
        return _Track((), ())
    index_format = "B" if frame_count <= 255 else "h"
    index_size = 1 if index_format == "B" else 2
    indices = row + 4 * indices
    values = row + 4 * values
    fmt = _CHANNEL_FORMATS[channel]
    if not (0 <= indices <= len(data) - index_size * count and 0 <= values <= len(data) - fmt.size * count):
        raise ValueError("animation track exceeds clip bounds")
    keys = struct.unpack_from(f"<{count}{index_format}", data, indices)
    by_frame = {
        abs(key) % frame_count: _unpack_value(channel, fmt.unpack_from(data, values + index * fmt.size))
        for index, key in enumerate(keys)
    }
    if any(not math.isfinite(axis) for value in by_frame.values() for axis in value):
        raise ValueError("nonfinite animation track value")
    frames = tuple(sorted(by_frame))
    return _Track(frames, tuple(by_frame[frame] for frame in frames))


@dataclass(frozen=True)
class AnimationClip:
    duration: float
    fps: float
    tracks: dict[int, tuple[_Track, _Track, _Track]]

    @classmethod
    def read(cls, data: bytes) -> "AnimationClip":
        if len(data) < 80:
            raise ValueError("truncated animation header")
        count, = struct.unpack_from("<H", data, 2)
        duration, _, fps = struct.unpack_from("<3f", data, 16)
        if not (math.isfinite(duration) and duration >= 0 and math.isfinite(fps) and fps > 0):
            raise ValueError("invalid animation timing")
        # Frame count uses the product of the two single-precision header fields.
        frame_count = int(struct.unpack("<f", struct.pack("<f", duration * fps))[0]) + 1
        ids, _, _, table = struct.unpack_from("<4q", data, 48)
        if not (0 <= ids <= len(data) - 4 * count and 0 <= table <= len(data) - _TRACK_HEADER.size * count):
            raise ValueError("animation bone table exceeds clip bounds")
        bones = struct.unpack_from(f"<{count}I", data, ids)
        tracks = {}
        for index, bone in enumerate(bones):
            row = table + index * _TRACK_HEADER.size
            *counts, _flags, si, pi, ri, sv, pv, rv = _TRACK_HEADER.unpack_from(data, row)
            if bone in tracks:
                raise ValueError("duplicate animation bone")
            tracks[bone] = tuple(
                _read_track(data, row, channel, number, indices, values, frame_count)
                for channel, (number, indices, values) in enumerate(
                    zip(counts, (si, pi, ri), (sv, pv, rv), strict=True)
                )
            )
        return cls(duration, fps, tracks)

    def sample_absolute(
        self, seconds: float, reference: dict[int, tuple[int | None, BoneTransform]]
    ) -> dict[int, BoneTransform]:
        """Sample an absolute clip at local time, keeping reference values for unkeyed channels."""
        if not math.isfinite(seconds):
            raise ValueError("animation sample time must be finite")
        frame = min(self.duration, max(0.0, seconds)) * self.fps
        sampled = {}
        for bone in self.tracks.keys() & reference.keys():
            scale, position, rotation = self.tracks[bone]
            _, base = reference[bone]
            sampled[bone] = BoneTransform(
                rotation.sample(frame, base.rotation, rotation=True),
                scale.sample(frame, base.scale),
                position.sample(frame, base.translation),
            )
        return sampled


@lru_cache(maxsize=4)
def wheel_primary_clips(path):
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {
        (int(blend, 16), int(alias, 16), int(category, 16)): int(clip, 16)
        for blend, aliases in rows.items()
        for alias, categories in aliases.items()
        for category, clip in categories.items()
        if isinstance(clip, str) and int(clip, 16) >> 48 == 0x0A00
    }


@lru_cache(maxsize=64)
def _wheel_clip(path):
    try:
        return AnimationClip.read(path.read_bytes())
    except FileNotFoundError:
        return None


@lru_cache(maxsize=64)
def _wheel_model(path):
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    return model_hardpoints(data), model_reference_bones(data), tuple(actor_model_shapes(data))


def wheel_clip_pose(
    directory, body: int, clip: int, seconds: float, looping: bool, transform: tuple[float, ...]
) -> ModelPose | None:
    model = _wheel_model(directory / "models" / f"{body & 0xFFFFFFFFFFFF:012X}.00C")
    if model is None:
        return None
    animation = _wheel_clip(directory / "clips" / f"{clip & 0xFFFFFFFFFFFF:012X}.006")
    if animation is None:
        return None
    hardpoints, reference, shapes = model
    if looping and animation.duration > 0:
        seconds %= animation.duration
    sampled = animation.sample_absolute(seconds, reference)
    if not sampled:
        return None
    return ModelPose.from_local_bones(hardpoints, reference, transform, sampled, shapes)
