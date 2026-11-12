"""Native projection math witnesses; no pose/cast/validation admission implied."""

import math
import struct
import unittest

from ow174.game.script import expr
from ow174.game.script import placement_geometry as geometry


class PlacementProjectionMathTests(unittest.TestCase):
    def test_original_polynomial_preserves_binary_witnesses_and_clamping(self):
        for value, expected in ((0, 0), (.5, .5235987901687622), (.9, 1.1181392669677734),
                                (1, 1.5707931518554688), (-.9, -1.1181392669677734)):
            with self.subTest(value=value):
                self.assertEqual(geometry.angle_polynomial(value), expected)
        for value in (2, float("inf"), float("nan")):
            self.assertEqual(geometry.angle_polynomial(value), 1.5707931518554688)
        self.assertEqual(geometry.angle_polynomial(-2), -1.5707931518554688)

    def test_horizontal_retry_schedule_repeats_first_direction_then_rotates(self):
        current, axis = expr.Vec3(0, 0, 1), expr.Vec3(1, 0, 0)
        angles = geometry.projection_angles(current, expr.Vec3(0, 1, 0), 1)
        self.assertEqual(angles.additional_attempts, 10)
        self.assertEqual(angles.first_adjustment, angles.step)
        first = angles.advance(current, axis, 0)
        self.assertEqual(first, current)
        second = angles.advance(first, axis, 1)
        self.assertGreater(second.y, 0)
        self.assertLess(second.z, 1)

    def test_near_vertical_count_and_nonpositive_step_use_native_schedule(self):
        for pitch, count in ((89, 0), (-89, 10)):
            radians = math.radians(pitch)
            direction = expr.Vec3(0, math.sin(radians), math.cos(radians))
            self.assertEqual(geometry.projection_angles(direction, expr.Vec3(0, 1, 0), 1)
                             .additional_attempts, count)
        for step in (0, -1):
            angles = geometry.projection_angles(expr.Vec3(0, 0, 1), expr.Vec3(0, 1, 0), step)
            self.assertEqual(angles.additional_attempts, 1)
            self.assertEqual(angles.first_adjustment, angles.step)

    def test_advance_uses_current_direction_supplied_after_the_validator(self):
        angles = geometry.projection_angles(expr.Vec3(0, 0, 1), expr.Vec3(0, 1, 0), 1)
        updated = angles.advance(expr.Vec3(0, -1, 0), expr.Vec3(1, 0, 0), 1)
        self.assertLess(updated.y, 0)
        self.assertGreater(updated.z, 0)
        for retry in (-1, 10, True):
            with self.subTest(retry=retry), self.assertRaises(ValueError):
                angles.advance(expr.Vec3(0, 0, 1), expr.Vec3(1, 0, 0), retry)

    def test_rotation_preserves_raw_axis_math_without_implicit_normalization(self):
        rotated = geometry.rotate(expr.Vec3(1, 0, 0), expr.Vec3(2, 0, 0), geometry.PI_OVER_TWO)
        self.assertEqual(rotated, expr.Vec3(4, 0, 0))
        self.assertEqual(geometry.rotate(expr.Vec3(1, 2, 3), expr.Vec3(0, 1, 0), 0), expr.Vec3(1, 2, 3))

    def test_invalid_service_vectors_and_unbounded_counter_profiles_are_rejected(self):
        for vector in ((0, 0, 1), expr.Vec3(True, 0, 1), expr.Vec3(float("nan"), 0, 1),
                       expr.Vec3(10**1000, 0, 0)):
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                geometry.projection_angles(vector, expr.Vec3(0, 1, 0), 1)
        for step in (True, float("inf"), 10**1000, 1e-43):
            with self.subTest(step=step), self.assertRaises(ValueError):
                geometry.projection_angles(expr.Vec3(0, 0, 1), expr.Vec3(0, 1, 0), step)
        for count in (-1, 11, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                geometry.ProjectionAngles(count, 1, 0)
        with self.assertRaises(ValueError):
            geometry.rotate(expr.Vec3(0, 0, 1), expr.Vec3(1, 0, 0), float("inf"))


class PlacementContactRefinementTests(unittest.TestCase):
    origin = expr.Vec3(0, 0, 0)
    end = expr.Vec3(0, 0, 10)
    direction = expr.Vec3(0, 0, 1)

    def test_minimum_range_replaces_only_a_too_close_candidate_and_preserves_equality(self):
        near, boundary, far = expr.Vec3(0, 0, 0), expr.Vec3(1, 0, 0), expr.Vec3(0, 0, 2)
        for pair, expected in (
            (geometry.Candidates(near, far), geometry.Candidates(far, far)),
            (geometry.Candidates(far, near), geometry.Candidates(far, far)),
            (geometry.Candidates(boundary, far), geometry.Candidates(boundary, far)),
            (geometry.Candidates(near, near), None),
        ):
            with self.subTest(pair=pair):
                self.assertEqual(geometry.range_candidates(self.origin, pair, 1), expected)
                self.assertEqual(geometry.range_candidates(self.origin, pair, -1), expected)
        pair = geometry.Candidates(near, near)
        self.assertEqual(geometry.range_candidates(self.origin, pair, 0), pair)

    def test_minimum_range_requires_explicit_finite_typed_inputs(self):
        pair = geometry.Candidates(self.origin, self.end)
        for minimum in (True, float('nan'), float('inf')):
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                geometry.range_candidates(self.origin, pair, minimum)
        with self.assertRaises(ValueError):
            geometry.range_candidates(self.origin, None, 1)

    def refine(self, normal, fraction=.5, replies=(False, False)):
        queries = []
        remaining = iter(replies)

        def clearance(*args):
            queries.append(args)
            return next(remaining)

        result = geometry.refine_contact(
            self.origin, self.end, self.direction,
            geometry.Contact(expr.Vec3(1, 2, 3), normal, fraction), .5, .611, clearance)
        return result, queries

    def test_clearance_outcomes_select_unvalidated_candidate_pair(self):
        primary, fallback = expr.Vec3(1, 2.5, 3), expr.Vec3(1, 2, 2.5)
        for replies, expected in (
            ((False, False), geometry.Candidates(primary, fallback)),
            ((True, False), geometry.Candidates(fallback, fallback)),
            ((False, True), geometry.Candidates(primary, primary)),
            ((True, True), None),
        ):
            with self.subTest(replies=replies):
                result, queries = self.refine(expr.Vec3(0, 1, 0), replies=replies)
                self.assertEqual(result, expected)
                self.assertEqual(len(queries), 2)
                self.assertEqual([q[1] for q in queries], [primary, fallback])
                self.assertEqual(queries[0][0], expr.Vec3(0, 0, expr.f32(5 - geometry.CONTACT_PADDING)))
                scalar = expr.f32(expr.f32(expr.f32(.611) + geometry.CONTACT_PADDING)
                                  - geometry.CONTACT_PADDING)
                self.assertEqual([q[2] for q in queries], [scalar, scalar])

    def test_normal_switch_is_strict_and_does_not_normalize_contact(self):
        threshold = geometry.CONTACT_NORMAL_SWITCH
        word = struct.unpack('<I', struct.pack('<f', threshold))[0]
        above = struct.unpack('<f', struct.pack('<I', word + 1))[0]
        result, _ = self.refine(expr.Vec3(2, threshold, 0))
        self.assertEqual(result.primary, expr.Vec3(2, expr.f32(2 + expr.f32(threshold * .5)), 3))
        result, _ = self.refine(expr.Vec3(2, above, 0))
        self.assertEqual(result.primary, expr.Vec3(1, 2.5, 3))

    def test_near_origin_contacts_do_not_backoff_past_the_origin(self):
        for fraction in (0, .0001):
            with self.subTest(fraction=fraction):
                _, queries = self.refine(expr.Vec3(0, 1, 0), fraction=fraction)
                self.assertEqual(queries[0][0], self.origin)
        _, queries = self.refine(expr.Vec3(0, 1, 0), fraction=.002)
        self.assertAlmostEqual(queries[0][0].z, .005, places=7)

    def test_unknown_clearance_preserves_unknown_without_guessing_a_candidate(self):
        for replies, count in (((NotImplemented,), 1), ((True, NotImplemented), 2)):
            with self.subTest(replies=replies):
                result, queries = self.refine(expr.Vec3(0, 1, 0), replies=replies)
                self.assertIs(result, NotImplemented)
                self.assertEqual(len(queries), count)
        for reply in (None, 0, 1, 'blocked'):
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                self.refine(expr.Vec3(0, 1, 0), replies=(reply,))

    def test_contact_rejects_invalid_service_data(self):
        for fraction in (-.1, 1.1, True, float('nan'), 10**1000):
            with self.subTest(fraction=fraction), self.assertRaises(ValueError):
                geometry.Contact(self.origin, self.direction, fraction)
        for vector in ((0, 1, 0), expr.Vec3(float('inf'), 0, 0)):
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                geometry.Contact(self.origin, vector, .5)
        with self.assertRaises(ValueError):
            geometry.refine_contact(self.origin, self.end, self.direction, None, .5, .611, None)
        for scalar in (True, float('inf'), 10**1000):
            with self.subTest(scalar=scalar), self.assertRaises(ValueError):
                geometry.refine_contact(self.origin, self.end, self.direction,
                                        geometry.Contact(self.origin, self.direction, .5),
                                        scalar, .611, None)


class PlacementSampleTests(unittest.TestCase):
    origin = expr.Vec3(1, 2, 3)
    direction = expr.Vec3(0, -.8, .6)

    def query(self, index=0, **overrides):
        profile = {"length": 10, "step": .4, "drop_limit": 4, "angle": .611, "sine_scale": .5,
                       "offset": .4, "sweep": True, "query_scalar": .611}
        profile.update(overrides)
        return geometry.sample_query(index, self.origin, self.direction, expr.Vec3(0, -1, 0), **profile)

    def check(self, *, point=None, fraction=.5, **overrides):
        point = self.origin if point is None else point
        hit = geometry.SampleHit(geometry.Contact(point, expr.Vec3(0, 1, 0), fraction),
                                 expr.Entity(123), 0xABCD)
        events, corrected = [], []
        replies = {"cast": hit, "clipped": False, "surface": True, "final": True}
        replies.update({key: overrides.pop(key) for key in tuple(overrides) if key in replies})

        def service(name):
            def call(*args):
                events.append(name)
                if name == 'final':
                    corrected.append(args[1])
                return replies[name]
            return call

        result = geometry.validate_sample(
            self.query(), expr.Vec3(1, 2, 0),
            **({"exclusion": 3, "limit_height": True, "maximum_y": 2} | overrides),
            **{name: service(name) for name in replies})
        return result, events, corrected

    def test_downward_first_sample_has_no_sweep_and_next_sample_sweeps_back(self):
        first = self.query()
        self.assertEqual(first.start, self.origin)
        self.assertEqual(first.end, expr.Vec3(1, expr.f32(2 - expr.f32(4 + expr.f32(.4))), 3))
        self.assertIsNone(first.sweep)
        self.assertEqual(first.scalar, 0)
        following = self.query(1)
        backward = expr.Vec3(*(expr.f32(a - b)
                               for a, b in zip(first.start, following.start, strict=True)))
        self.assertEqual(following.sweep, backward)
        self.assertEqual(following.scalar, expr.f32(.611))

    def test_upward_first_sample_can_sweep_and_disabled_sweep_zeroes_scalar(self):
        profile = {"length": 10, "step": .4, "drop_limit": 0, "angle": .611, "sine_scale": .5,
                       "offset": .5, "sweep": True, "query_scalar": .611}
        query = geometry.sample_query(0, self.origin, expr.Vec3(0, 1, 0),
                                       expr.Vec3(0, 1, 0), **profile)
        self.assertEqual(query.sweep, expr.Vec3(0, expr.f32(expr.f32(2 + expr.f32(.4)) - 2), 0))
        self.assertEqual(query.end, expr.Vec3(1, 1.5, 3))
        disabled = self.query(1, sweep=False)
        self.assertIsNone(disabled.sweep)
        self.assertEqual(disabled.scalar, 0)

    def test_equal_height_and_minimum_distance_pass_with_original_hit_metadata(self):
        result, events, corrected = self.check()
        self.assertEqual(result.status, 0)
        self.assertEqual(result.hit.entity, expr.Entity(123))
        self.assertEqual(result.hit.identifier, 0xABCD)
        self.assertEqual(result.hit.contact.point, expr.Vec3(1, 2, 3))
        self.assertEqual(corrected, [expr.Vec3(1, expr.f32(2 + geometry.CONTACT_PADDING), 3)])
        self.assertEqual(events, ['cast', 'clipped', 'surface', 'final'])

    def test_status_precedence_stops_at_the_first_rejected_check(self):
        for overrides, status, events in (
            ({'cast': None}, 3, ['cast']),
            ({'clipped': True}, 3, ['cast', 'clipped']),
            ({'surface': False}, 3, ['cast', 'clipped', 'surface']),
            ({'point': expr.Vec3(1, 3, 3), 'exclusion': 10}, 1, ['cast', 'clipped', 'surface']),
            ({'exclusion': 10}, 2, ['cast', 'clipped', 'surface']),
            ({'final': False}, 3, ['cast', 'clipped', 'surface', 'final']),
        ):
            with self.subTest(overrides=overrides):
                result, actual, _ = self.check(**overrides)
                self.assertEqual(result, geometry.SampleResult(status))
                self.assertEqual(actual, events)

    def test_unknown_services_stay_unknown_and_malformed_replies_are_rejected(self):
        for name in ('cast', 'clipped', 'surface', 'final'):
            with self.subTest(name=name):
                result, events, _ = self.check(**{name: NotImplemented})
                self.assertIs(result, NotImplemented)
                self.assertEqual(events[-1], name)
            with self.subTest(invalid=name), self.assertRaises(ValueError):
                self.check(**{name: 1})

    def test_query_and_result_service_boundaries_reject_invalid_profiles(self):
        for index in (-1, True, 1 << 31):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.query(index)
        for values in ({'offset': float('nan')}, {'query_scalar': True}, {'sweep': 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.query(**values)
        with self.assertRaises(ValueError):
            geometry.SampleQuery(self.origin, self.origin, None, float('inf'))
        with self.assertRaises(ValueError):
            geometry.SampleResult(0)
        with self.assertRaises(ValueError):
            geometry.SampleHit(geometry.Contact(self.origin, self.direction, .5), expr.Entity(0), -1)

    def test_static_surface_fallback_requires_strict_normal_threshold_and_flag_policy(self):
        y = geometry.CONTACT_NORMAL_SWITCH
        word = struct.unpack('<I', struct.pack('<f', y))[0]
        above = struct.unpack('<f', struct.pack('<I', word + 1))[0]
        self.assertFalse(geometry.static_surface_allowed(expr.Vec3(0, y, 0), 0, False))
        self.assertTrue(geometry.static_surface_allowed(expr.Vec3(2, above, 0), 0, False))
        for flags in (1, 3, 0xFFFF):
            with self.subTest(flags=flags):
                self.assertFalse(geometry.static_surface_allowed(expr.Vec3(0, 1, 0), flags, False))
                self.assertTrue(geometry.static_surface_allowed(expr.Vec3(0, 1, 0), flags, True))
        self.assertTrue(geometry.static_surface_allowed(expr.Vec3(0, 1, 0), 2, False))
        for flags in (-1, True, 0x10000):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                geometry.static_surface_allowed(expr.Vec3(0, 1, 0), flags, False)


class PlacementCandidateTests(unittest.TestCase):
    def hit(self, y=0, *, x=0, entity=123):
        return geometry.SampleHit(geometry.Contact(expr.Vec3(x, y, 0), expr.Vec3(0, 1, 0), .5),
                                  expr.Entity(entity), 7)

    def test_counts_round_with_half_offset_and_respace_the_fallback_interval(self):
        plan = geometry.sampling_plan(.2, 35, .4, -3)
        self.assertEqual((plan.primary_count, plan.fallback_count), (1, 12))
        self.assertEqual(plan.primary_step, expr.f32(.4))
        self.assertEqual(plan.fallback_step, expr.f32(35 / 12))
        empty = geometry.sampling_plan(0, .1, .4, 3)
        self.assertEqual((empty.primary_count, empty.fallback_count, empty.fallback_step), (0, 0, 3))
        self.assertEqual(geometry.sampling_plan(.6, 1.5, .4, 3).primary_count, 2)

    def test_success_shortens_each_phase_without_losing_sample_metadata(self):
        calls = []

        def probe(fallback, index):
            calls.append((fallback, index))
            return geometry.SampleResult(0, self.hit(index, entity=100 + index))

        candidates = geometry.collect_candidates(
            geometry.SamplingPlan(2, 10, .5, 3), stride=1, start=2, preferred=6,
            shorten=True, probe=probe, accept=lambda *_: True)
        self.assertEqual(calls, [(False, 0), (True, 2), (True, 3), (True, 4)])
        self.assertEqual([(c.fallback, c.index, c.hit.entity.id) for c in candidates],
                         [(False, 0, 100), (True, 2, 102), (True, 3, 103), (True, 4, 104)])

    def test_context_rejection_and_unknown_replies_do_not_become_accepted_candidates(self):
        plan = geometry.SamplingPlan(2, 0, .5, 3)
        def probe(*_):
            return geometry.SampleResult(0, self.hit())

        candidates = geometry.collect_candidates(plan, stride=1, start=0, preferred=-1,
                                                   shorten=True, probe=probe,
                                                   accept=lambda phase, index, hit: index == 1)
        self.assertEqual([c.index for c in candidates], [1])
        for service in ('probe', 'accept'):
            with self.subTest(service=service):
                callbacks = {'probe': probe, 'accept': lambda *_: True}
                callbacks[service] = lambda *_: NotImplemented
                self.assertIs(geometry.collect_candidates(plan, stride=1, start=0, preferred=-1,
                                                          shorten=False, **callbacks), NotImplemented)
        for service in ('probe', 'accept'):
            with self.subTest(invalid=service), self.assertRaises(ValueError):
                callbacks = {'probe': probe, 'accept': lambda *_: True}
                callbacks[service] = lambda *_: 0
                geometry.collect_candidates(plan, stride=1, start=0, preferred=-1,
                                             shorten=False, **callbacks)

    def test_selection_uses_transformed_height_instead_of_euclidean_distance(self):
        far = geometry.Candidate(False, 0, self.hit(0, x=100))
        near = geometry.Candidate(True, 2, self.hit(1))
        candidates = (far, near)
        self.assertIs(geometry.select_candidate(candidates, expr.Vec3(0, 0, 0), lambda p: p), far)
        self.assertIs(geometry.select_candidate(candidates, expr.Vec3(0, 0, 0),
                                                lambda p: expr.Vec3(p.y, p.x, p.z)), near)

    def test_selection_ties_keep_first_and_empty_input_still_transforms_reference(self):
        first = geometry.Candidate(False, 0, self.hit(-1))
        second = geometry.Candidate(True, 2, self.hit(1, entity=456))
        self.assertIs(geometry.select_candidate((first, second), expr.Vec3(0, 0, 0), lambda p: p), first)
        calls = []

        def transform(point):
            calls.append(point)
            return point

        reference = expr.Vec3(3, 2, 1)
        self.assertIsNone(geometry.select_candidate((), reference, transform))
        self.assertEqual(calls, [reference])
        unknown = geometry.select_candidate((first,), reference, lambda p: NotImplemented)
        self.assertIs(unknown, NotImplemented)

    def test_invalid_or_overflowing_counter_profiles_are_not_silently_admitted(self):
        for parameters in ((-1, 1, .4, 3), (1, 1, 0, 3), (1, 1, .4, 0), (1e20, 1, .4, 3)):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                geometry.sampling_plan(*parameters)
        for control in ({'stride': 0}, {'start': -1}, {'preferred': True}, {'shorten': 1},
                        {'stride': (1 << 31) - 1}):
            with self.subTest(control=control), self.assertRaises(ValueError):
                values = {'stride': 1, 'start': 0, 'preferred': -1, 'shorten': False} | control
                geometry.collect_candidates(geometry.SamplingPlan(2, 5, .5, 3),
                                             **values, probe=None, accept=None)
        with self.assertRaises(ValueError):
            geometry.select_candidate((), expr.Vec3(0, 0, 0), lambda p: (0, 0, 0))


class PlacementCameraTests(unittest.TestCase):
    identity = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
    clip = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 2, 1), (0, 0, 3, 0))

    def test_view_uses_the_resolved_near_offset_and_inverse_translation(self):
        view = geometry.view_matrix(expr.Vec3(2, 3, 4), expr.Vec3(0, 0, 1), expr.Vec3(0, 1, 0), 1)
        self.assertEqual(view, (*self.identity[:3], (-2, -3, -3, 1)))
        camera = geometry.CameraProjection(view, self.identity, False, (0, 0, 1, 1))
        self.assertEqual(camera.project(expr.Vec3(2, 3, 4)).point, expr.Vec3(.5, .5, 1))

    def test_view_preserves_nonunit_basis_instead_of_normalizing_it(self):
        view = geometry.view_matrix(expr.Vec3(0, 0, 0), expr.Vec3(0, 0, 2), expr.Vec3(0, 2, 0), 1)
        self.assertEqual(view[0][:3], (.25, 0, 0))
        self.assertEqual(view[1][:3], (0, .5, 0))
        self.assertEqual(view[2][:3], (0, 0, .5))
        self.assertEqual(view[3], (0, 0, 1, 1))

    def test_constructed_view_and_perspective_select_actual_raw_candidate_metadata(self):
        position, forward, up = expr.Vec3(2, 3, 4), expr.Vec3(.5, 0, math.sqrt(.75)), expr.Vec3(0, 1, 0)
        view = geometry.view_matrix(position, forward, up, 1)
        clip = geometry.perspective_matrix(0, .611, (0, 0, 100, 100), 1, 100)
        camera = geometry.CameraProjection(view, clip.matrix, True, (0, 0, 100, 100))
        points = tuple(expr.Vec3(*(expr.f32(p + f * distance + u * height)
                                  for p, f, u in zip(position, forward, up, strict=True)))
                       for distance, height in ((2, 1), (20, 2)))
        candidates = tuple(geometry.Candidate(False, i, geometry.SampleHit(
            geometry.Contact(point, up, .5), expr.Entity(100 + i), 7)) for i, point in enumerate(points))
        self.assertIs(geometry.select_candidate(candidates, position, camera.selection_point), candidates[1])

    def test_singular_or_untyped_decoded_view_inputs_are_outside_admission(self):
        for forward, up in ((expr.Vec3(0, 0, 0), expr.Vec3(0, 1, 0)),
                            (expr.Vec3(0, 1, 0), expr.Vec3(0, 2, 0)),
                            ((0, 0, 1), expr.Vec3(0, 1, 0))):
            with self.subTest(forward=forward), self.assertRaises(ValueError):
                geometry.view_matrix(expr.Vec3(0, 0, 0), forward, up, 1)
        with self.assertRaises(ValueError):
            geometry.view_matrix(expr.Vec3(0, 0, 0), expr.Vec3(0, 0, 1), expr.Vec3(0, 1, 0), True)

    def test_perspective_builder_preserves_native_mirrored_x_and_depth_planes(self):
        result = geometry.perspective_matrix(.4, .611, (0, 0, 1920, 1080), 1, 100)
        self.assertEqual(result.matrix[0][0], -2.365222454071045)
        self.assertEqual(result.matrix[1][1], 1.4277384281158447)
        self.assertEqual(result.matrix[2], (0, 0, 1.0101009607315063, 1))
        self.assertEqual(result.matrix[3], (0, 0, -1.0101009607315063, 0))

    def test_perspective_derived_angle_and_metadata_clamp_do_not_rebuild_matrix(self):
        derived = geometry.perspective_matrix(0, .4, (0, 0, 100, 100), 1, 100)
        self.assertEqual(derived.horizontal_half, expr.f32(.4))
        self.assertEqual(derived.vertical_half, expr.f32(.4))
        raw = geometry.perspective_matrix(1.55, .4, (0, 0, 100, 100), 1, 100)
        clamped = geometry.perspective_matrix(1.5, .4, (0, 0, 100, 100), 1, 100)
        self.assertEqual(raw.horizontal_half, 1.5)
        self.assertNotEqual(raw.matrix[0][0], clamped.matrix[0][0])

    def test_perspective_builder_rejects_unadmitted_plane_and_viewport_profiles(self):
        for viewport, near, far in (((0, 0, 0, 100), 1, 100), ((0, 0, 100, 100), 0, 100),
                                    ((0, 0, 100, 100), 1, 1),
                                    ((-3e38, 0, 3e38, 100), 1, 100),
                                    ((0, 0, 100, 100), 1e-45, 1)):
            with self.subTest(viewport=viewport, near=near), self.assertRaises(ValueError):
                geometry.perspective_matrix(0, .4, viewport, near, far)

    def test_perspective_output_and_undivided_return_depth_are_distinct(self):
        camera = geometry.CameraProjection(self.identity, self.clip, True, (0, 0, 100, 80))
        result = camera.project(expr.Vec3(2, 1, 4))
        self.assertEqual(result.point, expr.Vec3(75, 30, 2.75))
        self.assertEqual(result.clip_depth, 11)
        camera = geometry.CameraProjection(self.identity, self.clip, False, (0, 0, 100, 80))
        result = camera.project(expr.Vec3(2, 1, 4))
        self.assertEqual(result.point, expr.Vec3(150, 0, 11))
        self.assertEqual(result.clip_depth, 11)

    def test_view_depth_guard_does_not_invent_a_written_selection_point(self):
        camera = geometry.CameraProjection(self.identity, self.identity, False, (0, 0, 1, 1))
        threshold = 1.1920928955078125e-6
        word = struct.unpack('<I', struct.pack('<f', threshold))[0]
        below = struct.unpack('<f', struct.pack('<I', word - 1))[0]
        result = camera.project(expr.Vec3(.25, -.5, below))
        self.assertEqual(result, geometry.ProjectedPoint(None, -1))
        self.assertIs(camera.selection_point(expr.Vec3(0, 0, 0)), NotImplemented)
        result = camera.project(expr.Vec3(.25, -.5, threshold))
        self.assertEqual(result.point, expr.Vec3(.625, .75, threshold))

    def test_perspective_selection_can_prefer_a_point_farther_in_world_height(self):
        clip = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 1), (0, 0, 0, 0))
        camera = geometry.CameraProjection(self.identity, clip, True, (0, 0, 100, 100))
        candidates = tuple(geometry.Candidate(False, index, geometry.SampleHit(
            geometry.Contact(point, expr.Vec3(0, 1, 0), .5), expr.Entity(100 + index), 7))
                           for index, point in enumerate((expr.Vec3(0, 1, 2), expr.Vec3(0, 2, 20))))
        chosen = geometry.select_candidate(candidates, expr.Vec3(0, 0, 1), camera.selection_point)
        self.assertIs(chosen, candidates[1])
        self.assertEqual(chosen.hit.entity, expr.Entity(101))

    def test_resolved_matrix_and_divide_guards_reject_invalid_services(self):
        for matrix in ((), list(self.identity), ((1, 0, 0),) * 4,
                       ((float('nan'), 0, 0, 0),) * 4):
            with self.subTest(matrix=matrix), self.assertRaises(ValueError):
                geometry.CameraProjection(matrix, self.identity, False, (0, 0, 1, 1))
        for viewport in ((0, 0, 1), (0, 0, True, 1)):
            with self.subTest(viewport=viewport), self.assertRaises(ValueError):
                geometry.CameraProjection(self.identity, self.identity, False, viewport)
        zero = ((0, 0, 0, 0),) * 4
        camera = geometry.CameraProjection(self.identity, zero, True, (0, 0, 1, 1))
        with self.assertRaises(ValueError):
            camera.project(expr.Vec3(0, 0, 1))


if __name__ == "__main__":
    unittest.main()
