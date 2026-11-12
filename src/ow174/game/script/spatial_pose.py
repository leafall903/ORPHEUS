"""Native spatial arithmetic plus an explicit reconstruction of Match model poses.

C5E040 rotates a pitch direction by the state quaternion. BBF060 adds the
stance/scale/spring offset along the mover's up vector. Entity/model hierarchy
selection remains the caller's responsibility.
"""

import math

from ow174.game import contacts
from ow174.game.script.expr import Vec3, f32

PITCH_RADIANS = f32(9.58738019107841e-5)


def yaw_rotation(yaw):
    """The quaternion sent for an unparented Match body, rounded as wire f32s."""
    half = yaw * math.pi / 65536
    return (0.0, f32(math.sin(half)), 0.0, f32(math.cos(half)))


def look_direction(pitch, rotation):
    """C5E040's s16 pitch and quaternion arithmetic; no additional pitch clamp."""
    pitch = (pitch + 0x8000) % 0x10000 - 0x8000
    angle = -f32(pitch * PITCH_RADIANS)
    vector = (0.0, f32(math.sin(angle)), f32(math.cos(angle)))
    return Vec3(*contacts.rotate(rotation, vector))


def mover_origin(position, up, pogo, scale, spring):
    """C371D0's mover-state branch: C62A50 position plus BBF060 offset."""
    height = f32(f32(pogo * contacts.minps(scale, 1.0)) + spring)
    return Vec3(*contacts.add(position, contacts.scale(up, height)))


def match_vector(cls, position, yaw, pitch, pogo, spring=0.0):
    """Unparented, unit-scale Match body policy, not a native hierarchy resolver.

    Match stores feet and yaw; its model root is reconstructed at those feet.
    The mover's state supplies the eye/cast origin offset and aim pitch. Bots
    without a Mover use their standing authored pogo and zero pitch/spring.
    """
    rotation = yaw_rotation(yaw)
    if cls == "STU_8CBDD81C":
        return mover_origin(position, (0.0, 1.0, 0.0), pogo, 1.0, spring)
    if cls == "STU_0BA27A07":
        return Vec3(*(f32(axis) for axis in position))
    if cls in ("STU_2EDEE064", "STU_2200A66D"):
        return look_direction(pitch, rotation)
    if cls == "STU_021D01CD":
        return Vec3(*contacts.rotate(rotation, (0.0, 0.0, 1.0)))
    return NotImplemented
