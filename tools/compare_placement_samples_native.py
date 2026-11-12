"""Compare C9E800 sample checks with explicit engine-service replies.

Original C9C760 and C9E800 execute. Cast, clipping, surface and final geometry
services are declared adapters; neither actual physics nor placement is accepted.
"""

import argparse
import hashlib
import itertools
import json
import math
import struct
from pathlib import Path

from compare_placement_projection_native import CONTEXT, END, MANAGER, RESOURCE, STATE, Native
from unicorn.x86_const import (
    UC_X86_REG_R8,
    UC_X86_REG_R9,
    UC_X86_REG_RAX,
    UC_X86_REG_RCX,
    UC_X86_REG_RDX,
    UC_X86_REG_RIP,
    UC_X86_REG_RSP,
    UC_X86_REG_XMM2,
)

from ow174.game.script import expr
from ow174.game.script import placement_geometry as model

STACK, QUERY, ORIGIN, DIRECTION, REFERENCE = 0x240008, 0x360000, 0x361000, 0x362000, 0x363000
BASIS, POINT, ENTITY, IDENTIFIER = 0x364000, 0x365000, 0x366000, 0x367000
EVAL, OBJECT, TABLE = END + 0x200, 0x370000, 0x371000


class SampleNative(Native):
    def hook(self, uc, address, size, user):
        offset = address - self.base
        rcx = uc.reg_read(UC_X86_REG_RCX)
        sp = uc.reg_read(UC_X86_REG_RSP)
        if offset == 0xCA5550 and getattr(self, 'static_surface_mode', False):
            return  # Execute the original static surface routine.
        if offset == 0x24E39C0:
            self.returned(uc.reg_read(UC_X86_REG_RAX))  # Cookie ABI preserves the result register.
        elif address == EVAL:
            self.returned(1)
        elif offset == 0xF64C80:
            self.returned(scalar=self.offset)
        elif offset == 0xF63740:
            self.returned()
        elif offset == 0xD617C0:
            query = uc.reg_read(UC_X86_REG_RDX)
            sweep = None
            if struct.unpack('<I', uc.mem_read(query + 0x1B0, 4))[0]:
                pointer = self.qword(query + 0x1A8)
                if self.vector(pointer) != expr.Vec3(0, 0, 0):
                    raise RuntimeError('native sweep first vector was not zero')
                sweep = self.vector(pointer + 16)
            self.query = model.SampleQuery(self.vector(query), self.vector(query + 16), sweep,
                                          struct.unpack('<f', uc.mem_read(query + 0x1A0, 4))[0])
            self.events.append('cast')
            if self.hit is not None:
                self.set_vector(rcx, self.hit.contact.point)
                self.set_vector(rcx + 16, self.hit.contact.normal)
                uc.mem_write(rcx + 0x20, struct.pack('<Q', self.hit.entity.id))
                uc.mem_write(rcx + 0x30, struct.pack('<f', self.hit.contact.fraction))
                uc.mem_write(rcx + 0x38, struct.pack('<H', self.hit.identifier))
            self.returned(int(self.hit is not None))
        elif offset in (0xC1F280, 0xCA5550, 0xC9DA00):
            name = {0xC1F280: 'clipped', 0xCA5550: 'surface', 0xC9DA00: 'final'}[offset]
            self.events.append(name)
            if name == 'final':
                self.corrected = self.vector(self.qword(sp + 0x28))
            self.returned(int(self.replies[name]))
        else:
            super().hook(uc, address, size, user)

    def sample(self, index, origin, direction, reference, basis, *, length, step,
               drop_limit, angle, sine_scale, offset, sweep, query_scalar, exclusion,
               limit_height, maximum_y, hit, clipped=False, surface=True, final=True):
        uc = self.uc
        uc.mem_write(0x200000, bytes(0x200000))
        self.hit, self.offset = hit, offset
        self.replies = {'clipped': clipped, 'surface': surface, 'final': final}
        self.events, self.query, self.corrected = [], None, None
        for address, value in ((STATE + 0x78, CONTEXT),
                               (CONTEXT + 0x28, MANAGER ^ 0x98DE97B50B3BE1E3),
                               (RESOURCE + 0xF0, RESOURCE + 0x800),
                               (RESOURCE + 0x808, OBJECT), (OBJECT, TABLE), (TABLE + 0x20, EVAL)):
            uc.mem_write(address, struct.pack('<Q', value))
        for address, value in ((ORIGIN, origin), (DIRECTION, direction),
                               (REFERENCE, reference), (BASIS, basis)):
            self.set_vector(address, value)
        for position, value in ((0x28, 0), (0x30, ORIGIN), (0x38, REFERENCE),
                                (0x40, DIRECTION), (0x90, POINT), (0x98, ENTITY),
                                (0xA0, IDENTIFIER), (0xA8, BASIS)):
            uc.mem_write(STACK + position, struct.pack('<Q', value))
        for position, value in ((0x48, length), (0x50, step), (0x58, drop_limit),
                                (0x60, exclusion), (0x68, angle), (0x70, sine_scale),
                                (0x88, maximum_y)):
            uc.mem_write(STACK + position, struct.pack('<f', value))
        uc.mem_write(STACK + 0x78, bytes([limit_height]))
        uc.mem_write(STACK + 0x80, bytes([sweep]))
        uc.mem_write(QUERY + 0x1A0, struct.pack('<f', query_scalar))
        uc.mem_write(self.base + 0x2F7F89A, bytes(1))
        uc.mem_write(STACK, struct.pack('<Q', END))
        for register, value in ((UC_X86_REG_RSP, STACK), (UC_X86_REG_RCX, STATE),
                                (UC_X86_REG_RDX, index), (UC_X86_REG_R8, RESOURCE),
                                (UC_X86_REG_R9, QUERY)):
            uc.reg_write(register, value)
        uc.emu_start(self.base + 0xC9E800, END, count=10000)
        if uc.reg_read(UC_X86_REG_RIP) != END:
            raise RuntimeError('native sample did not return')
        status = uc.reg_read(UC_X86_REG_RAX) & 0xFFFFFFFF
        output = None
        if status == 0:
            output = (self.vector(POINT), self.qword(ENTITY),
                      struct.unpack('<H', uc.mem_read(IDENTIFIER, 2))[0])
        return status, output

    def static_surface(self, normal, flags, ignore_flag):
        uc = self.uc
        self.static_surface_mode = True
        self.set_vector(QUERY + 16, normal)
        uc.mem_write(QUERY + 0x28, struct.pack('<Q', 0xFFFF0000FFFFFFFF))
        uc.mem_write(QUERY + 0x3E, struct.pack('<H', flags))
        uc.mem_write(STACK + 0x28, bytes([ignore_flag]))
        uc.mem_write(STACK, struct.pack('<Q', END))
        for register, value in ((UC_X86_REG_RSP, STACK), (UC_X86_REG_RCX, MANAGER),
                                (UC_X86_REG_RDX, QUERY), (UC_X86_REG_R9, 0x18)):
            uc.reg_write(register, value)
        self.set_xmm(UC_X86_REG_XMM2, (.05, 0, 0, 0))
        uc.emu_start(self.base + 0xCA5550, END, count=1000)
        self.static_surface_mode = False
        if uc.reg_read(UC_X86_REG_RIP) != END:
            raise RuntimeError('native static surface fallback did not return')
        return bool(uc.reg_read(UC_X86_REG_RAX) & 0xFF)


def query_error(actual, expected):
    if (actual.sweep is None) != (expected.sweep is None):
        return math.inf
    pairs = [(actual.start, expected.start), (actual.end, expected.end)]
    if actual.sweep is not None:
        pairs.append((actual.sweep, expected.sweep))
    return max([abs(actual.scalar - expected.scalar),
                *(abs(a - b) for av, bv in pairs for a, b in zip(av, bv, strict=True))])


def compare(image, base):
    native = SampleNative(image, base)
    failures, maximum_error, queries, checks = [], 0.0, 0, 0
    origin, reference = expr.Vec3(1, 2, 3), expr.Vec3(1, 2, 0)
    for index, y, basis, drop_limit, angle, sweep, offset in itertools.product(
        (0, 1, 2), (-.8, 0, .8), (expr.Vec3(0, -1, 0), expr.Vec3(0, 0, 1), expr.Vec3(0, 1, 0)),
        (0, 4), (0, .611), (False, True), (.4, .5),
    ):
        direction = expr.Vec3(0, y, math.sqrt(1 - y * y))
        profile = {'length': 10, 'step': .4, 'drop_limit': drop_limit, 'angle': angle, 'sine_scale': .5,
                       'offset': offset, 'sweep': sweep, 'query_scalar': .611}
        expected = model.sample_query(index, origin, direction, basis, **profile)
        status, _ = native.sample(index, origin, direction, reference, basis, **profile,
                                  exclusion=0, limit_height=False, maximum_y=0, hit=None)
        error = query_error(native.query, expected)
        maximum_error = max(maximum_error, error)
        queries += 1
        if error > 2e-5 or status != 3:
            failures.append({'kind': 'query', 'case': queries, 'error': error, 'status': status})
    profile = {'length': 10, 'step': .4, 'drop_limit': 4, 'angle': .611, 'sine_scale': .5,
                   'offset': .4, 'sweep': True, 'query_scalar': .611}
    direction, basis = expr.Vec3(0, -.8, .6), expr.Vec3(0, 1, 0)
    query = model.sample_query(1, origin, direction, basis, **profile)
    for fraction, point, exclusion, limit_height, clipped, surface, final in itertools.product(
        (0, .0001, .5, 1), (expr.Vec3(1, 1, 3), expr.Vec3(1, 2, 3), expr.Vec3(1, 3, 3)),
        (0, 3, 10), (False, True), (False, True), (False, True), (False, True),
    ):
        hit = model.SampleHit(model.Contact(point, expr.Vec3(0, 1, 0), fraction),
                              expr.Entity(0x123456), 0xABCD)
        events, corrected = [], []

        def cast(value, events=events, hit=hit):
            events.append('cast')
            return hit

        def clip(value, events=events, clipped=clipped):
            events.append('clipped')
            return clipped

        def surf(value, events=events, surface=surface):
            events.append('surface')
            return surface

        def finish(value, adjusted, events=events, corrected=corrected, final=final):
            events.append('final')
            corrected.append(adjusted)
            return final

        expected = model.validate_sample(query, reference, exclusion=exclusion,
                                         limit_height=limit_height, maximum_y=2,
                                         cast=cast, clipped=clip, surface=surf, final=finish)
        status, output = native.sample(1, origin, direction, reference, basis, **profile,
                                      exclusion=exclusion, limit_height=limit_height, maximum_y=2,
                                      hit=hit, clipped=clipped, surface=surface, final=final)
        error = 0
        if corrected and native.corrected is not None:
            error = max(abs(a - b) for a, b in zip(corrected[0], native.corrected, strict=True))
        maximum_error = max(maximum_error, error)
        wanted = (point, hit.entity.id, hit.identifier) if expected.status == 0 else None
        checks += 1
        if status != expected.status or events != native.events or output != wanted or error > 2e-5:
            failures.append({'kind': 'checks', 'case': checks, 'status': status,
                             'expected_status': expected.status, 'events': native.events,
                             'expected_events': events, 'error': error})
    surface_cases = 0
    word = struct.unpack('<I', struct.pack('<f', model.CONTACT_NORMAL_SWITCH))[0]
    normals = (-1, 0, 1, *(struct.unpack('<f', struct.pack('<I', value))[0]
                           for value in (word - 1, word, word + 1)))
    for y, flags, ignore_flag in itertools.product(normals, (0, 1, 2, 3, 0xFFFE, 0xFFFF), (False, True)):
        normal = expr.Vec3(.25, y, -.5)
        actual = native.static_surface(normal, flags, ignore_flag)
        expected = model.static_surface_allowed(normal, flags, ignore_flag)
        surface_cases += 1
        if actual != expected:
            failures.append({'kind': 'static_surface', 'normal_y': y, 'flags': flags,
                             'ignore_flag': ignore_flag, 'actual': actual, 'expected': expected})
    return {'query_profiles': queries, 'check_profiles': checks, 'failures': failures,
            'static_surface_cases': surface_cases,
            'maximum_component_error': maximum_error, 'absolute_tolerance': 2e-5,
            'original_rvas': ['C9C760', 'C9E800', 'CA5550 static fallback'],
            'adapters': ['context/config', 'sinf ABI', 'cast replies', 'nonmutating clipping',
                         'surface replies in sample profiles', 'final geometry replies',
                         'debug disabled', 'security-cookie ABI', 'declared no-body surface sentinel'],
            'actual_physics_or_placement_accepted': False,
            'input_sha256': hashlib.sha256(image).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, type=Path)
    parser.add_argument('--base', required=True, type=lambda value: int(value, 0))
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output == args.image.resolve() or output.is_relative_to(source):
        parser.error('report must be external to source and distinct from input')
    report = compare(args.image.read_bytes(), args.base)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('query_profiles', 'check_profiles',
                                                 'maximum_component_error')}))
    if report['failures']:
        raise SystemExit(f"{len(report['failures'])} native sample mismatches")


if __name__ == '__main__':
    main()
