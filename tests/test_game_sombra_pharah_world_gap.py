"""Accepted Pharah fire and delayed releases across missing owner commands."""

import unittest
from unittest.mock import patch

import test_game_sombra_pharah as fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge

from ow174.game import combat, pharah, stats
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes


class SombraPharahWorldGapTests(unittest.TestCase):
    def play(self, button, frames, gap, *, toward=True):
        f, fight = fixture.SombraPharahTests().arena()
        angles = aim(fight._eye(f.b), (0, 1.1, 0)) if toward else (0, 0)
        if button == 32:
            f.b.body_script.set_ult_charge(2100)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
            f.run.step({f.b: (*angles, button)})
            if gap:
                f.run.frame += frames
                f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                # World update leaves the endpoint for its real command.
                f.b.take_commands([Command(f.run.frame, yaw=angles[0], pitch=angles[1])])
            else:
                for _ in range(frames):
                    f.run.step({f.b: (*angles, 0)})
            count = shots.call_count
        return f, fight, count

    def test_primary_and_concussive_world_gap_match_damage_and_displacement(self):
        for button, frames, health in ((1, 30, 80), (16, 50, 200)):
            with self.subTest(button=button):
                samples = []
                for gap in (False, True):
                    f, fight, shots = self.play(button, frames, gap)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(shots, 1)
                    self.assertEqual(f.health(f.a), health)
                    self.assertLess(f.a.mover.position[2], -1)
                    samples.append((tuple(f.a.mover.position), tuple(f.b.mover.position)))
                self.assertEqual(samples[0], samples[1])

    def test_barrage_matches_report_count_curves_and_natural_end(self):
        samples = []
        for gap in (False, True):
            f, fight, shots = self.play(32, 125, gap, toward=False)
            self.assertEqual(shots, 61)
            samples.append(tuple((r.position, r.flown, r.range_left) for r in fight.rockets))
        self.assertEqual(samples[0], samples[1])
        for gap in (False, True):
            f, _, shots = self.play(32, 240, gap, toward=False)
            self.assertEqual(shots, 76)
            self.assertIsNone(pharah.barrage_channel(f.b.body_script.component))

    def test_barrage_lethal_tick_matches_and_replayed_input_cannot_fire_primary(self):
        deaths = []
        for gap in (False, True):
            f, fight, _ = self.play(32, 100, gap)
            self.assertEqual(f.health(f.a), 0)
            deaths.append(fight.vitals[f.a.body].death_tick)
        self.assertEqual(deaths[0], deaths[1])
        f, fight, _ = self.play(32, 100, True, toward=False)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
            f.b.take_commands([Command(f.run.frame - 1, buttons=1)])
            self.assertEqual(shots.call_count, 0)



class SombraPharahAirGapTests(unittest.TestCase):
    def launch(self):
        f, fight = fixture.SombraPharahTests().arena()
        player = f.b
        player.body_script.set_ult_charge(2100)
        f.run.step({player: (0, 0, 8)})
        for _ in range(7):
            f.run.step({player: (0, 0, 0)})
        f.run.step({player: (0, 0, 32)})
        self.assertIn(player.body, fight.pharah_airborne)
        self.assertGreater(player.mover.position[1], 2)
        return f, fight

    @staticmethod
    def advance(f, frames, gap):
        if gap:
            f.run.frame += frames
            f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
            f.b.take_commands([Command(f.run.frame)])
        else:
            for _ in range(frames):
                f.run.step({f.b: (0, 0, 0)})

    def test_airborne_barrage_locks_then_falls_and_lands_at_matching_poses(self):
        samples = []
        for gap in (False, True):
            f, fight = self.launch()
            start = tuple(f.b.mover.position)
            self.advance(f, 70, gap)
            self.assertEqual(tuple(f.b.mover.position), start)
            self.assertEqual(f.b.mover.velocity, (0, 0, 0))
            self.advance(f, 103, gap)
            self.assertIsNone(pharah.barrage_channel(f.b.body_script.component))
            self.assertGreater(f.b.mover.position[1], 0)
            self.assertLess(f.b.mover.position[1], start[1])
            self.assertLess(f.b.mover.velocity[1], 0)
            samples.append((tuple(f.b.mover.position), f.b.mover.velocity))
            self.advance(f, 60, gap)
            self.assertEqual(tuple(f.b.mover.position), (start[0], 0, start[2]))
            self.assertNotIn(f.b.body, fight.pharah_airborne)
        self.assertEqual(samples[0], samples[1])

    def test_actual_hack_and_emp_release_airborne_pose_during_missing_input(self):
        for control in (2, 32):
            with self.subTest(control=control):
                samples = []
                for gap in (False, True):
                    f, fight = self.launch()
                    if control == 32:
                        f.a.body_script.set_ult_charge(1400)
                    x, y, z = f.b.mover.position
                    angles = aim(fight._eye(f.a), (x, y + 1, z))
                    for i in range(72):
                        f.run.step({f.a: (*angles, control if i == 0 or control == 2 else 0),
                                    f.b: (0, 0, 0)})
                        if f.b.body in fight.hacks:
                            break
                    self.assertIn(f.b.body, fight.hacks)
                    self.assertIsNone(pharah.barrage_channel(f.b.body_script.component))
                    self.advance(f, 12, gap)
                    self.assertLess(f.b.mover.velocity[1], 0)
                    samples.append((tuple(f.b.mover.position), f.b.mover.velocity))
                    self.advance(f, 55, gap)
                    self.assertEqual(f.b.mover.position[1], 0)
                    self.assertNotIn(f.b.body, fight.pharah_airborne)
                self.assertEqual(samples[0], samples[1])

    def test_replacement_retires_old_body_tracking(self):
        f, fight = self.launch()
        retired = f.b.body
        self.assertTrue(f.run.match.switch_hero(f.b, heroes()[SOLDIER]))
        self.assertNotEqual(f.b.body, retired)
        self.assertNotIn(retired, fight.pharah_airborne)
        self.assertNotIn(f.b.body, fight.pharah_airborne)


class SombraPharahConcussiveCooldownTests(unittest.TestCase):
    @staticmethod
    def cast_emp(f, fight, launch_offset):
        f.a.body_script.set_ult_charge(1400)
        angles = aim(fight._eye(f.a), fight._eye(f.b))
        for i in range(22):
            f.run.step({f.a: (*angles, 32 if i == 0 else 0),
                        f.b: (0, 0, pharah.CONCUSSIVE_INPUT if i == launch_offset else 0)})
        effect, = fight.hacks[f.b.body]
        return effect.until_tick

    @staticmethod
    def advance(f, frame, gap, buttons=0, *, resume=True):
        if gap:
            f.run.frame = frame
            f.run.match.update(frame * combat.TICK, frame, frames=False)
            if resume:
                f.b.take_commands([Command(frame, buttons=buttons)])
        else:
            while f.run.frame < frame - 1:
                f.run.step({})
            f.run.step({f.b: (0, 0, buttons)})

    def test_emp_canceled_windup_rejects_preexpiry_input_and_accepts_at_expiry(self):
        for gap in (False, True):
            for offset in (-1, 0):
                with self.subTest(gap=gap, offset=offset):
                    f, fight = fixture.SombraPharahTests().arena()
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                        end = self.cast_emp(f, fight, launch_offset=12)
                        self.assertFalse(pharah.pending_release(f.b.body_script))
                        self.assertEqual(shots.call_count, 0)
                        frame = end + offset
                        self.advance(f, frame, gap, pharah.CONCUSSIVE_INPUT)
                        self.assertEqual(f.b.body in fight.hacks, offset < 0)
                        self.advance(f, frame + 20, gap)
                        self.assertEqual(shots.call_count, int(offset == 0))

    def test_late_emp_preserves_cooldown_and_buffered_release_on_original_world_tick(self):
        for gap in (False, True):
            with self.subTest(gap=gap):
                self.check_late_emp_buffer(gap)

    def check_late_emp_buffer(self, gap):
        f, fight = fixture.SombraPharahTests().arena()
        launches = []
        shoot = fight._shoot

        def record(player, command, state, *args, **kwargs):
            self.assertEqual((state.instance.graph.index, state.node.pos), pharah.CONCUSSIVE)
            launches.append((command.frame, fight.tick))
            return shoot(player, command, state, *args, **kwargs)

        with patch.object(fight, "_shoot", side_effect=record):
            end = self.cast_emp(f, fight, launch_offset=6)
            (launch, world_tick), = launches
            self.assertEqual(launch, world_tick)
            instance, = (ins for ins in f.b.body_script.component.instances.values()
                         if ins.graph.index == pharah.CONCUSSIVE[0])
            ability, = (state for state in instance.states.values()
                        if isinstance(state, nodes.Ability) and state.node.pos == 14)
            self.assertEqual(ability.cur, 9)
            self.assertGreater(launch + 561, end)
            self.advance(f, launch + 561, gap, pharah.CONCUSSIVE_INPUT)
            self.assertGreater(ability.remaining(), 0)
            self.assertEqual(launches, [(launch, launch)])
            # The skill graph buffered the real press. World time owns its eventual shot.
            self.advance(f, launch + 581, gap, resume=False)
            self.assertEqual(launches, [(launch, launch), (launch + 573, launch + 573)])
            if gap:
                f.b.take_commands([Command(f.run.frame)])
                self.assertEqual(len(launches), 2)


class SombraPharahHoverGapTests(unittest.TestCase):
    @staticmethod
    def sample(f):
        player = f.b
        instance = pharah.hover_instance(player.body_script)
        return (tuple(player.mover.position), player.mover.velocity,
                instance.vars[87].value(), pharah.hover_force(player.body_script) is not None)

    @staticmethod
    def without_input(f, frames):
        f.run.frame += frames
        f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)

    def test_hover_bindings_lift_and_full_depletion_recovers_without_owner_frames(self):
        for button in (2, 64):
            with self.subTest(button=button):
                f, fight = fixture.SombraPharahTests().arena()
                for _ in range(40):
                    f.run.step({f.b: (0, 0, button)})
                position, velocity, fuel, active = self.sample(f)
                self.assertGreater(position[1], 1)
                self.assertAlmostEqual(velocity[1], 2.4697, places=3)
                self.assertTrue(active)
                self.assertLess(fuel, 100)
                if button == 2:
                    for _ in range(86):
                        f.run.step({f.b: (0, 0, button)})
                    self.assertEqual(self.sample(f)[2:], (0, False))
                self.without_input(f, 240)
                self.assertEqual(self.sample(f), ((0, 0, 6), (0, 0, 0), 100, False))
                self.assertNotIn(f.b.body, fight.pharah_airborne)
                f.b.take_commands([Command(f.run.frame, buttons=button)])
                for _ in range(20):
                    f.run.step({f.b: (0, 0, button)})
                self.assertTrue(self.sample(f)[3])

    def test_single_accepted_hover_press_releases_with_matching_airborne_pose(self):
        samples = []
        for gap in (False, True):
            f, _ = fixture.SombraPharahTests().arena()
            f.run.step({f.b: (0, 0, 2)})
            self.assertEqual(f.b.mover.position[1], 0)
            if gap:
                self.without_input(f, 3)
            else:
                for _ in range(2):
                    f.run.step({})
            sample = self.sample(f)
            self.assertGreater(sample[0][1], 0)
            self.assertFalse(sample[3])
            samples.append(sample)
        self.assertEqual(samples[0], samples[1])

    def test_hack_and_emp_match_world_only_descent_landing_and_refuel(self):
        for control in (2, 32):
            with self.subTest(control=control):
                runs = []
                for gap in (False, True):
                    f, fight = fixture.SombraPharahTests().arena()
                    for _ in range(16):
                        f.run.step({f.b: (0, 0, 2)})
                    if control == 32:
                        f.a.body_script.set_ult_charge(1400)
                    for i in range(100):
                        angles = aim(fight._eye(f.a), fight._eye(f.b))
                        f.run.step({f.a: (*angles, control if control == 2 or i == 0 else 0),
                                    f.b: (0, 0, 2)})
                        if f.b.body in fight.hacks:
                            break
                    self.assertIn(f.b.body, fight.hacks)
                    self.assertGreater(f.b.mover.position[1], 1)
                    self.assertFalse(self.sample(f)[3])
                    hit = f.run.frame
                    samples = []
                    for offset in (11, 66, 159):
                        target = hit + offset
                        if gap:
                            self.without_input(f, target + 1 - f.run.frame)
                        else:
                            while f.run.frame < target:
                                f.run.step({})
                        samples.append(self.sample(f))
                        if gap:
                            f.b.take_commands([Command(f.run.frame)])
                    self.assertLess(samples[0][1][1], 0)
                    self.assertEqual(samples[1][0][1], 0)
                    self.assertEqual(samples[-1], ((0, 0, 6), (0, 0, 0), 100, False))
                    self.assertNotIn(f.b.body, fight.pharah_airborne)
                    runs.append(samples)
                self.assertEqual(runs[0], runs[1])


class SombraPharahBarrageLifecycleTests(unittest.TestCase):
    @staticmethod
    def advance(f, frames, gap):
        if gap:
            f.run.frame += frames
            f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
        else:
            for _ in range(frames):
                f.run.step({})

    def test_hack_and_emp_stop_barrage_without_erasing_released_rockets(self):
        for control, expected_frame in ((2, 40), (32, 21)):
            with self.subTest(control=control):
                results = []
                for gap in (False, True):
                    f, fight = fixture.SombraPharahTests().arena()
                    f.run.step({})
                    f.b.body_script.set_ult_charge(2100)
                    if control == 32:
                        f.a.body_script.set_ult_charge(1400)
                    to_sombra = aim(fight._eye(f.b), (0, 1.1, 0))
                    to_pharah = aim(fight._eye(f.a), (0, .95, 6))
                    for offset in range(expected_frame + 1):
                        angles = to_sombra if offset >= expected_frame - 6 else (0, 0)
                        f.run.step({f.a: (*to_pharah, control if control == 2 or offset == 0 else 0),
                                    f.b: (*angles, 32 if offset == 0 else 0)})
                    self.assertIn(f.b.body, fight.hacks)
                    self.assertIsNone(pharah.barrage_channel(f.b.body_script.component))
                    self.assertEqual(f.health(f.a), 200)
                    self.assertTrue(fight.rockets)
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                        self.advance(f, 65, gap)
                        self.assertEqual(shots.call_count, 0)
                    self.assertLess(f.health(f.a), 200)
                    self.assertEqual(charge(f.b), 0)
                    results.append(f.health(f.a))
                self.assertEqual(results[0], results[1])

    def test_machine_pistol_trade_credits_barrage_to_original_body(self):
        death_ticks = []
        for gap, replace_body in ((False, False), (True, False), (True, True)):
            with self.subTest(gap=gap, replace_body=replace_body):
                f, fight = fixture.SombraPharahTests().arena()
                place(f.b, (0, 0, 12))
                f.run.step({})
                to_sombra = aim(fight._eye(f.b), (0, 1.1, 0))
                f.run.step({f.b: (*to_sombra, 1)})
                self.advance(f, 65, False)
                self.assertEqual(f.health(f.a), 80)
                # Restore the initial range after the primary's actual knockback.
                place(f.a, (0, 0, 0))
                f.run.step({})
                to_pharah = aim(fight._eye(f.a), (0, .95, 12))
                for _ in range(150):
                    f.run.step({f.a: (*to_pharah, 1)})
                    if f.health(f.b) <= 8:
                        break
                self.assertEqual(f.health(f.b), 8)
                self.advance(f, 15, False)
                old_body = f.b.body
                f.b.body_script.set_ult_charge(2100)
                f.run.step({f.b: (*to_sombra, 32)})
                for _ in range(4):
                    f.run.step({f.b: (*to_sombra, 0)})
                f.run.step({f.a: (*to_pharah, 1), f.b: (*to_sombra, 0)})
                self.assertTrue(fight.is_dead(f.b))
                self.assertEqual(fight.vitals[old_body].killer, f.a.body)
                self.assertIsNone(pharah.barrage_channel(f.b.body_script.component))
                self.assertEqual(f.health(f.a), 80)
                self.assertEqual(len(fight.rockets), 3)
                self.assertTrue(all(r.definition == pharah.BARRAGE_ENTITY and r.source_body == old_body
                                    for r in fight.rockets))
                if replace_body:
                    self.assertTrue(f.run.match.switch_hero(f.b, heroes()[SOLDIER]))
                    self.assertNotEqual(f.b.body, old_body)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    self.advance(f, 60, gap)
                    self.assertEqual(shots.call_count, 0)
                self.assertEqual(f.health(f.a), 0)
                self.assertEqual(fight.vitals[f.a.body].killer, old_body)
                self.assertEqual(f.a.stats.value(stats.ELIMINATIONS), 1)
                self.assertEqual(f.b.stats.value(stats.ELIMINATIONS), 1)
                death, = f.a.client.messages(combat.DEATH_NOTICE)
                self.assertEqual(death["+0x7C"]["+0x0"], old_body)
                self.assertFalse(fight.rockets)
                death_ticks.append(fight.vitals[f.a.body].death_tick)
        self.assertEqual(death_ticks, [death_ticks[0]] * 3)

    def test_pharah_projectile_damage_keeps_camo_exit_and_cooldown_on_original_timeline(self):
        for button, health in ((1, 80), (32, 160)):
            with self.subTest(button=button):
                runs = []
                for gap in (False, True):
                    f, fight = fixture.SombraPharahTests().arena()
                    place(f.b, (0, 0, 12))
                    f.press(8)
                    f.press(0, 60)
                    camo = f.camo().state(13)
                    self.assertTrue(camo.flags & camo.RUNNING)
                    if button == 32:
                        f.b.body_script.set_ult_charge(2100)
                    angles = aim(fight._eye(f.b), (0, 1.1, 0))
                    f.run.step({f.b: (*angles, button)})
                    self.assertEqual(len(fight.rockets), 1)
                    self.assertTrue(f.run.match.switch_hero(f.b, heroes()[SOLDIER]))
                    self.advance(f, 40, gap)
                    if gap:
                        f.a.take_commands([Command(f.run.frame)])
                    self.assertEqual(f.health(f.a), health)
                    self.assertTrue(camo.flags & camo.RUNNING)
                    ended = None
                    for offset in range(41, 81):
                        f.run.step({})
                        if ended is None and not camo.flags & camo.RUNNING:
                            ended = offset
                    self.assertIsNotNone(ended)
                    self.assertGreater(camo.remaining(), 0)
                    runs.append((ended, camo.remaining()))
                self.assertEqual(runs[0], runs[1])

    def test_barrage_hit_ends_camo_and_cooldown_without_sombra_commands(self):
        f, fight = fixture.SombraPharahTests().arena()
        place(f.b, (0, 0, 12))
        f.press(8)
        f.press(0, 60)
        ability = f.camo().state(13)
        target = next(target for target in fight._targets() if target.owner is f.a)
        self.assertFalse(fight.visible_for_acquisition(target, (0, 0, 12)))
        f.b.body_script.set_ult_charge(2100)
        angles = aim(fight._eye(f.b), (0, 1.1, 0))
        f.run.step({f.b: (*angles, 32)})
        self.assertTrue(f.run.match.switch_hero(f.b, heroes()[SOLDIER]))
        start = f.run.frame

        self.advance(f, 18, True)
        self.assertEqual(f.health(f.a), 160)
        self.assertTrue(fight.visible_for_acquisition(target, (0, 0, 12)))
        self.assertTrue(ability.flags & ability.RUNNING)
        self.advance(f, 45, True)
        self.assertEqual(f.a.body_script.frame, start + 62)
        self.assertTrue(ability.flags & ability.RUNNING)
        self.advance(f, 1, True)
        self.assertEqual(f.a.body_script.frame, start + 63)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertEqual(ability.remaining(), 6)
        self.advance(f, 436, True)
        self.assertEqual(ability.remaining(), 0)

        with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
            f.a.take_commands([Command(start + 60, buttons=1)])
            self.assertEqual(shots.call_count, 0)
            f.a.take_commands([Command(f.run.frame, buttons=1)])
            self.assertEqual(shots.call_count, 1)
