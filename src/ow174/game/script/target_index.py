"""C76D60/C76980 existing-member lookup and C75B20 removal ordering.

Explicit native index storage and tree callbacks are required. Missing upserts
need the separate insertion path; they are not silently treated as handled.
"""

from dataclasses import dataclass

from .target_scan import _mask

U32 = 0xFFFFFFFF


def _u32(value):
    if type(value) is not int or not 0 <= value <= U32:
        raise ValueError("member index key must be uint32")
    return value


def _i32(value):
    if type(value) is not int or not -(1 << 31) <= value < 1 << 31:
        raise ValueError("member index link/value must be int32")
    return value


def hash_key(entity):
    value = (_u32(entity) * 0x1001 + 0x7ED55D16) & U32
    value = ((value >> 19) ^ value ^ 0xC761C23C) & U32
    mixed = (value * 0x21 + 0x165667B1) & U32
    value = ((mixed - 0x2C5D9B94) ^ (mixed << 9)) & U32
    value = (value * 9 - 0x028FB93B) & U32
    value = (value >> 16) ^ 0x355A4F09 ^ value
    return value & 0x7FFFFFFF


def bucket(entity, count):
    if type(count) is not int or not 0 < count <= U32 or count & (count - 1):
        raise ValueError("member index bucket count must be a positive power of two")
    return hash_key(entity) & (count - 1)


@dataclass(frozen=True)
class Entry:
    key: int
    next: int
    cached_hash: object = NotImplemented  # only insertion/erasure writes this field


@dataclass
class ValueCell:
    value: int  # retains the native selected storage location across an erase callback


@dataclass
class Index:
    buckets: object = NotImplemented  # None is a known absent table
    entries: tuple = ()
    values: tuple = ()
    used_count: object = NotImplemented
    free_head: object = NotImplemented
    free_count: object = NotImplemented

    def find(self, entity):
        index, _previous, _slot = self._locate(entity)
        return None if index is None else self._cell(index)

    def _cell(self, index):
        if index >= len(self.values) or not isinstance(self.values[index], ValueCell):
            raise ValueError("matched member index value is unavailable")
        return self.values[index]

    def _locate(self, entity):
        entity = _u32(entity)
        if self.buckets is None:
            return None, None, None
        if not isinstance(self.buckets, tuple):
            raise ValueError("member index buckets are unavailable")
        slot = bucket(entity, len(self.buckets))
        index = self.buckets[slot]
        seen, previous = set(), None
        while _i32(index) >= 0:
            if index in seen or index >= len(self.entries):
                raise ValueError("invalid member index collision chain")
            seen.add(index)
            entry = self.entries[index]
            if not isinstance(entry, Entry):
                raise ValueError("member index entry is unavailable")
            if _u32(entry.key) == entity:
                return index, previous, slot
            previous = index
            index = entry.next
        return None, previous, slot

    def _reserve(self, callback, needed):
        if not callable(callback):
            raise ValueError("member index reservation service is unavailable")
        callback(self, needed)
        if not isinstance(self.buckets, tuple):
            raise ValueError("member index reservation did not provide buckets")
        bucket(0, len(self.buckets))

    def ensure(self, entity, *, reserve=NotImplemented):
        """C72B30 returns (value cell, already present); new values remain untouched."""
        entity = _u32(entity)
        if self.buckets is None:
            self._reserve(reserve, 3)
        found, _previous, slot = self._locate(entity)
        if found is not None:
            return self._cell(found), True
        free_count = _i32(self.free_count)
        if free_count < 0:
            raise ValueError("member free count is invalid")
        if free_count:
            index = _i32(self.free_head)
            if not 0 <= index < len(self.entries) or not isinstance(self.entries[index], Entry):
                raise ValueError("member free list is invalid")
            self.free_head = _i32(self.entries[index].next)
            self.free_count = free_count - 1
        else:
            used = _i32(self.used_count)
            if used < 0:
                raise ValueError("member used count is invalid")
            if used == len(self.buckets):
                self._reserve(reserve, used + 1)
                slot = bucket(entity, len(self.buckets))
                used = _i32(self.used_count)
            index = used
            self.used_count = used + 1
        if index >= len(self.entries):
            raise ValueError("member entry backing is unavailable")
        cell = self._cell(index)
        entries, buckets = list(self.entries), list(self.buckets)
        entries[index] = Entry(entity, _i32(buckets[slot]), hash_key(entity))
        buckets[slot] = index
        self.entries, self.buckets = tuple(entries), tuple(buckets)
        return cell, False

    def erase(self, entity):
        """C73780 unlinks first match and pushes its slot to free list; values do not change."""
        index, previous, slot = self._locate(entity)
        if index is None:
            return False
        head, count = _i32(self.free_head), _i32(self.free_count)
        if count < 0:
            raise ValueError("member free count is invalid")
        entries, buckets = list(self.entries), list(self.buckets)
        successor = _i32(entries[index].next)
        if previous is None:
            buckets[slot] = successor
        else:
            old = entries[previous]
            entries[previous] = Entry(old.key, successor, old.cached_hash)
        entries[index] = Entry(0, head, U32)
        self.entries, self.buckets = tuple(entries), tuple(buckets)
        self.free_head, self.free_count = index, count + 1
        return True


def update_existing(index, entity, bits, bounds, *, write_mask, update_node):
    cell = index.find(entity)
    if cell is None:
        return False  # caller must run insertion; this is not a completed upsert
    node = _i32(cell.value)
    write_mask(node, _mask(bits))
    update_node(node, bounds, 0.0)
    return True


def remove(index, entity, *, remove_key, remove_node):
    cell = index.find(entity)
    if cell is None:
        return False
    remove_key(index, entity)
    # Original C75B20 preserves the value address, then reads it AFTER key removal.
    remove_node(_i32(cell.value))
    return True
