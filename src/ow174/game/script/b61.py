"""104319 B61 payload and priority order for an explicitly supplied registry."""

import math
from dataclasses import dataclass

from ow174.game.script import expr


@dataclass(frozen=True)
class Payload:
    enabled: bool = False
    first: float = 0.0
    second: float = 0.0
    extras: tuple[float, ...] = (0.0, 0.0)


@dataclass
class Entry:
    token: object
    priority: float
    serial: int
    payload: Payload


class Registry:
    """BC48D0 insertion and BC4DB0 removal with a finite-priority host."""

    def __init__(self, descriptors=(), *, serial=0):
        if type(serial) is not int or not 0 <= serial <= 0xFFFFFFFF:
            raise ValueError("B61 registry serial must be uint32")
        self.serial = serial
        self.descriptors = dict(descriptors)
        self.entries = []

    def priority(self, value):
        if value is None or isinstance(value, expr.Asset) and value.guid == 0:
            return 0.0, False
        if isinstance(value, expr.Asset):
            return self.descriptors.get(value.guid, NotImplemented)
        return NotImplemented

    def insert(self, token, payload, priority, forward):
        priority = expr.f32(priority)
        if not math.isfinite(priority) or type(forward) is not bool:
            raise ValueError("B61 registry requires finite priority and a serial direction")
        self.serial = (self.serial + 1) & 0xFFFFFFFF
        serial = expr.i32(self.serial if forward else -self.serial)
        key = priority, serial
        index = next((i for i, entry in enumerate(self.entries)
                      if key >= (entry.priority, entry.serial)), len(self.entries))
        self.entries.insert(index, Entry(token, priority, serial, payload))

    def find(self, token):
        return next((entry for entry in self.entries if entry.token is token), None)

    def update(self, token, payload):
        entry = self.find(token)
        if entry is not None:
            entry.payload = payload

    def remove(self, token):
        for index, entry in enumerate(self.entries):
            if entry.token is token:
                self.entries.pop(index)
                return

    def clear(self):
        self.entries.clear()
