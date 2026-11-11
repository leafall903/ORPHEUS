"""C76980 second-root member wrapper with explicit backing, tree and reserve services."""

from dataclasses import dataclass

from .target_index import _i32, update_existing
from .target_scan import _mask


@dataclass
class Buffer:
    data: list  # explicitly declared physical backing; never silently extended by a store
    count: int
    capacity: int
    grow: object = NotImplemented  # callback(buffer, needed)->bool; may update backing/capacity

    def resize_count(self, needed):
        count, capacity = _i32(self.count), _i32(self.capacity)
        if count < 0 or capacity < 0:
            raise ValueError("member buffer header is invalid")
        if needed > count and needed > capacity:
            if not callable(self.grow):
                raise ValueError("member buffer growth is unavailable")
            succeeded = self.grow(self, needed)
            if type(succeeded) is not bool:
                raise ValueError("member buffer growth result is unavailable")
            if not succeeded:
                return  # native leaves logical count unchanged, then still performs the store
        self.count = needed

    def store(self, index, value):
        if not 0 <= index < len(self.data):
            raise ValueError("member write exceeds declared physical backing")
        self.data[index] = value


def upsert_second(index, entity, bits, bounds, payload24, *, payloads, masks,
                  insert_node, update_node, reserve_index=NotImplemented):
    """Update an existing node, else allocate and bind record/mask/ID without guessing them."""
    bits = _mask(bits)
    if payload24 is NotImplemented:
        raise ValueError("component24 record binding is unavailable")
    if update_existing(index, entity, bits, bounds, write_mask=masks.store, update_node=update_node):
        return
    if payload24 is None or not callable(insert_node):
        raise ValueError("new second-root member requires its component24 record and tree allocator")
    node = _i32(insert_node(bounds, 0, 0.0))
    if node < 0:
        raise ValueError("tree allocation returned an unsupported invalid node")
    if node >= _i32(payloads.count):
        payloads.resize_count(node + 1)
        masks.resize_count(node + 1)
    payloads.store(node, payload24)
    masks.store(node, bits)
    cell, _already_present = index.ensure(entity, reserve=reserve_index)
    cell.value = node
