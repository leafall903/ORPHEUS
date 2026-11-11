"""Compare104319 placement begin/+70 callbacks with the production State.

Stable clock/frame inputs are explicit. World/resource lookup and replay/cast
backends are adapters; native geometry and prediction admission are not accepted.
"""

import argparse
import hashlib
import itertools
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_64, Uc
from unicorn.x86_const import (
    UC_X86_REG_R8,
    UC_X86_REG_RAX,
    UC_X86_REG_RCX,
    UC_X86_REG_RDX,
    UC_X86_REG_RIP,
    UC_X86_REG_RSP,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ow174.game.script import placement  # noqa: E402
from ow174.game.script.nodes import Placement  # noqa: E402

BEGIN, TICK, VTABLE = 0xC83C30, 0xC83C50, 0x261CE58
STACK, STATE, CONTEXT, FRAME, WORLD, RESOURCE, END = (
    0x200000,
    0x300000,
    0x310000,
    0x320000,
    0x330000,
    0x340000,
    0x600000,
)


class Adapter:
    def __init__(self):
        self.context = None
        self.replay = False
        self.calls = []

    def placement_clock(self, state):
        return self.context

    def replay_placement(self, state, frame):
        self.calls.append(("replay", frame))
        return self.replay

    def calculate_placement(self, state, *, commit):
        self.calls.append(("calculate", commit))
        return True


class Model:
    _unknown = Placement._unknown

    def __init__(self, world):
        self.instance = SimpleNamespace(component=SimpleNamespace(world=world, strict=True))
        self.node = SimpleNamespace(cls="STU_4C96470A")
        self.last_frame = self.latest_frame = 0xFFFFFFFF
        self.registrations = 0

    def ticks(self):
        self.registrations += 1


def compare(image, base):
    for offset, callback in ((0x70, TICK), (0xB8, BEGIN), (0x90, 0x1CE5640), (0xE0, 0x1CE5640)):
        if struct.unpack_from("<Q", image, VTABLE + offset)[0] != base + callback:
            raise ValueError("not the expected104319 placement server vtable")
    uc = Uc(UC_ARCH_X86, UC_MODE_64)
    uc.mem_map(base, (len(image) + 4095) & ~4095)
    uc.mem_write(base, image)
    uc.mem_map(STACK, 0x100000)
    uc.mem_map(STATE, 0x50000)
    uc.mem_map(END, 0x1000)
    uc.mem_write(STATE + 0x78, struct.pack("<Q", CONTEXT))
    uc.mem_write(CONTEXT + 0x10, struct.pack("<Q", FRAME))
    observed = []
    replay = False

    def returned(machine, value):
        stack = machine.reg_read(UC_X86_REG_RSP)
        address = struct.unpack("<Q", machine.mem_read(stack, 8))[0]
        machine.reg_write(UC_X86_REG_RSP, stack + 8)
        machine.reg_write(UC_X86_REG_RAX, value)
        machine.reg_write(UC_X86_REG_RIP, address)

    def hook(machine, address, size, user):
        if address == END:
            machine.emu_stop()
        elif address == base + 0x1B71C40:
            returned(machine, WORLD)
        elif address == base + 0x1B71C90:
            returned(machine, RESOURCE)
        elif address == base + 0xC98E90:
            if machine.reg_read(UC_X86_REG_RDX) != RESOURCE:
                raise ValueError("unexpected native replay resource")
            observed.append(("replay", machine.reg_read(UC_X86_REG_R8) & 0xFFFFFFFF))
            returned(machine, int(replay))
        elif address == base + 0xCB6BA0:
            if machine.reg_read(UC_X86_REG_RDX) != RESOURCE:
                raise ValueError("unexpected native calculation resource")
            observed.append(("calculate", bool(machine.reg_read(UC_X86_REG_R8) & 255)))
            returned(machine, 0)

    uc.hook_add(UC_HOOK_CODE, hook)
    adapter = Adapter()
    model = Model(adapter)
    failures, count = [], 0

    def run(callback):
        stack = STACK + 0xFFF08
        uc.mem_write(stack, struct.pack("<Q", END))
        uc.reg_write(UC_X86_REG_RSP, stack)
        uc.reg_write(UC_X86_REG_RCX, STATE)
        uc.emu_start(base + callback, END, count=256)

    for latest in (0, 17, 0xFFFFFFFF):
        uc.mem_write(STATE + 0x2C0, struct.pack("<II", 12, latest))
        run(BEGIN)
        model.last_frame, model.latest_frame = 12, latest
        Placement.on_begin(model)
        count += 1
        if (model.last_frame, model.latest_frame) != struct.unpack("<II", uc.mem_read(STATE + 0x2C0, 8)):
            failures.append({"begin_latest": latest})
    for last, latest, frame, interval, clock, replay in itertools.product(
        (0xFFFFFFFF, 0, 10),
        (0xFFFFFFFF, 0, 9, 20),
        (0, 5, 0x7FFFFFFF, 0xFFFFFFFF),
        (1, 2, 3, 16),
        (-(1 << 63), -16, -1, 0, 1, 2, 15, 16, (1 << 63) - 1),
        (False, True),
    ):
        observed.clear()
        uc.mem_write(STATE + 0x2C0, struct.pack("<II", last, latest))
        uc.mem_write(FRAME + 0x1D0, struct.pack("<I", frame))
        uc.mem_write(FRAME + 0x1E0, struct.pack("<q", clock))
        uc.mem_write(WORLD + 0x430, struct.pack("<q", interval))
        run(TICK)
        expected = struct.unpack("<II", uc.mem_read(STATE + 0x2C0, 8))
        adapter.context = placement.Clock(frame, clock, interval)
        adapter.replay = replay
        adapter.calls.clear()
        model.last_frame, model.latest_frame = last, latest
        Placement.tick(model)
        actual = model.last_frame, model.latest_frame
        count += 1
        if actual != expected or adapter.calls != observed:
            failures.append(
                {
                    "input": [last, latest, frame, interval, clock, replay],
                    "native_fields": expected,
                    "model_fields": actual,
                    "native_calls": list(observed),
                    "model_calls": list(adapter.calls),
                }
            )
    return {
        "cases": count,
        "failures": failures,
        "begin_rva": f"{BEGIN:X}",
        "tick_rva": f"{TICK:X}",
        "frame_getter_rva": "1B71860",
        "clock_getter_rva": "1B92FE0",
        "scope": "begin reset, stable-clock tick gating, unsigned high-water and backend dispatch",
        "adapters": ["world/resource lookup", "history replay", "geometry calculation"],
        "native_placement_geometry_or_prediction_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--base", type=lambda v: int(v, 0), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT) or args.output.resolve() == args.image.resolve():
        parser.error("write report outside source and the immutable input")
    image = args.image.read_bytes()
    result = compare(image, args.base)
    result["input_sha256"] = hashlib.sha256(image).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": result["cases"], "failures": result["failures"][:5]}))
    return bool(result["failures"])


if __name__ == "__main__":
    raise SystemExit(main())
