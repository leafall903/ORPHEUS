"""Compare owned upright placement axes with104319 native evaluator instructions.

Unicorn executes quaternion/basis arithmetic and three original config evaluators.
Entity lookup, nested config conversion and engine transform/trigonometric services are adapters;
this does not validate native ownership, arbitrary transforms or placement casts.
"""

import argparse
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_64, Uc
from unicorn.x86_const import (
    UC_X86_REG_R8,
    UC_X86_REG_R9,
    UC_X86_REG_RAX,
    UC_X86_REG_RBP,
    UC_X86_REG_RCX,
    UC_X86_REG_RDX,
    UC_X86_REG_RIP,
    UC_X86_REG_RSI,
    UC_X86_REG_RSP,
    UC_X86_REG_XMM0,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ow174.game import symmetra  # noqa: E402
from ow174.game.script import expr  # noqa: E402

STACK, DATA, END = 0x200000, 0x300000, 0x600000
BODY = 0xA0001234
QUAT, FRAME, SCRATCH = DATA + 0x20000, DATA + 0x21000, DATA + 0x22000
EVALUATORS = {
    "STU_66F3B89E": (0x2663690, 0xCE8460),
    "STU_021D01CD": (0x26612F0, 0xCCDD80),
    "STU_2200A66D": (0x2660B48, 0xCC94D0),
}
POSE, VIEW = DATA + 0x23000, DATA + 0x24000


def compare(image, base):
    for vt, callback in EVALUATORS.values():
        if struct.unpack_from("<Q", image, vt + 0x20)[0] != base + callback:
            raise ValueError("not the expected104319 evaluator vtable")
    uc = Uc(UC_ARCH_X86, UC_MODE_64)
    uc.mem_map(base, (len(image) + 4095) & ~4095)
    uc.mem_write(base, image)
    uc.mem_map(STACK, 0x100000)
    uc.mem_map(DATA, 0x40000)
    uc.mem_map(END, 0x1000)
    ctx, vt, resource, nested, evaluator, evt = (DATA + i * 0x1000 for i in range(6))
    world_pointer, instance, out, entity_pointer = (DATA + i * 0x1000 for i in range(6, 10))
    uc.mem_write(ctx, struct.pack("<Q", vt))
    uc.mem_write(vt + 0x30, struct.pack("<Q", END + 0x100))
    uc.mem_write(resource + 0x10, struct.pack("<Q", nested))
    uc.mem_write(nested + 8, struct.pack("<Q", evaluator))
    uc.mem_write(evaluator, struct.pack("<Q", evt))
    uc.mem_write(evt + 0x20, struct.pack("<Q", END + 0x200))
    uc.mem_write(entity_pointer, struct.pack("<I", BODY))
    observed, lookups = [], []

    def return_from_call(machine, value=0):
        stack = machine.reg_read(UC_X86_REG_RSP)
        target = struct.unpack("<Q", machine.mem_read(stack, 8))[0]
        machine.reg_write(UC_X86_REG_RSP, stack + 8)
        machine.reg_write(UC_X86_REG_RAX, value)
        machine.reg_write(UC_X86_REG_RIP, target)

    def hook(machine, address, size, user):
        if address == END:
            machine.emu_stop()
        elif address == END + 0x100:
            return_from_call(machine, world_pointer)
        elif address == END + 0x200:
            return_from_call(machine, 1)
        elif address == base + 0xF64C20:
            return_from_call(machine, entity_pointer)
        elif address in (base + 0xF63740, base + 0x1B7FD20):
            return_from_call(machine)
        elif address == base + 0xD1B5E0:
            lookups.append(machine.reg_read(UC_X86_REG_RDX) & 0xFFFFFFFF)
            destination = machine.reg_read(UC_X86_REG_R8)
            machine.mem_write(destination, bytes(machine.mem_read(FRAME, 64)))
            return_from_call(machine, 1)
        elif address == base + 0xD18780:
            lookups.append(machine.reg_read(UC_X86_REG_RDX) & 0xFFFFFFFF)
            machine.mem_write(machine.reg_read(UC_X86_REG_R8), bytes(machine.mem_read(VIEW, 16)))
            return_from_call(machine, 1)
        elif address == base + 0xC62760:
            destination = machine.reg_read(UC_X86_REG_RDX)
            machine.mem_write(destination, bytes(machine.mem_read(QUAT, 16)))
            return_from_call(machine, destination)
        elif address in (base + 0x2500E50, base + 0x2501350):
            bits = machine.reg_read(UC_X86_REG_XMM0).to_bytes(16, "little")
            angle = struct.unpack("<f", bits[:4])[0]
            value = math.cos(angle) if address == base + 0x2500E50 else math.sin(angle)
            machine.reg_write(UC_X86_REG_XMM0, int.from_bytes(struct.pack("<f", value), "little"))
            return_from_call(machine)
        elif address == base + 0xF67550:
            point = machine.reg_read(UC_X86_REG_RDX)
            observed.append(struct.unpack("<3f", machine.mem_read(point, 12)))
            return_from_call(machine)

    uc.hook_add(UC_HOOK_CODE, hook)
    failures, count = [], 0
    yaws = sorted({*range(-32768, 32768, 997), -32768, -16384, -8192, 0, 8192, 16384, 32767})
    owner = SimpleNamespace(
        body=BODY, gone=False, has_body=True, spawned=True, mover=SimpleNamespace(yaw=0, pitch=0)
    )
    world = SimpleNamespace(
        hero=symmetra.HERO,
        body=BODY,
        match=SimpleNamespace(players=[owner], combat=SimpleNamespace(is_dead=lambda p: False)),
    )
    model_instance = SimpleNamespace(
        graph=SimpleNamespace(index=0x156B),
        evaluate=lambda cfg: expr.Entity(BODY) if cfg == {"$": "STU_B5A0CAF0"} else None,
    )
    for yaw in yaws:
        angle = yaw * math.tau / 65536
        uc.mem_write(QUAT, struct.pack("<4f", 0, math.sin(angle / 2), 0, math.cos(angle / 2)))
        stack = STACK + 0xFFF08
        uc.mem_write(stack, struct.pack("<Q", END))
        uc.reg_write(UC_X86_REG_RSP, stack)
        uc.reg_write(UC_X86_REG_RCX, QUAT)
        uc.reg_write(UC_X86_REG_RDX, SCRATCH - 0x50)
        uc.reg_write(UC_X86_REG_R8, SCRATCH - 0x40)
        uc.emu_start(base + 0x184BC70, END, count=100)
        uc.mem_write(SCRATCH - 0x30, struct.pack("<4f", 0, 0, 0, 1))
        uc.reg_write(UC_X86_REG_RBP, SCRATCH)
        uc.reg_write(UC_X86_REG_RSI, FRAME)
        # Exact D1B5E0 block builds rows0/1/2/3 from the native quaternion axes.
        uc.emu_start(base + 0xD1B6A0, base + 0xD1B6E4, count=100)
        owner.mover.yaw = yaw
        cases = [(cls, callback, 0) for cls, (_, callback) in EVALUATORS.items() if cls != "STU_2200A66D"]
        cases += [("STU_2200A66D", EVALUATORS["STU_2200A66D"][1], p) for p in (-16200, -8000, 0, 8000, 16200)]
        for cls, callback, pitch in cases:
            owner.mover.pitch = pitch
            if cls == "STU_2200A66D":
                uc.mem_write(POSE + 0x42, struct.pack("<h", pitch))
                uc.mem_write(stack, struct.pack("<Q", END))
                uc.reg_write(UC_X86_REG_RSP, stack)
                uc.reg_write(UC_X86_REG_RCX, POSE)
                uc.reg_write(UC_X86_REG_RDX, VIEW)
                uc.emu_start(base + 0xC5E040, END, count=1000)
            observed.clear()
            lookups.clear()
            uc.mem_write(stack, struct.pack("<Q", END))
            uc.mem_write(stack + 0x28, struct.pack("<Q", out))
            uc.reg_write(UC_X86_REG_RSP, stack)
            uc.reg_write(UC_X86_REG_RCX, 0)
            uc.reg_write(UC_X86_REG_RDX, ctx)
            uc.reg_write(UC_X86_REG_R8, instance)
            uc.reg_write(UC_X86_REG_R9, resource)
            uc.emu_start(base + callback, END, count=1000)
            actual = symmetra.placement_direction_query(
                world, {"$": cls, "m_entity": {"$": "STU_B5A0CAF0"}}, model_instance
            )
            expected = observed[0] if len(observed) == 1 else ()
            values = (actual.x, actual.y, actual.z)
            count += 1
            if (
                len(expected) != 3
                or lookups != [BODY]
                or not uc.reg_read(UC_X86_REG_RAX) & 1
                or any(abs(a - b) > 2e-6 for a, b in zip(values, expected, strict=True))
            ):
                failures.append(
                    {
                        "yaw": yaw,
                        "pitch": pitch,
                        "cls": cls,
                        "native": expected,
                        "model": values,
                        "lookups": lookups,
                    }
                )
    return {
        "cases": count,
        "failures": failures,
        "evaluator_rvas": {k: f"{v[1]:X}" for k, v in EVALUATORS.items()},
        "quaternion_helper_rva": "184BC70",
        "basis_block_rvas": ["D1B6A0", "D1B6E4"],
        "absolute_tolerance": 2e-6,
        "scope": "upright yaw/pitch reconstruction, native basis selection and original C5E040 rotation",
        "view_helper_rva": "C5E040",
        "adapters": [
            "entity/config lookup",
            "engine transform lookup",
            "typed variant output",
            "sinf/cosf ABI",
        ],
        "native_parent_transforms_and_placement_casts_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--base", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT) or args.output.resolve() == args.image.resolve():
        parser.error("write reports outside source and the immutable input")
    raw = args.image.read_bytes()
    result = compare(raw, args.base)
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": result["cases"], "failures": result["failures"]}))
    return bool(result["failures"])


if __name__ == "__main__":
    raise SystemExit(main())
