"""Compare native sampling counts, loops and height selection with declared services.

Three original CA0E50 blocks execute independently. Pose/basis construction,
sample checks, contextual predicate, transforms and allocation are explicit
fixtures. This does not accept actual geometry, full CA0E50 or deployment.
"""

import argparse
import hashlib
import itertools
import json
import struct
from pathlib import Path

from compare_placement_projection_native import BP, RESOURCE, SPEC, STATE, Native
from unicorn.x86_const import (
    UC_X86_REG_R8,
    UC_X86_REG_R12,
    UC_X86_REG_R13,
    UC_X86_REG_R14,
    UC_X86_REG_R15,
    UC_X86_REG_RAX,
    UC_X86_REG_RBP,
    UC_X86_REG_RBX,
    UC_X86_REG_RDI,
    UC_X86_REG_RDX,
    UC_X86_REG_RIP,
    UC_X86_REG_RSI,
    UC_X86_REG_RSP,
    UC_X86_REG_XMM3,
    UC_X86_REG_XMM6,
    UC_X86_REG_XMM7,
    UC_X86_REG_XMM9,
    UC_X86_REG_XMM11,
    UC_X86_REG_XMM12,
    UC_X86_REG_XMM14,
    UC_X86_REG_XMM15,
)

from ow174.game.script import expr
from ow174.game.script import placement_geometry as model

RECORDS, METADATA, REFERENCE, POINT, ENTITY, IDENTIFIER = (
    0x380000, 0x390000, 0x3A0000, 0x3A1000, 0x3A2000, 0x3A3000,
)


def sample_hit(fallback, index):
    return model.SampleHit(model.Contact(expr.Vec3(index, int(fallback) * 2, 10),
                                         expr.Vec3(0, 1, 0), .5),
                           expr.Entity(0x100000 + int(fallback) * 0x10000 + index), 0x10 + index)


def result_for(pattern, fallback, index):
    status = (0, 3, (index + int(fallback)) % 4)[pattern]
    return model.SampleResult(status, sample_hit(fallback, index) if status == 0 else None)


def allowed(filter_mode, fallback, index):
    return not filter_mode or (index + int(fallback)) % 2 == 1


class CandidateNative(Native):
    def hook(self, uc, address, size, user):
        offset = address - self.base
        if offset == self.stop or (offset == 0xC90323 and getattr(self, 'range_mode', False)):
            uc.emu_stop()
        elif offset == 0xC9E800:
            sp = uc.reg_read(UC_X86_REG_RSP)
            index = uc.reg_read(UC_X86_REG_RDX) & 0xFFFFFFFF
            fallback = bool(uc.mem_read(sp + 0x80, 1)[0])
            self.requests.append((fallback, index))
            self.current = fallback, index
            result = result_for(self.pattern, fallback, index)
            if result.status == 0:
                self.set_vector(self.qword(sp + 0x90), result.hit.contact.point)
                uc.mem_write(self.qword(sp + 0x98), struct.pack('<Q', result.hit.entity.id))
                uc.mem_write(self.qword(sp + 0xA0), struct.pack('<H', result.hit.identifier))
            self.returned(result.status)
        elif offset == 0xF67550:
            self.returned()  # Explicit result-context installation, no expression engine claimed.
        elif offset == 0x8F60F0:
            pointer = uc.reg_read(UC_X86_REG_RDX)
            if pointer == RESOURCE + 0x900:
                self.predicates.append(self.current)
                self.returned(int(allowed(self.filter_mode, *self.current)))
            else:
                self.returned(0)
        elif offset == 0x1A77F80:
            point = self.vector(uc.reg_read(UC_X86_REG_RDX))
            self.transforms.append(point)
            self.set_vector(uc.reg_read(UC_X86_REG_R8), self.transform(point))
            self.returned()
        else:
            super().hook(uc, address, size, user)

    def block(self, start, stop):
        self.stop = stop
        self.uc.emu_start(self.base + start, self.base + stop, count=100000)
        if self.uc.reg_read(UC_X86_REG_RIP) != self.base + stop:
            raise RuntimeError('native candidate block did not reach its exit')

    def counts(self, primary_length, fallback_length, offset, spacing):
        self.set_xmm(UC_X86_REG_XMM15, (fallback_length, 0, 0, 0))
        self.set_xmm(UC_X86_REG_XMM12, (abs(spacing), 0, 0, 0))
        self.set_xmm(UC_X86_REG_XMM9, (.5, 0, 0, 0))
        self.uc.reg_write(UC_X86_REG_RBP, BP)
        self.block(0xCA1262, 0xCA129E)
        fallback_count = self.uc.reg_read(UC_X86_REG_R14) & 0xFFFFFFFF
        fallback_step = self.scalar(UC_X86_REG_XMM12)
        self.set_xmm(UC_X86_REG_XMM14, (primary_length, 0, 0, 0))
        self.set_xmm(UC_X86_REG_XMM7, (offset, 0, 0, 0))
        self.uc.reg_write(UC_X86_REG_RSI, 2)
        self.block(0xCA1924, 0xCA1943)
        return model.SamplingPlan(self.uc.reg_read(UC_X86_REG_RSI), fallback_count, offset, fallback_step)

    def rangecheck(self, origin, candidates, minimum):
        self.prepare()
        self.range_mode, self.stop = True, 0xC902BC
        self.set_vector(BP - 0x60, origin)
        self.set_vector(BP - 0x10, candidates.primary)
        self.set_vector(BP + 0x60, candidates.fallback)
        self.set_xmm(UC_X86_REG_XMM3, (*candidates.primary, 0))
        self.set_xmm(UC_X86_REG_XMM6, (*candidates.fallback, 0))
        self.set_xmm(UC_X86_REG_XMM11, (minimum, 0, 0, 0))
        self.uc.emu_start(self.base + 0xC90212, self.base + 0xC902BC, count=1000)
        self.range_mode = False
        pc = self.uc.reg_read(UC_X86_REG_RIP) - self.base
        if pc == 0xC90323:
            if self.uc.reg_read(UC_X86_REG_RDI) != 2:
                raise RuntimeError('native both-too-close branch did not return status2')
            return None
        if pc != 0xC902BC:
            raise RuntimeError('native range check did not reach an admitted exit')
        return model.Candidates(self.vector(BP - 0x10), self.vector(BP + 0x60))

    def prepare(self):
        uc = self.uc
        uc.mem_write(0x200000, bytes(0x200000))
        for register, value in ((UC_X86_REG_RBP, BP), (UC_X86_REG_RSP, BP - 0x1500),
                                (UC_X86_REG_R12, SPEC), (UC_X86_REG_R13, RESOURCE),
                                (UC_X86_REG_R15, STATE)):
            uc.reg_write(register, value)
        for address, value in ((BP + 0x340, RECORDS), (BP - 0x68, METADATA), (BP + 0x28, SPEC),
                               (BP - 8, REFERENCE), (BP + 0x80, POINT),
                               (BP + 0x88, ENTITY), (BP + 0x90, IDENTIFIER)):
            uc.mem_write(address, struct.pack('<Q', value))
        for address in (BP + 0x34C, BP - 0x5C):
            uc.mem_write(address, struct.pack('<I', 0x80000040))

    def collect(self, plan, stride, start, preferred, shorten, pattern, filter_mode):
        self.prepare()
        uc = self.uc
        self.pattern, self.filter_mode = pattern, filter_mode
        self.requests, self.predicates = [], []
        uc.mem_write(SPEC, struct.pack('<4i', stride, start, preferred, -1))
        uc.mem_write(BP - 0x58, struct.pack('<i', plan.fallback_count))
        uc.mem_write(RESOURCE + 0xF0, struct.pack('<Q', RESOURCE + 0x800))
        uc.mem_write(RESOURCE + 0x178, struct.pack('<Q', RESOURCE + 0x900))
        uc.mem_write(self.base + 0x2E2EBC8, bytes([shorten]))
        self.values = {RESOURCE + 0x800: plan.primary_step}
        for register, value in ((UC_X86_REG_RAX, preferred & 0xFFFFFFFF),
                                (UC_X86_REG_RDI, 0), (UC_X86_REG_RBX, 0), (UC_X86_REG_RSI, 2),
                                (UC_X86_REG_R14, plan.fallback_count)):
            uc.reg_write(register, value)
        for register, value in ((UC_X86_REG_XMM9, .5),
                                (UC_X86_REG_XMM14, expr.f32(plan.primary_count * plan.primary_step)),
                                (UC_X86_REG_XMM15, expr.f32(plan.fallback_count * plan.fallback_step)),
                                (UC_X86_REG_XMM12, plan.fallback_step)):
            self.set_xmm(register, (value, 0, 0, 0))
        self.block(0xCA18BD, 0xCA1CF3)
        count = struct.unpack('<I', uc.mem_read(BP + 0x348, 4))[0]
        metadata_count = struct.unpack('<I', uc.mem_read(BP - 0x60, 4))[0]
        if count != metadata_count or count > 64:
            raise RuntimeError('native admitted arrays lost index alignment/capacity')
        candidates = []
        for position in range(count):
            index = struct.unpack('<I', uc.mem_read(RECORDS + position * 32 + 16, 4))[0]
            entity = self.qword(METADATA + position * 16)
            identifier = struct.unpack('<H', uc.mem_read(METADATA + position * 16 + 8, 2))[0]
            hit = model.SampleHit(model.Contact(self.vector(RECORDS + position * 32), expr.Vec3(0, 1, 0), .5),
                                  expr.Entity(entity), identifier)
            candidates.append(model.Candidate(bool(entity & 0x10000), index, hit))
        return tuple(candidates)

    def select(self, candidates, reference, transform):
        self.prepare()
        uc = self.uc
        self.transform, self.transforms = transform, []
        self.set_vector(REFERENCE, reference)
        uc.mem_write(BP + 0x348, struct.pack('<I', len(candidates)))
        for position, candidate in enumerate(candidates):
            self.set_vector(RECORDS + position * 32, candidate.hit.contact.point)
            uc.mem_write(RECORDS + position * 32 + 16, struct.pack('<I', candidate.index))
            uc.mem_write(METADATA + position * 16, struct.pack('<QH', candidate.hit.entity.id,
                                                             candidate.hit.identifier))
        self.set_xmm(UC_X86_REG_XMM6, (3.4028234663852886e38, 0, 0, 0))
        uc.reg_write(UC_X86_REG_RSI, 0)
        uc.reg_write(UC_X86_REG_RDI, 0xFFFFFFFF)
        self.block(0xCA21CD, 0xCA22B1)
        status = uc.reg_read(UC_X86_REG_RBX) & 0xFFFFFFFF
        selected = None
        if status == 0:
            selected = (self.vector(POINT), self.qword(ENTITY),
                        struct.unpack('<H', uc.mem_read(IDENTIFIER, 2))[0],
                        struct.unpack('<I', uc.mem_read(SPEC + 12, 4))[0])
        return status, selected


def compare(image, base):
    native = CandidateNative(image, base)
    failures, counts, loops, selections = [], 0, 0, 0
    range_profiles = 0
    threshold = struct.unpack('<I', struct.pack('<f', 1))[0]
    minimums = (0, .5, -1, 2, *(struct.unpack('<f', struct.pack('<I', v))[0]
                               for v in (threshold - 1, threshold, threshold + 1)))
    offsets = (expr.Vec3(0, 0, 0), expr.Vec3(1, 0, 0), expr.Vec3(0, 1, 0), expr.Vec3(2, 0, 0))
    for origin, primary, fallback, minimum in itertools.product(
        (expr.Vec3(0, 0, 0), expr.Vec3(10, -3, 5)), offsets, offsets, minimums,
    ):
        pair = model.Candidates(expr.Vec3(*(a + b for a, b in zip(origin, primary, strict=True))),
                                expr.Vec3(*(a + b for a, b in zip(origin, fallback, strict=True))))
        actual = native.rangecheck(origin, pair, minimum)
        expected = model.range_candidates(origin, pair, minimum)
        range_profiles += 1
        if actual != expected:
            failures.append({'kind': 'range', 'case': range_profiles})
    for primary, fallback, offset, spacing in itertools.product(
        (0, .2, .6, 4), (0, .2, 1.5, 35), (.4, .5), (-3, .4, 3),
    ):
        actual = native.counts(primary, fallback, offset, spacing)
        expected = model.sampling_plan(primary, fallback, offset, spacing)
        counts += 1
        if actual != expected:
            failures.append({'kind': 'counts', 'case': counts})
    for primary, fallback, start, stride, preferred, shorten, pattern, filter_mode in itertools.product(
        (0, 1, 2), (0, 1, 5), (0, 2), (1, 2), (-1, 0, 3), (False, True), (0, 1, 2), (False, True),
    ):
        plan = model.SamplingPlan(primary, fallback, .5, 3)
        requests, predicates = [], []

        def probe(phase, index, requests=requests, pattern=pattern):
            requests.append((phase, index))
            return result_for(pattern, phase, index)

        def accept(phase, index, hit, predicates=predicates, filter_mode=filter_mode):
            predicates.append((phase, index))
            return allowed(filter_mode, phase, index)

        expected = model.collect_candidates(plan, stride=stride, start=start, preferred=preferred,
                                            shorten=shorten, probe=probe, accept=accept)
        actual = native.collect(plan, stride, start, preferred, shorten, pattern, filter_mode)
        loops += 1
        if actual != expected or native.requests != requests or native.predicates != predicates:
            failures.append({'kind': 'loops', 'case': loops, 'native_requests': native.requests,
                             'model_requests': requests, 'actual_count': len(actual),
                             'expected_count': len(expected)})
    for size, scale, mix, reference_y in itertools.product((0, 1, 2, 5), (-1, 0, 1), (0, 1), (-2, 0, 2)):
        candidates = tuple(model.Candidate(False, i, model.SampleHit(
            model.Contact(expr.Vec3(i, (i % 3) - 1, 4 - i), expr.Vec3(0, 1, 0), .5),
            expr.Entity(100 + i), 0x20 + i)) for i in range(size))
        reference, transforms = expr.Vec3(0, reference_y, 0), []

        def transform(point, scale=scale, mix=mix):
            return expr.Vec3(point.x, expr.f32(expr.f32(point.y * scale) + expr.f32(point.z * mix)), point.z)

        def recorded(point, transforms=transforms, transform=transform):
            transforms.append(point)
            return transform(point)

        expected = model.select_candidate(candidates, reference, recorded)
        status, selected = native.select(candidates, reference, transform)
        wanted = (expected.hit.contact.point, expected.hit.entity.id, expected.hit.identifier,
                  expected.index) if expected is not None else None
        selections += 1
        if (status != (0 if expected is not None else 3) or selected != wanted
                or native.transforms != transforms):
            failures.append({'kind': 'selection', 'case': selections, 'status': status})
    return {'count_profiles': counts, 'loop_profiles': loops, 'selection_profiles': selections,
            'range_profiles': range_profiles,
            'failures': failures, 'original_blocks': ['C90212..C902BC', 'CA1262..129E', 'CA1924..1943',
                                                     'CA18BD..1CF3', 'CA21CD..22B1'],
            'comparison': 'exact counts/steps, request/predicate order, candidate metadata, selected output',
            'captured_shorten_on_success_flag': image[0x2E2EBC8],
            'adapters': ['resolved distance seeds', 'preallocated arrays/no allocation failure',
                         'sample replies', 'result-context installation', 'contextual predicate replies',
                         'declared coordinate transforms'],
            'full_CA0E50_or_actual_geometry_or_actors_accepted': False,
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
    print(json.dumps({key: report[key] for key in ('range_profiles', 'count_profiles',
                                                 'loop_profiles', 'selection_profiles')}))
    if report['failures']:
        raise SystemExit(f"{len(report['failures'])} native candidate mismatches")


if __name__ == '__main__':
    main()
