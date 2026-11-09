"""Transient input context for posed combat commands."""

from contextlib import contextmanager
from contextvars import ContextVar

active_pose = ContextVar("combat_command_pose", default=None)


@contextmanager
def command_pose(world, player, command):
    token = active_pose.set((world, player, command) if command is not None else None)
    try:
        yield
    finally:
        active_pose.reset(token)
