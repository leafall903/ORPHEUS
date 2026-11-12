"""Compare placement projection/refinement with original104319 instructions.

Late entry C8FAB3 has declared pose inputs. Casts miss, candidate validation asks
for retry, initial clipping is absent. Owner/query construction/lifecycle services
are adapters. Refinement profiles supply contacts/clearance replies and reject
candidate validation; no actual cast, candidate validity or actor is accepted.
"""

import argparse
import hashlib
import itertools
import json
import math
import random
import struct
import sys
from pathlib import Path

from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_64, Uc
from unicorn.x86_const import (
    UC_X86_REG_EDI,
    UC_X86_REG_ESI,
    UC_X86_REG_R8,
    UC_X86_REG_R12,
    UC_X86_REG_R13,
    UC_X86_REG_R15,
    UC_X86_REG_RAX,
    UC_X86_REG_RBP,
    UC_X86_REG_RCX,
    UC_X86_REG_RDX,
    UC_X86_REG_RIP,
    UC_X86_REG_RSP,
    UC_X86_REG_XMM0,
    UC_X86_REG_XMM2,
    UC_X86_REG_XMM3,
    UC_X86_REG_XMM7,
    UC_X86_REG_XMM8,
    UC_X86_REG_XMM9,
    UC_X86_REG_XMM10,
    UC_X86_REG_XMM11,
    UC_X86_REG_XMM15,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]

from ow174.game.script import expr  # noqa: E402
from ow174.game.script import placement_geometry as model  # noqa: E402

BP = 0x240000
STATE, RESOURCE, CONTEXT, MANAGER, OWNER = 0x300000, 0x310000, 0x320000, 0x330000, 0x340000
OUTPOINT, OUTENTITY, OUTWORD, SPEC = 0x350000, 0x351000, 0x352000, 0x353000
END, LOOKUP = 0x600000, 0x600100
START, STOP = 0xC8FAB3, 0xC904DC


def bits(value):
    return struct.unpack("<I", struct.pack("<f", value))[0]


class Native:
    def __init__(self, image, base):
        self.base = base
        self.uc = uc = Uc(UC_ARCH_X86, UC_MODE_64)
        uc.mem_map(base, (len(image) + 4095) & ~4095)
        uc.mem_write(base, image)
        uc.mem_map(0x200000, 0x200000)
        uc.mem_map(END, 0x1000)
        self.values, self.directions, self.schedule = {}, [], None
        self.candidates = 0
        self.refinement_mode, self.hit_script = False, []
        self.queries, self.candidate_inputs = [], []
        uc.hook_add(UC_HOOK_CODE, self.hook)

    def qword(self, address):
        return struct.unpack("<Q", self.uc.mem_read(address, 8))[0]

    def scalar(self, reg):
        return struct.unpack("<f", (self.uc.reg_read(reg) & 0xFFFFFFFF).to_bytes(4, "little"))[0]

    def vector(self, address):
        return expr.Vec3(*struct.unpack("<3f", self.uc.mem_read(address, 12)))

    def set_vector(self, address, value):
        self.uc.mem_write(address, struct.pack("<4f", *value, 0))

    def set_xmm(self, reg, values):
        self.uc.reg_write(reg, int.from_bytes(struct.pack("<4f", *values), "little"))

    def returned(self, value=0, *, scalar=None):
        sp = self.uc.reg_read(UC_X86_REG_RSP)
        target = self.qword(sp)
        self.uc.reg_write(UC_X86_REG_RSP, sp + 8)
        self.uc.reg_write(UC_X86_REG_RAX, value)
        if scalar is not None:
            self.set_xmm(UC_X86_REG_XMM0, (expr.f32(scalar), 0, 0, 0))
        self.uc.reg_write(UC_X86_REG_RIP, target)

    def hook(self, uc, address, size, user):
        offset = address - self.base
        rcx, rdx, r8 = (uc.reg_read(r) for r in (UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8))
        if address == END or offset == STOP:
            uc.emu_stop()
        elif offset in (0x2501350, 0x2500E50):
            angle = self.scalar(UC_X86_REG_XMM0)
            self.returned(scalar=math.sin(angle) if offset == 0x2501350 else math.cos(angle))
        elif offset == 0x8F6350:
            self.returned(scalar=self.values[rdx] if rdx else self.scalar(UC_X86_REG_XMM2))
        elif offset == 0x8F60F0:
            self.returned(0)
        elif offset == 0x1B71C60:
            self.returned(MANAGER)
        elif address == LOOKUP or offset == 0x1B71C30:
            self.returned(OWNER)
        elif offset == 0xCC6EF0:
            self.returned(CONTEXT)
        elif offset == 0xD8E520:
            uc.mem_write(rcx, bytes(0x30))
            self.returned(rcx)
        elif offset == 0xBEC340:
            uc.mem_write(rcx, bytes(0x1C0))
            for array, inline, capacity in ((0x60, 0x70, 4), (0x90, 0xA0, 4), (0xD0, 0xE0, 8)):
                uc.mem_write(rcx + array, struct.pack("<QII", rcx + inline, 0, 0x80000000 | capacity))
            self.returned(rcx)
        elif offset == 0x184F5A0:
            uc.mem_write(rcx, bytes(uc.mem_read(rdx, r8)))
            self.returned(rcx)
        elif offset == 0xC1F280:
            self.schedule = model.ProjectionAngles(uc.reg_read(UC_X86_REG_ESI),
                                                   self.scalar(UC_X86_REG_XMM8),
                                                   self.scalar(UC_X86_REG_XMM9))
            self.returned(0)  # Explicit no initial clipping result.
        elif offset == 0xD617C0:
            self.directions.append(self.vector(BP - 0x50))
            scalar = struct.unpack("<f", uc.mem_read(rdx + 0x1A0, 4))[0]
            self.queries.append((self.vector(rdx), self.vector(rdx + 0x10), scalar))
            if self.refinement_mode:
                if not self.hit_script:
                    raise RuntimeError("unexpected extra native clearance query")
                reply = self.hit_script.pop(0)
                if type(reply) is model.Contact:
                    self.set_vector(rcx, reply.point)
                    self.set_vector(rcx + 0x10, reply.normal)
                    uc.mem_write(rcx + 0x30, struct.pack("<f", reply.fraction))
                    self.returned(1)
                else:
                    self.returned(int(reply))
            else:
                self.returned(0)  # Explicit admitted miss, not an unknown engine service.
        elif offset == 0xCA0E50:
            self.candidates += 1
            if self.refinement_mode:
                sp = uc.reg_read(UC_X86_REG_RSP)
                self.candidate_inputs.append(model.Candidates(self.vector(self.qword(sp + 0x28)),
                                                             self.vector(self.qword(sp + 0x30))))
                self.returned(3)  # Explicit candidate rejection, no accepted placement.
            else:
                self.returned(1)  # Explicit candidate retry, no accepted placement outcome.
        elif offset in (0x69E750, 0x68DA40):
            self.returned()

    def polynomial(self, word):
        sp = 0x250008
        self.uc.mem_write(sp, struct.pack("<Q", END))
        self.uc.reg_write(UC_X86_REG_RSP, sp)
        self.uc.reg_write(UC_X86_REG_XMM0, word)
        self.uc.emu_start(self.base + 0x5C7970, END, count=1000)
        if self.uc.reg_read(UC_X86_REG_RIP) != END:
            raise RuntimeError("native polynomial did not return")
        return self.uc.reg_read(UC_X86_REG_XMM0) & 0xFFFFFFFF

    def rotation(self, vector, axis, angle):
        sp, source, normal = 0x250008, 0x390000, 0x391000
        self.set_vector(source, vector)
        self.set_vector(normal, axis)
        self.uc.mem_write(sp, struct.pack("<Q", END))
        for reg, value in ((UC_X86_REG_RSP, sp), (UC_X86_REG_RCX, OUTPOINT),
                           (UC_X86_REG_RDX, source), (UC_X86_REG_R8, normal)):
            self.uc.reg_write(reg, value)
        self.set_xmm(UC_X86_REG_XMM3, (expr.f32(angle), 0, 0, 0))
        self.uc.emu_start(self.base + 0x884090, END, count=1000)
        if self.uc.reg_read(UC_X86_REG_RIP) != END:
            raise RuntimeError("native rotation did not return")
        return self.vector(OUTPOINT)

    def _prepare(self, direction, axis, step, *, offset=.4, query_scalar=.611):
        uc = self.uc
        uc.mem_write(0x200000, bytes(0x200000))
        self.values.clear()
        self.directions.clear()
        self.queries.clear()
        self.candidate_inputs.clear()
        self.schedule, self.candidates = None, 0
        for address, value in (
            (STATE + 0x78, CONTEXT), (CONTEXT + 0x28, MANAGER ^ 0x98DE97B50B3BE1E3),
            (MANAGER, MANAGER + 0x1000), (MANAGER + 0x1168, LOOKUP),
            (BP - 0x18, STATE), (BP + 0x30, RESOURCE), (BP - 0x80, OWNER),
            (BP + 0xA0, OUTWORD), (BP + 0xA8, SPEC), (BP + 0xB0, OUTENTITY), (BP + 0xB8, OUTPOINT),
        ):
            uc.mem_write(address, struct.pack("<Q", value))
        uc.mem_write(CONTEXT + 0x234, struct.pack("<I", 0x123))
        self.set_vector(BP - 0x60, expr.Vec3(0, 1.55, 0))
        self.set_vector(BP - 0x50, direction)
        self.set_vector(BP + 0x100, axis)
        for field, value in ((0x118, step), (0x128, query_scalar), (0xF0, offset)):
            pointer = RESOURCE + 0x800 + field
            uc.mem_write(RESOURCE + field, struct.pack("<Q", pointer))
            self.values[pointer] = value
        uc.mem_write(RESOURCE + 0x170, struct.pack("<Q", RESOURCE + 0xA00))
        uc.mem_write(BP - 0x100 + 0x70, struct.pack("<H", 0xFFFF))
        uc.mem_write(BP - 0x100 + 0x74, bytes(4))
        uc.mem_write(self.base + 0x2F7F89A, bytes(1))  # Debug rendering is not supplied.
        self.set_xmm(UC_X86_REG_XMM7, (0, 1, 0, 0))
        self.set_xmm(UC_X86_REG_XMM10, (0, 0, 0, 0))
        self.set_xmm(UC_X86_REG_XMM11, (0, 0, 0, 0))
        self.set_xmm(UC_X86_REG_XMM15, (35, 0, 0, 0))
        for reg, value in ((UC_X86_REG_RBP, BP), (UC_X86_REG_RSP, BP - 0x100),
                           (UC_X86_REG_R15, STATE), (UC_X86_REG_R12, RESOURCE), (UC_X86_REG_R13, 0xFFFF)):
            uc.reg_write(reg, value)

    def _run(self):
        uc = self.uc
        uc.emu_start(self.base + START, self.base + STOP, count=100000)
        if uc.reg_read(UC_X86_REG_RIP) != self.base + STOP:
            raise RuntimeError("native projection did not reach its exit")

    def projection(self, direction, axis, step):
        self._prepare(direction, axis, step)
        self.refinement_mode = False
        self._run()
        uc = self.uc
        if uc.reg_read(UC_X86_REG_EDI) != 1 or self.candidates != len(self.directions):
            raise RuntimeError("native fixture did not retain the declared retry path")
        return self.schedule, tuple(self.directions)

    def refinement(self, direction, axis, contact, offset, query_scalar, replies):
        self._prepare(direction, axis, 1, offset=offset, query_scalar=query_scalar)
        self.refinement_mode = True
        self.hit_script = [contact, *replies]
        self._run()
        if self.uc.reg_read(UC_X86_REG_EDI) != 3 or len(self.queries) != 3 or self.hit_script:
            raise RuntimeError("native fixture did not retain the declared refinement/rejection path")
        pair = self.candidate_inputs[0] if self.candidate_inputs else None
        return tuple(self.queries[1:]), pair


def compare(image, base):
    native = Native(image, base)
    rng = random.Random(104319)
    words = [0, 0x80000000, 0x7F800000, 0xFF800000, 0x7FC00001, 0xFFC00001,
             0x3F800000, 0x3F800001, 0x3F7FFFFF, 0xBF800000, 0xBF800001, 0xBF7FFFFF]
    words.extend(rng.randrange(1 << 32) for _ in range(4096))
    words.extend(bits(index / 1000) for index in range(-1000, 1001))
    failures = []
    for word in words:
        value = struct.unpack("<f", struct.pack("<I", word))[0]
        actual, expected = native.polynomial(word), bits(model.angle_polynomial(value))
        if actual != expected:
            failures.append({"kind": "polynomial", "word": hex(word),
                             "native": hex(actual), "model": hex(expected)})
    rotations, rotation_error = 0, 0.0
    for vector, axis, angle in itertools.product(
        (expr.Vec3(0, 1, 0), expr.Vec3(0, 0, 1), expr.Vec3(1, .2, -.3)),
        (expr.Vec3(1, 0, 0), expr.Vec3(2, 0, 0), expr.Vec3(0, 1, 0), expr.Vec3(0, 0, -1)),
        (0, model.PI_OVER_TWO, -.2, 1.7),
    ):
        actual = native.rotation(vector, axis, angle)
        expected = model.rotate(vector, axis, angle)
        error = max(abs(a - e) for a, e in zip(actual, expected, strict=True))
        rotation_error = max(rotation_error, error)
        rotations += 1
        if error > 2e-6:
            failures.append({"kind": "rotation", "case": rotations, "error": error})
    profiles, maximum_error = 0, 0.0
    for pitch, yaw, step in itertools.product(
        (-89, -60, -20, -8.3, 0, 8.3, 20, 60, 89), (0, 45, 90, 180), (-1, 0, 1, 3, 10, 90),
    ):
        p, y = math.radians(pitch), math.radians(yaw)
        direction = expr.Vec3(*(expr.f32(v) for v in (math.sin(y) * math.cos(p), math.sin(p),
                                                     math.cos(y) * math.cos(p))))
        axis = expr.Vec3(*(expr.f32(v) for v in (math.cos(y), 0, -math.sin(y))))
        schedule = model.projection_angles(direction, expr.Vec3(0, 1, 0), step)
        expected = [direction]
        for retry in range(schedule.additional_attempts):
            expected.append(schedule.advance(expected[-1], axis, retry))
        actual_schedule, actual = native.projection(direction, axis, step)
        errors = [abs(a - e) for av, ev in zip(actual, expected, strict=False)
                  for a, e in zip(av, ev, strict=True)]
        error = max(errors, default=0)
        maximum_error = max(maximum_error, error)
        profiles += 1
        if schedule != actual_schedule or len(actual) != len(expected) or error > 2e-6:
            failures.append({"kind": "projection", "pitch": pitch, "yaw": yaw, "step": step,
                             "native_count": len(actual), "model_count": len(expected), "error": error})
    refinement_profiles, refinement_error = 0, 0.0
    threshold_word = bits(model.CONTACT_NORMAL_SWITCH)
    threshold_normals = []
    for word in (threshold_word - 1, threshold_word, threshold_word + 1):
        y = struct.unpack("<f", struct.pack("<I", word))[0]
        threshold_normals.append(expr.Vec3(math.sqrt(1 - y * y), y, 0))
    fraction_word = bits(.015 / 35)
    fractions = (0, .0001, .2, 1, *(struct.unpack("<f", struct.pack("<I", word))[0]
                                   for word in (fraction_word - 1, fraction_word, fraction_word + 1)))
    for pose, normal, fraction, offset, scalar, replies in itertools.product(
        ((-20, 0), (0, 45), (20, 180)),
        (expr.Vec3(0, 1, 0), expr.Vec3(0, 0, -1), *threshold_normals),
        fractions, (.4, .5), (.611, .1745328),
        ((False, False), (True, False), (False, True), (True, True)),
    ):
        pitch, yaw = (math.radians(value) for value in pose)
        direction = expr.Vec3(*(expr.f32(v) for v in (math.sin(yaw) * math.cos(pitch), math.sin(pitch),
                                                     math.cos(yaw) * math.cos(pitch))))
        axis = expr.Vec3(math.cos(yaw), 0, -math.sin(yaw))
        origin = expr.Vec3(0, expr.f32(1.55), 0)
        end = expr.Vec3(*(expr.f32(o + expr.f32(d * 35))
                          for o, d in zip(origin, direction, strict=True)))
        contact = model.Contact(expr.Vec3(1, -.3, 5), normal, fraction)
        expected_queries = []

        def clearance(start, target, value, answers=replies, queries=expected_queries):
            index = len(queries)
            queries.append((start, target, value))
            return answers[index]

        expected_pair = model.refine_contact(origin, end, direction, contact, offset, scalar, clearance)
        actual_queries, actual_pair = native.refinement(direction, axis, contact, offset, scalar, replies)
        errors = []
        for actual, expected in zip(actual_queries, expected_queries, strict=True):
            for av, ev in zip(actual[:2], expected[:2], strict=True):
                errors.extend(abs(a - e) for a, e in zip(av, ev, strict=True))
            errors.append(abs(actual[2] - expected[2]))
        pair_shape = (actual_pair is None) == (expected_pair is None)
        if actual_pair is not None and expected_pair is not None:
            for av, ev in ((actual_pair.primary, expected_pair.primary),
                           (actual_pair.fallback, expected_pair.fallback)):
                errors.extend(abs(a - e) for a, e in zip(av, ev, strict=True))
        error = max(errors, default=0)
        refinement_profiles += 1
        refinement_error = max(refinement_error, error)
        if not pair_shape or error > 2e-5:
            failures.append({"kind": "refinement", "case": refinement_profiles, "pose": pose,
                             "fraction": fraction, "normal": list(normal), "replies": replies,
                             "pair_shape": pair_shape, "error": error})
    return {
        "polynomial_cases": len(words), "projection_profiles": profiles, "failures": failures,
        "rotation_cases": rotations, "maximum_rotation_component_error": rotation_error,
        "contact_refinement_profiles": refinement_profiles,
        "maximum_refinement_component_error": refinement_error,
        "refinement_absolute_tolerance": 2e-5,
        "polynomial_rva": "5C7970", "late_block_rvas": [f"{START:X}", f"{STOP:X}"],
        "rotation_rva": "884090", "polynomial_comparison": "return float32 word bit exact",
        "projection_absolute_tolerance": 2e-6, "maximum_projection_component_error": maximum_error,
        "adapters": ["declared pose seed", "config/owner services",
                     "preallocated query construction/lifecycle", "memcpy", "sinf/cosf ABI",
                     "no initial clip", "cast miss/retry and declared contact/clearance/rejection replies"],
        "scope": ("original polynomial, angle/count/first adjustment, "
                  "queried retry directions and pre-validation contact refinement with declared services"),
        "full_pose_casts_hit_refinement_validation_or_actor_deployment_accepted": False,
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
    print(json.dumps({key: result[key] for key in ("polynomial_cases", "projection_profiles",
                                                  "maximum_projection_component_error",
                                                  "contact_refinement_profiles",
                                                  "maximum_refinement_component_error")}))
    print(json.dumps({"failures": result["failures"][:3]}))
    return bool(result["failures"])


if __name__ == "__main__":
    raise SystemExit(main())
