"""Compare placement CAB450/C9FCE0 history storage with original instructions.

Role admission is an adapter, capacity is supplied, debug entity checks are off.
This does not validate placement geometry, client prediction or callback transport.
"""

import argparse
import hashlib
import json
import random
import struct
import sys
from pathlib import Path

from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_64, Uc
from unicorn.x86_const import (
    UC_X86_REG_R8,
    UC_X86_REG_R9,
    UC_X86_REG_RAX,
    UC_X86_REG_RCX,
    UC_X86_REG_RDX,
    UC_X86_REG_RIP,
    UC_X86_REG_RSP,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ow174.game.script import expr, placement  # noqa: E402

STACK, STATE, CONTEXT, VTABLE, ROWS, ENTITY, IDENTIFIER, POINT, END = (
    0x200000, 0x300000, 0x310000, 0x320000, 0x330000, 0x340000, 0x341000, 0x342000, 0x600000,
)
RECORD, PRUNE, ROLE = 0xCAB450, 0xC9FCE0, END + 0x100
ROW = struct.Struct("<IIQ4fB15x")


def pack(value):
    return ROW.pack(value.frame, value.entity.id, value.identifier.guid, *value.point, 0, value.valid)


def compare(image, base):
    machine = Uc(UC_ARCH_X86, UC_MODE_64)
    machine.mem_map(base, (len(image) + 4095) & ~4095)
    machine.mem_write(base, image)
    machine.mem_map(STACK, 0x100000)
    machine.mem_map(STATE, 0x50000)
    machine.mem_map(END, 0x1000)
    machine.mem_write(STATE + 0x78, struct.pack("<Q", CONTEXT))
    machine.mem_write(CONTEXT, struct.pack("<Q", VTABLE))
    machine.mem_write(VTABLE + 0x148, struct.pack("<Q", ROLE))
    machine.mem_write(base + 0x2E2EF10, b"\0")  # Native debug entity validation switch.
    enabled = False
    count, failures = 0, []

    def hook(uc, address, size, user):
        if address == END:
            uc.emu_stop()
        elif address == ROLE:
            stack = uc.reg_read(UC_X86_REG_RSP)
            target = struct.unpack("<Q", uc.mem_read(stack, 8))[0]
            uc.reg_write(UC_X86_REG_RSP, stack + 8)
            uc.reg_write(UC_X86_REG_RAX, int(enabled))
            uc.reg_write(UC_X86_REG_RIP, target)
        elif address == base + 0xCA2DB0:
            raise RuntimeError("unexpected allocation despite supplied capacity")

    machine.hook_add(UC_HOOK_CODE, hook)

    def install(values):
        machine.mem_write(STATE + 0x1F0, struct.pack("<QII", ROWS, len(values), 256))
        machine.mem_write(ROWS, b"".join(pack(value) for value in values) or bytes(ROW.size))

    def actual():
        length = struct.unpack("<I", machine.mem_read(STATE + 0x1F8, 4))[0]
        result = []
        for index in range(length):
            frame, entity, identifier, x, y, z, _w, valid = ROW.unpack(
                machine.mem_read(ROWS + index * ROW.size, ROW.size)
            )
            result.append(placement.Record(frame, expr.Entity(entity), expr.Asset(identifier),
                                           expr.Vec3(x, y, z), bool(valid)))
        return result

    def run(callback, frame):
        stack = STACK + 0xFFF08
        machine.mem_write(stack, struct.pack("<Q", END))
        machine.reg_write(UC_X86_REG_RSP, stack)
        machine.reg_write(UC_X86_REG_RCX, STATE)
        machine.reg_write(UC_X86_REG_RDX, frame)
        machine.emu_start(base + callback, END, count=10000)
        if machine.reg_read(UC_X86_REG_RIP) != END:
            raise RuntimeError("native history did not return within instruction budget")

    rng = random.Random(104319)

    def value(frame):
        return placement.Record(frame, expr.Entity(rng.randrange(0x100000000)),
                                expr.Asset(rng.randrange(1 << 64)),
                                expr.Vec3(*(rng.randrange(-10000, 10001) / 7 for _ in range(3))),
                                bool(rng.randrange(2)))

    for trial in range(512):
        values = [value(rng.choice((0, 1, 59, 60, 61, 100, 200, 0xFFFFFFFF)))
                  for _ in range(rng.randrange(9))]
        install(values)
        for before in (0, 1, 59, 60, 61, 100, 201, 0xFFFFFFFF):
            expected = list(values)
            install(values)
            placement.prune(expected, before)
            run(PRUNE, before)
            count += 1
            if actual() != expected:
                failures.append({"kind": "prune", "trial": trial, "cutoff": before})
        install(values)
        for index in range(8):
            enabled = bool(index % 2)
            row = value(rng.choice((0, 1, 59, 60, 61, 100, 200, 0xFFFFFFFE, 0xFFFFFFFF)))
            machine.mem_write(ENTITY, struct.pack("<I", row.entity.id))
            machine.mem_write(IDENTIFIER, struct.pack("<Q", row.identifier.guid))
            machine.mem_write(POINT, struct.pack("<4f", *row.point, 0))
            stack = STACK + 0xFFF08
            machine.mem_write(stack + 0x28, struct.pack("<Q", POINT))
            machine.mem_write(stack + 0x30, bytes((row.valid,)))
            machine.reg_write(UC_X86_REG_R8, ENTITY)
            machine.reg_write(UC_X86_REG_R9, IDENTIFIER)
            placement.remember(values, row, enabled=enabled)
            run(RECORD, row.frame)
            count += 1
            if actual() != values:
                failures.append({"kind": "remember", "trial": trial, "index": index})
    return {
        "cases": count, "failures": failures,
        "record_rva": f"{RECORD:X}", "prune_rva": f"{PRUNE:X}",
        "scope": (
            "unsigned cutoff, reverse swap-removal, frame-60 retention, "
            "first-frame-wins append and typed row values"
        ),
        "adapters": ["explicit component role", "preallocated capacity", "debug entity checks disabled"],
        "native_prediction_geometry_or_transport_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--base", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT) or args.output.resolve() == args.image.resolve():
        parser.error("write report outside source and immutable input")
    image = args.image.read_bytes()
    result = compare(image, args.base)
    result["input_sha256"] = hashlib.sha256(image).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": result["cases"], "failures": result["failures"][:5]}))
    return bool(result["failures"])


if __name__ == "__main__":
    raise SystemExit(main())
