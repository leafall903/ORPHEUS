"""Execute native CB6BA0 result/commit processing with declared geometry.

Run from the original function entry. Entity lookup storage is supplied, config
uses the native default, geometry/history/typed writes/output services are adapters.
Cases have no hit entity, so entity-relative result transforms are not accepted.
"""

import argparse
import hashlib
import itertools
import json
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

STACK, STATE, RESOURCE, CONTEXT, FRAME, MANAGER, BUCKETS, ROWS, OWNER, END = (
    0x200000, 0x300000, 0x310000, 0x320000, 0x330000, 0x340000,
    0x350000, 0x370000, 0x380000, 0x600000,
)
OUTPUTS = ((0x1C8, "entity"), (0x1A8, "point"), (0x1B8, "valid"))
VALUE_BITS, DIRTY_BITS, PLUG_VTABLE, PLUG = 0x390000, 0x391000, 0x392000, 0x393000
TYPE_CHECK, ABORT_CHECK = END + 0x100, END + 0x110
CONTEXT_VTABLE, REGISTER, UNREGISTER = 0x394000, END + 0x120, END + 0x130


class ModelConfirmation(placement.Confirmation):
    def clear(self):
        self.events.append(["clear_value_bits", 1])
        super().clear()

    def mark(self):
        self.events.append(["property_dirty"])
        super().mark()

    def enter(self, register):
        self.events.append(["scope_begin"])
        super().enter(register)

    def leave(self, unregister):
        self.events.append(["scope_end"])
        super().leave(unregister)


class ModelResults:
    def __init__(self, frame, mask):
        self.current_frame, self.mask, self.events = frame, mask, []

    def write(self, field, value):
        bit, name = {"m_2FECD1E3": (0, "entity"), "m_30710B76": (1, "point"),
                     "m_2339572B": (2, "valid")}[field]
        if self.mask & (1 << bit):
            value = value.id if isinstance(value, expr.Entity) else (
                list(value) if isinstance(value, expr.Vec3) else value)
            self.events.append(["write", name, value])

    def frame(self):
        self.events.append(["frame", self.current_frame])
        return self.current_frame

    def record(self, value):
        self.events.append(["history", value.frame, value.entity.id, value.identifier.guid,
                            list(value.point), value.valid])

    def follow(self, plug):
        success = plug == "m_F6955E81"
        self.events.append(["follow", 0x111111 if success else 0x222222,
                            0x188 if success else 0x190, True])


def compare(image, base):
    if struct.unpack_from("<I", image, 0x3241288)[0] != 0:
        raise ValueError("not the expected native null entity constant")
    if struct.unpack_from("<4f", image, 0x3241710) != (0, 0, 0, 0):
        raise ValueError("not the expected native failure point constant")
    uc = Uc(UC_ARCH_X86, UC_MODE_64)
    uc.mem_map(base, (len(image) + 4095) & ~4095)
    uc.mem_write(base, image)
    uc.mem_map(STACK, 0x100000)
    uc.mem_map(STATE, 0xA0000)
    uc.mem_map(END, 0x1000)
    uc.mem_write(STATE + 0x78, struct.pack("<Q", CONTEXT))
    uc.mem_write(CONTEXT + 0x10, struct.pack("<Q", FRAME))
    uc.mem_write(OWNER + 0xB0, struct.pack("<I", 0x123))
    # Native bucket decoding remains original instructions, with a declared key.
    uc.mem_write(base + 0x2EE53A8, bytes(8))
    uc.mem_write(MANAGER + 0x18, struct.pack("<Q", BUCKETS ^ 0x42B51EF9B84A901))
    uc.mem_write(base + 0x2E2EF10, bytes(1))
    uc.mem_write(ROWS, struct.pack("<IIQ", 0, 0, OWNER))
    # CC6EF0 normally resolves the native owner. Its result's +28 is encoded.
    uc.mem_write(CONTEXT + 0x28, struct.pack("<Q", MANAGER ^ 0x98DE97B50B3BE1E3))
    events, expected, failures = [], [], []
    point = (0.0, 0.0, 0.0)
    valid = False
    bindings = {}
    input_mode = False
    matched = False

    def qword(address):
        return struct.unpack("<Q", uc.mem_read(address, 8))[0]

    def word(address):
        return struct.unpack("<I", uc.mem_read(address, 4))[0]

    def vector(address):
        return list(struct.unpack("<3f", uc.mem_read(address, 12)))

    def returned(value=0):
        stack = uc.reg_read(UC_X86_REG_RSP)
        target = qword(stack)
        uc.reg_write(UC_X86_REG_RSP, stack + 8)
        uc.reg_write(UC_X86_REG_RAX, value)
        uc.reg_write(UC_X86_REG_RIP, target)

    def hook(machine, address, size, user):
        if address == END:
            machine.emu_stop()
            return
        offset = address - base
        if address in (TYPE_CHECK, ABORT_CHECK):
            descriptor = 0x2DD53C0 if address == TYPE_CHECK else 0x2DD53B0
            if machine.reg_read(UC_X86_REG_RDX) != base + descriptor:
                raise ValueError("unexpected native input type descriptor")
            returned(int(matched and address == TYPE_CHECK))
        elif input_mode and offset == 0x1B6F700:
            if (machine.reg_read(UC_X86_REG_RDX), machine.reg_read(UC_X86_REG_R8),
                    machine.reg_read(UC_X86_REG_R9)) != (STATE, STATE + 0x348, STATE + 0x2C8):
                raise ValueError("unexpected confirmation scope binding")
            events.append(["scope_begin"])
        elif input_mode and offset == 0x1B6F950:
            events.append(["scope_end"])
        elif address in (REGISTER, UNREGISTER):
            if (machine.reg_read(UC_X86_REG_RCX), machine.reg_read(UC_X86_REG_RDX),
                    machine.reg_read(UC_X86_REG_R8)) != (CONTEXT, STATE, STATE + 0x2C8):
                raise ValueError("unexpected property-scope owner registration")
            events.append(["register" if address == REGISTER else "unregister"])
            returned()
        elif input_mode and offset == 0x184FAD0:
            address = machine.reg_read(UC_X86_REG_RCX)
            count = machine.reg_read(UC_X86_REG_RDX)
            if address != VALUE_BITS or count != 1:
                raise ValueError("unexpected native property bitset clear")
            events.append(["clear_value_bits", count])
            machine.mem_write(address, bytes(count))
            returned(address)
        elif input_mode and offset == 0x1BEB2F0:
            if (machine.reg_read(UC_X86_REG_RCX), machine.reg_read(UC_X86_REG_RDX) & 255,
                    machine.reg_read(UC_X86_REG_R8)) != (STATE + 0x2C8, 1, 0):
                raise ValueError("unexpected confirmation property write")
            events.append(["property_dirty"])
            # Execute the original bitset/dirty-marker instructions, no callback.
        elif input_mode and offset == 0x1B71140:
            events.append(["request_begin"])
            returned()
        elif offset == 0xCC6EF0:
            returned(CONTEXT)
        elif offset == 0xC8EB30:
            if machine.reg_read(UC_X86_REG_RDX) != RESOURCE:
                raise ValueError("wrong native geometry resource")
            events.append(["geometry"])
            stack = machine.reg_read(UC_X86_REG_RSP)
            if valid:
                uc.mem_write(qword(stack + 0x28), struct.pack("<4f", *point, 0))
                uc.mem_write(qword(stack + 0x30), bytes(8))  # No dynamic hit entity.
            returned(int(valid))
        elif offset in (0x1B72D60, 0x1B72F30, 0x1B72D20):
            binding = machine.reg_read(UC_X86_REG_RDX)
            payload = machine.reg_read(UC_X86_REG_R8)
            if offset == 0x1B72D60:
                value = word(payload)
            elif offset == 0x1B72F30:
                value = vector(payload)
            else:
                value = bool(payload & 255)
            if binding:
                events.append(["write", bindings[binding], value])
            returned(int(bool(binding)))
        elif offset == 0xCAB450:
            stack = machine.reg_read(UC_X86_REG_RSP)
            events.append([
                "history", machine.reg_read(UC_X86_REG_RDX) & 0xFFFFFFFF,
                word(machine.reg_read(UC_X86_REG_R8)), qword(machine.reg_read(UC_X86_REG_R9)),
                vector(qword(stack + 0x28)), bool(machine.mem_read(stack + 0x30, 1)[0]),
            ])
            returned()
        elif offset == 0x1B71860:
            events.append(["frame", word(FRAME + 0x1D0)])
        elif offset == 0x21EB2E0:
            returned(CONTEXT)
        elif offset == 0x1B73FA0:
            stack = machine.reg_read(UC_X86_REG_RSP)
            events.append([
                "follow", machine.reg_read(UC_X86_REG_R9), qword(stack + 0x28),
                machine.reg_read(UC_X86_REG_R8) == RESOURCE,
            ])
            returned()

    uc.hook_add(UC_HOOK_CODE, hook)
    cases = 0
    for available, valid, commit, frame, point, mask in itertools.product(
        (False, True), (False, True), (False, True), (0, 60, 0xFFFFFFFF),
        ((0.0, 0.0, 0.0), (1.25, -7.5, 31.0)), range(8),
    ):
        uc.mem_write(BUCKETS, struct.pack("<QII", ROWS, int(available), 0))
        uc.mem_write(FRAME + 0x1D0, struct.pack("<I", frame))
        bindings.clear()
        for bit, (offset, name) in enumerate(OUTPUTS):
            pointer = RESOURCE + 0x800 + bit * 0x100 if mask & (1 << bit) else 0
            uc.mem_write(RESOURCE + offset, struct.pack("<Q", pointer))
            if pointer:
                bindings[pointer] = name
        # Distinct success/failure links prove the actual offset passed to output.
        uc.mem_write(RESOURCE + 0x188, struct.pack("<QQ", 0x111111, 0x222222))
        events.clear()
        model = ModelResults(frame, mask)
        if available:
            model.events.append(["geometry"])
            result = placement.StaticResult(valid, expr.Vec3(*point))
            placement.publish_static(result, commit=commit, write=model.write, frame=model.frame,
                                     record=model.record, follow=model.follow)
        expected = model.events
        stack = STACK + 0xFFF08
        uc.mem_write(stack, struct.pack("<Q", END))
        uc.reg_write(UC_X86_REG_RSP, stack)
        uc.reg_write(UC_X86_REG_RCX, STATE)
        uc.reg_write(UC_X86_REG_RDX, RESOURCE)
        uc.reg_write(UC_X86_REG_R8, int(commit))
        uc.emu_start(base + 0xCB6BA0, END, count=10000)
        if uc.reg_read(UC_X86_REG_RIP) != END:
            raise ValueError("native calculation exceeded instruction budget")
        cases += 1
        if events != expected:
            failures.append({"case": cases, "available": available, "valid": valid,
                             "commit": commit, "mask": mask, "actual": list(events), "expected": expected})
    input_mode = True
    uc.mem_write(PLUG, struct.pack("<Q", PLUG_VTABLE))
    uc.mem_write(PLUG_VTABLE + 0x10, struct.pack("<QQ", TYPE_CHECK, ABORT_CHECK))
    uc.mem_write(CONTEXT, struct.pack("<Q", CONTEXT_VTABLE))
    uc.mem_write(CONTEXT_VTABLE + 0x198, struct.pack("<QQ", REGISTER, UNREGISTER))
    input_cases, input_failures = 0, []
    for priority, matched, latched, active, value_bits, dirty_bits, depth, pending in itertools.product(
        (1, 2), (False, True), (False, True), (False, True),
        (0, 0xAAAAAAAA, 0xFFFFFFFF), (0, 0xAAAAAAAA, 0xFFFFFFFF), (0, 1, 2), (False, True),
    ):
        vtable = 0x261CE58 if priority == 1 else 0x261E920
        if struct.unpack_from("<Q", image, vtable + 0x48)[0] != base + 0x1B73D80:
            raise ValueError("not the expected native generic input handler")
        extension = 0x123CC10 if priority == 1 else 0x8E8AC0
        if struct.unpack_from("<Q", image, vtable + 0x198)[0] != base + extension:
            raise ValueError("not the expected variant input extension")
        uc.mem_write(STATE, struct.pack("<Q", base + vtable))
        uc.mem_write(STATE + 0x40, struct.pack("<I", 4 if active else 0))
        uc.mem_write(STATE + 0x340, bytes((latched,)))
        uc.mem_write(STATE + 0x348, struct.pack("<I", depth))
        uc.mem_write(STATE + 0x2C8 + 0x74, bytes((pending, pending, pending, 0)))
        uc.mem_write(STATE + 0x2C8 + 8, struct.pack("<Q", VALUE_BITS))
        uc.mem_write(STATE + 0x2C8 + 0x10, struct.pack("<I", 1))
        uc.mem_write(STATE + 0x2C8 + 0x30, struct.pack("<Q", DIRTY_BITS))
        uc.mem_write(STATE + 0x2C8 + 0x58, bytes(0x18))
        uc.mem_write(VALUE_BITS, struct.pack("<I", value_bits))
        uc.mem_write(DIRTY_BITS, struct.pack("<I", dirty_bits))
        events.clear()
        special = priority == 2 and matched
        model = ModelConfirmation(latched, depth, value_bits, dirty_bits, pending, pending, pending)
        model.events = []
        if special:
            model.request(lambda model=model: model.events.append(["register"]),
                          lambda model=model: model.events.append(["unregister"]))
        expected = model.events
        if not special:
            expected = [["request_begin"]]
        stack = STACK + 0xFFF08
        uc.mem_write(stack, struct.pack("<Q", END))
        uc.mem_write(stack + 0x28, struct.pack("<Q", PLUG))
        uc.reg_write(UC_X86_REG_RSP, stack)
        uc.reg_write(UC_X86_REG_RCX, STATE)
        uc.emu_start(base + 0x1B73D80, END, count=10000)
        if uc.reg_read(UC_X86_REG_RIP) != END:
            raise ValueError("native input exceeded instruction budget")
        actual = {
            "events": list(events), "latched": bool(uc.mem_read(STATE + 0x340, 1)[0]),
            "value_bits": word(VALUE_BITS), "dirty_bits": word(DIRTY_BITS),
            "markers": list(uc.mem_read(STATE + 0x2C8 + 0x74, 4)),
            "state_flags": word(STATE + 0x40), "returned": uc.reg_read(UC_X86_REG_RAX) & 255,
            "scope_depth": word(STATE + 0x348),
        }
        expected_values = {
            "events": expected, "latched": model.latched,
            "value_bits": model.value_bits, "dirty_bits": model.dirty_bits,
            "markers": [int(model.dirty), int(model.has_values), int(model.touched), 0],
            "state_flags": (4 if special else 12) if active else 0, "returned": 1,
            "scope_depth": model.scope_depth,
        }
        input_cases += 1
        if actual != expected_values:
            input_failures.append({"case": input_cases, "priority": priority,
                                   "depth": depth, "actual": actual, "expected": expected_values})
    return {
        "cases": cases, "failures": failures, "entry_rva": "CB6BA0",
        "scope": "native entity availability, no-dynamic-hit result writes/history/preview/commit order",
        "result_production_model": "placement.publish_static",
        "adapters": ["CC6EF0 owner service", "C8EB30 geometry result", "typed output writes",
                     "CAB450 history", "context lookup and plug forwarding"],
        "native_geometry_entity_relative_transforms_prediction_or_transport_accepted": False,
        "confirmation_input_cases": input_cases,
        "confirmation_input_failures": input_failures,
        "confirmation_production_model": "placement.Confirmation",
        "confirmation_input_scope": (
            "generic input, priority1/2 extension, priority2 latch, original nested scope entry/exit, "
            "property clear/dirty control; type, owner register/unregister, memset and begin are adapters"
        ),
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
    print(json.dumps({"cases": result["cases"], "failures": result["failures"][:2],
                      "input_cases": result["confirmation_input_cases"],
                      "input_failures": result["confirmation_input_failures"][:2]}))
    return bool(result["failures"] or result["confirmation_input_failures"])


if __name__ == "__main__":
    raise SystemExit(main())
