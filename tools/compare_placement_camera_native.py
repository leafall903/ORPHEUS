"""Execute original1A77F80 projection and CA0E50 selection with resolved matrices.

The view-key service and supplied camera matrices/mode/viewport are adapters.
Original matrix decode, arithmetic, depth guard, divide and viewport mapping run;
camera/basis/FOV construction, actual display services and geometry remain open.
"""

import argparse
import hashlib
import itertools
import json
import math
import struct
from pathlib import Path

from compare_placement_candidates_native import BP, CandidateNative
from compare_placement_projection_native import END
from unicorn.x86_const import (
    UC_X86_REG_R8,
    UC_X86_REG_RBP,
    UC_X86_REG_RCX,
    UC_X86_REG_RDX,
    UC_X86_REG_RIP,
    UC_X86_REG_RSI,
    UC_X86_REG_RSP,
    UC_X86_REG_XMM0,
    UC_X86_REG_XMM2,
    UC_X86_REG_XMM3,
)

from ow174.game.script import expr
from ow174.game.script import placement_geometry as model

CAMERA, POINT, OUTPUT, STACK = 0x3B0000, 0x3B1000, 0x3B2000, 0x250008
IDENTITY = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
VIEW_STEPS = (
    0xFC6AC04E28AD427C, 0xC5391C6DCDEF9262, 0xE7A2ECEB329A556B, 0x18096832E295D502,
    0x31D6CAE106E7EB2F, 0xE9B9A74D58CFF3E9, 0x57B249C7411CAC5B, 0x9197A36B2ECEFDB0,
)


def packed_matrix(matrix):
    return struct.pack('<16f', *(v for column in matrix for v in column))


class CameraNative(CandidateNative):
    def __init__(self, image, base):
        self.stop, self.key, self.camera = -1, 0, None
        self.transforms = []
        super().__init__(image, base)

    def hook(self, uc, address, size, user):
        offset = address - self.base
        if offset == 0x6E47A0:
            self.returned(self.key)  # Declared decoded key, no runtime key service accepted.
        elif offset == 0x1A77F80:
            self.transforms.append(self.vector(uc.reg_read(UC_X86_REG_RDX)))
            return  # Execute all original projection instructions.
        else:
            super().hook(uc, address, size, user)

    def install(self, address):
        key = self.key
        words = []
        for word in struct.unpack('<8Q', packed_matrix(self.camera.view)):
            words.append(word ^ key)
            key = (key + VIEW_STEPS[key & 7]) & 0xFFFFFFFFFFFFFFFF
        self.uc.mem_write(address + 0x58, struct.pack('<8Q', *words))
        self.uc.mem_write(address + 0x98, bytes(8))  # Opaque key service argument.
        self.uc.mem_write(address + 0xA0, packed_matrix(self.camera.clip))
        self.uc.mem_write(address + 0xE0, struct.pack('<I', int(self.camera.perspective)))
        self.uc.mem_write(address + 0x1F0, struct.pack('<4f', *self.camera.viewport))

    def prepare(self):
        super().prepare()
        self.install(BP + 0x430)

    def project(self, camera, point, key):
        self.camera, self.key = camera, key
        self.install(CAMERA)
        self.set_vector(POINT, point)
        sentinel = struct.pack('<4f', 7, 8, 9, 10)
        self.uc.mem_write(OUTPUT, sentinel)
        self.uc.mem_write(STACK, struct.pack('<Q', END))
        for register, value in ((UC_X86_REG_RSP, STACK), (UC_X86_REG_RCX, CAMERA),
                                (UC_X86_REG_RDX, POINT), (UC_X86_REG_R8, OUTPUT)):
            self.uc.reg_write(register, value)
        self.uc.emu_start(self.base + 0x1A77F80, END, count=10000)
        if self.uc.reg_read(UC_X86_REG_RIP) != END:
            raise RuntimeError('native camera projection did not return')
        depth = self.scalar(UC_X86_REG_XMM0)
        return depth, self.vector(OUTPUT), bytes(self.uc.mem_read(OUTPUT, 16)) == sentinel

    def perspective(self, horizontal, vertical, viewport, near, far):
        buffer, bounds = 0x3B4000, 0x3B5000
        uc = self.uc
        uc.mem_write(buffer, bytes(0x200))
        uc.mem_write(bounds, struct.pack('<4f', *viewport))
        uc.mem_write(STACK, struct.pack('<Q', END))
        uc.reg_write(UC_X86_REG_RSP, STACK)
        uc.reg_write(UC_X86_REG_RCX, buffer)
        uc.emu_start(self.base + 0x1A86510, END, count=1000)
        if uc.reg_read(UC_X86_REG_RIP) != END:
            raise RuntimeError('native projection defaults did not return')
        uc.mem_write(STACK, struct.pack('<Q', END))
        uc.mem_write(STACK + 0x28, struct.pack('<f', near))
        uc.mem_write(STACK + 0x30, struct.pack('<f', far))
        for register, value in ((UC_X86_REG_RSP, STACK), (UC_X86_REG_RCX, buffer), (UC_X86_REG_RDX, bounds)):
            uc.reg_write(register, value)
        self.set_xmm(UC_X86_REG_XMM2, (horizontal, 0, 0, 0))
        self.set_xmm(UC_X86_REG_XMM3, (vertical, 0, 0, 0))
        uc.emu_start(self.base + 0x1A86910, END, count=100000)
        if uc.reg_read(UC_X86_REG_RIP) != END:
            raise RuntimeError('native perspective builder did not return')
        values = struct.unpack('<16f', uc.mem_read(buffer, 64))
        matrix = tuple(tuple(values[i:i + 4]) for i in range(0, 16, 4))
        h, v = struct.unpack('<2f', uc.mem_read(buffer + 0x44, 8))
        return model.PerspectiveMatrix(matrix, h, v)

    def view(self, position, forward, up, near):
        uc = self.uc
        for register, value in ((UC_X86_REG_RBP, BP), (UC_X86_REG_RSP, 0x230000),
                                (UC_X86_REG_RSI, CAMERA)):
            uc.reg_write(register, value)
        self.set_vector(BP - 0x80, forward)
        self.set_vector(BP - 0x70, position)
        self.set_vector(0x230070, up)
        uc.mem_write(BP - 0x18, struct.pack('<f', near))
        self.block(0x1A77779, 0x1A778F3)
        values = struct.unpack('<16f', uc.mem_read(0x230030, 64))
        return tuple(tuple(values[i:i + 4]) for i in range(0, 16, 4))


def compare(image, base):
    native = CameraNative(image, base)
    failures, projections, selections = [], 0, 0
    builders, angle_error = 0, 0.0
    view_profiles, view_error, view_word_mismatches = 0, 0.0, 0
    poses = []
    for yaw, pitch in itertools.product((0, 30, 90, 180), (-89, -60, 0, 60, 89)):
        y, p = math.radians(yaw), math.radians(pitch)
        poses.append(expr.Vec3(math.sin(y) * math.cos(p), math.sin(p), math.cos(y) * math.cos(p)))
    for position, forward, up, scale, near in itertools.product(
        (expr.Vec3(0, 0, 0), expr.Vec3(2, 3, 4), expr.Vec3(-4, .25, 7)), poses,
        (expr.Vec3(0, 1, 0), expr.Vec3(0, 2, 0), expr.Vec3(.1, 1, .2)), (.5, 1, 2), (0, 1, 2),
    ):
        forward = expr.Vec3(*(expr.f32(v * scale) for v in forward))
        actual = native.view(position, forward, up, near)
        expected = model.view_matrix(position, forward, up, near)
        error = max(abs(a - b) for ac, ec in zip(actual, expected, strict=True)
                    for a, b in zip(ac, ec, strict=True))
        view_profiles += 1
        view_error = max(view_error, error)
        words_match = packed_matrix(actual) == packed_matrix(expected)
        view_word_mismatches += int(not words_match)
        if not words_match:
            failures.append({'kind': 'view', 'case': view_profiles, 'error': error})
    constructed_selections = 0
    position, up, viewport = expr.Vec3(2, 3, 4), expr.Vec3(0, 1, 0), (0, 0, 1920, 1080)
    for forward, size in itertools.product(poses, (0, 2, 5)):
        forward = expr.Vec3(*(expr.f32(v) for v in forward))
        native_view = native.view(position, forward, up, 1)
        native_clip = native.perspective(0, .611, viewport, 1, 100)
        model_view = model.view_matrix(position, forward, up, 1)
        model_clip = model.perspective_matrix(0, .611, viewport, 1, 100)
        native.camera, native.key = model.CameraProjection(native_view, native_clip.matrix, True, viewport), 7
        camera = model.CameraProjection(model_view, model_clip.matrix, True, viewport)
        candidates = tuple(model.Candidate(False, i, model.SampleHit(
            model.Contact(expr.Vec3(*(expr.f32(o + expr.f32(f * (i + 1)) + expr.f32(u * (i % 3) * .3))
                                      for o, f, u in zip(position, forward, up, strict=True))), up, .5),
            expr.Entity(100 + i), 0x20 + i)) for i in range(size))
        expected = model.select_candidate(candidates, position, camera.selection_point)
        if expected is NotImplemented:
            raise RuntimeError('constructed camera fixture failed depth admission')
        status, selected = native.select(candidates, position, camera.selection_point)
        wanted = (expected.hit.contact.point, expected.hit.entity.id, expected.hit.identifier,
                  expected.index) if expected is not None else None
        constructed_selections += 1
        if status != (0 if expected is not None else 3) or selected != wanted:
            failures.append({'kind': 'constructed_selection', 'case': constructed_selections,
                             'status': status})
    for horizontal, vertical, viewport, planes in itertools.product(
        (0, .001, .4, 1.55), (0, .001, .611, 1.55),
        ((0, 0, 1920, 1080), (0, 0, 100, 100), (-10, 20, 100, 200)),
        ((1, 100), (.1, 1000), (.5, 5), (1, 1.01)),
    ):
        actual = native.perspective(horizontal, vertical, viewport, *planes)
        expected = model.perspective_matrix(horizontal, vertical, viewport, *planes)
        error = max(abs(actual.horizontal_half - expected.horizontal_half),
                    abs(actual.vertical_half - expected.vertical_half))
        builders += 1
        angle_error = max(angle_error, error)
        angles_match = struct.pack('<2f', actual.horizontal_half, actual.vertical_half) == struct.pack(
            '<2f', expected.horizontal_half, expected.vertical_half)
        if packed_matrix(actual.matrix) != packed_matrix(expected.matrix) or not angles_match:
            failures.append({'kind': 'perspective_builder', 'case': builders,
                             'horizontal': horizontal, 'vertical': vertical, 'angle_error': error})
    views = (
        IDENTITY,
        ((0, 0, -1, 0), (0, 1, 0, 0), (1, 0, 0, 0), (2, -1, 10, 1)),
        ((2, .25, 0, 0), (-.5, 1, 0, 0), (0, 0, 1, 0), (1, 2, 5, 1)),
    )
    clips = (
        IDENTITY,
        ((1.25, 0, 0, 0), (0, 1.75, 0, 0), (0, 0, 1, 1), (0, 0, 0, 0)),
        ((1.5, .25, .125, 0), (.125, 1.25, .25, 0), (.25, .5, 1, 1), (.1, -.2, -.3, 1)),
    )
    viewports = ((0, 0, 1920, 1080), (-10, 20, 100, 200), (0, 0, 1, 1))
    epsilon = 1.1920928955078125e-6
    word = struct.unpack('<I', struct.pack('<f', epsilon))[0]
    depths = (-1, 0, *(struct.unpack('<f', struct.pack('<I', v))[0] for v in (word - 1, word, word + 1)),
              1, 2, 10)
    for view, clip, mode, viewport, depth, key in itertools.product(
        views, clips, (False, True), viewports, depths, (0, 1, 7, 0xFEEDFACE1),
    ):
        camera = model.CameraProjection(view, clip, mode, viewport)
        point = expr.Vec3(.25, -.5, depth)
        expected = camera.project(point)
        actual_depth, actual_point, untouched = native.project(camera, point, key)
        projections += 1
        matches = struct.pack('<f', actual_depth) == struct.pack('<f', expected.clip_depth)
        if expected.point is None:
            matches = matches and untouched
        else:
            matches = matches and struct.pack('<3f', *actual_point) == struct.pack('<3f', *expected.point)
        if not matches:
            failures.append({'kind': 'projection', 'case': projections,
                             'actual_depth': actual_depth, 'expected_depth': expected.clip_depth,
                             'actual_point': list(actual_point),
                             'expected_point': list(expected.point) if expected.point is not None else None})
    for view, clip, mode, viewport, size in itertools.product(
        views, clips, (False, True), viewports, (0, 1, 2, 5),
    ):
        camera = model.CameraProjection(view, clip, mode, viewport)
        native.camera, native.key = camera, 0xFEEDFACE1
        candidates = tuple(model.Candidate(False, i, model.SampleHit(
            model.Contact(expr.Vec3(i * .25, (i % 3) - 1, i + 1), expr.Vec3(0, 1, 0), .5),
            expr.Entity(100 + i), 0x20 + i)) for i in range(size))
        reference = expr.Vec3(0, 0, 1)
        expected = model.select_candidate(candidates, reference, camera.selection_point)
        if expected is NotImplemented:
            raise RuntimeError('coupled fixture has an unwritten projection, outside admission')
        status, selected = native.select(candidates, reference, camera.selection_point)
        wanted = (expected.hit.contact.point, expected.hit.entity.id, expected.hit.identifier,
                  expected.index) if expected is not None else None
        selections += 1
        if status != (0 if expected is not None else 3) or selected != wanted:
            failures.append({'kind': 'coupled_selection', 'case': selections, 'status': status})
    return {'projection_profiles': projections, 'coupled_selection_profiles': selections,
            'view_profiles': view_profiles, 'maximum_view_component_error': view_error,
            'view_absolute_tolerance': 2e-5, 'view_word_mismatch_profiles': view_word_mismatches,
            'view_comparison': 'all matrix float32 words exact under emulated974260 reciprocal seed',
            'constructed_camera_selection_profiles': constructed_selections,
            'perspective_builder_profiles': builders, 'maximum_builder_angle_error': angle_error,
            'perspective_builder_comparison': 'matrix and saved half-angle float32 words exact',
            'failures': failures,
            'comparison': 'float32 words for output/depth; no-write below guard; exact selection',
            'original_rvas': ['1A77F80', 'CA21CD..CA22B1'],
            'perspective_builder_rvas': ['1A86510', '1A86910', '1A86F30', '1A86540', '2500A04'],
            'view_builder_block': '1A77779..1A778F3 including974260',
            'depth_guard': epsilon,
            'adapters': ['decoded key service', 'resolved view/clip matrices', 'mode/viewport',
                         'preallocated candidate/metadata buffers', 'sinf/cosf ABI in builder profiles',
                         'decoded pose/basis seeds for view builder; no earlier sanitization/owner binding'],
            'nondefault_shift_scale_or_reverse_depth_accepted': False,
            'matrix_construction_or_actual_geometry_or_full_placement_accepted': False,
            'input_sha256': hashlib.sha256(image).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, type=Path)
    parser.add_argument('--base', required=True, type=lambda value: int(value, 0))
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output == args.image.resolve() or output.is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error('report must be external to source and distinct from input')
    report = compare(args.image.read_bytes(), args.base)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('perspective_builder_profiles',
                                                 'view_profiles', 'constructed_camera_selection_profiles',
                                                 'projection_profiles', 'coupled_selection_profiles')}))
    if report['failures']:
        raise SystemExit(f"{len(report['failures'])} native camera mismatches")


if __name__ == '__main__':
    main()
