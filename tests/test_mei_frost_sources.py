"""Executed per-source reconstruction, not native effect-instance merge proof."""

import dataclasses
import unittest

import test_mei_blizzard as blizzard_fixtures
import test_mei_combat as fixtures
import test_mei_frost as frost_fixtures
import test_mei_wall as wall_fixtures
from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat, mei, mei_frost
from ow174.game.content import heroes


class FrostSourceTests(unittest.TestCase):
    def scene(self, *, victim_mei=False):
        run = Run(KANEZAKA_DM, ('Mei A', 'Mei B', 'Victim'))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        first, second, victim = run.players
        for actor in (first, second, *((victim,) if victim_mei else ())):
            run.match.switch_hero(actor, heroes()[mei.HERO])
        x, y, z = first.mover.position
        for actor, position in ((first, (x - 1., y, z)), (second, (x + 1., y, z)),
                                (victim, (x, y, z + 5.))):
            run.match.combat.walls.place_player(actor, position)
        for _ in range(30):
            run.step({})
        target = next(t for t in run.match.combat._targets() if t.owner is victim)
        return run, first, second, victim, target

    def hit(self, run, source, target, count=1):
        for _ in range(count):
            run.match.combat._damage(source, target, 2.75, False, mei.SPRAY_LOADOUT, run.frame)

    def both_spray(self, run, first, second, target, frames=60):
        point = (target.feet[0], target.feet[1] + .9, target.feet[2])
        a = fixtures.aim(run.match.combat._eye(first), point)
        b = fixtures.aim(run.match.combat._eye(second), point)
        for _ in range(frames):
            run.step({first: (*a, 1), second: (*b, 1)})
        for _ in range(10):
            run.step({})

    def test_actual_two_mei_sprays_keep_independent_progress_and_take_maximum(self):
        run, first, second, victim, target = self.scene()
        self.both_spray(run, first, second, target)
        counts = [len(actor.client.messages(combat.HIT)) for actor in (first, second)]
        self.assertTrue(all(10 <= count < 30 for count in counts), counts)
        expected = .2 + (max(counts) - 1) * .0285
        sample = run.match.combat.frost(victim)
        self.assertAlmostEqual(sample[0], expected, places=5)
        self.assertFalse(run.match.combat.frozen(victim))
        self.assertEqual(len(run.match.combat.frost_sources[victim.body]), 2)
        self.assertAlmostEqual(run.match.combat.vitals_of(target).total, 200. - sum(counts) * 2.75)

    def test_ice_wall_blocks_only_the_occluded_spray_frost_source(self):
        run, first, second, victim, target = self.scene(victim_mei=True)
        fight = run.match.combat
        x, y, z = victim.mover.position
        fight.walls.place_player(second, (x + 1., y, z - 1.))
        self.both_spray(run, first, second, target, frames=20)
        first_key, second_key = (first.body, 0), (second.body, 0)
        entries = fight.frost_sources[victim.body]
        initial_first, initial_second = entries[first_key], entries[second_key]

        wall = wall_fixtures.WallTests()
        aim = fixtures.aim(fight._eye(victim), (x, y, z - 2.5))
        wall.tap(run, victim, 16, aim)
        wall.tap(run, victim, 1, aim)
        self.assertEqual(len(fight.walls.pieces), 5)
        self.both_spray(run, first, second, target, frames=20)
        entries = fight.frost_sources[victim.body]
        self.assertEqual(entries[first_key], initial_first)
        self.assertGreater(entries[second_key][0][0], initial_second[0][0])
        self.assertEqual(fight.frost(victim)[0], entries[second_key][0][0])

        wall.tap(run, victim, 16, aim)
        self.assertFalse(fight.walls.pieces)
        self.both_spray(run, first, second, target, frames=16)
        entries = fight.frost_sources[victim.body]
        self.assertGreater(entries[first_key][0][0], initial_first[0][0])

    def test_explicit_stacking_setting_freezes_with_same_two_actual_sprays(self):
        run, first, second, victim, target = self.scene()
        for source in (first, second):
            source.body_script.component.set(mei_frost.STACKING, True)
        self.both_spray(run, first, second, target)
        self.assertTrue(run.match.combat.frozen(victim))
        sample = run.match.combat.frost(victim)
        self.assertEqual(sample[2] - sample[1], 1300)
        for _ in range(5):
            self.hit(run, second, target)
            run.step({})
        self.assertEqual(run.match.combat.frost(victim)[2], sample[2])

    def test_actual_spray_and_own_blizzard_use_separate_frost_channels(self):
        for stacking in (False, True):
            with self.subTest(stacking=stacking):
                blizzard = blizzard_fixtures.BlizzardTests()
                run, owner, victim, target = blizzard.scene()
                fight = run.match.combat
                owner.body_script.component.set(mei_frost.STACKING, stacking)
                blizzard.launch(run, owner)
                area = blizzard.landed(run)
                while run.frame * 16 < area.starts + 800:
                    run.step({})
                self.assertEqual(set(fight.frost_sources[victim.body]), {(owner.body, area.entity)})
                yaw, pitch = fixtures.aim(
                    fight._eye(owner), (target.feet[0], target.feet[1] + .9, target.feet[2]),
                )
                for _ in range(45):
                    run.step({owner: (yaw, pitch, 1)})
                entries = fight.frost_sources[victim.body]
                self.assertEqual(set(entries), {(owner.body, 0), (owner.body, area.entity)})
                progress = [sample[0] for sample, _ in entries.values()]
                self.assertLess(max(progress), 1.)
                self.assertGreaterEqual(sum(progress), 1.)
                self.assertAlmostEqual(fight.frost(victim)[0], 1. if stacking else max(progress))
                self.assertEqual(fight.frozen(victim), stacking)
                if stacking:
                    sample = fight.frost(victim)
                    self.assertEqual(sample[2] - sample[1], 1300)
                    health = fight.vitals_of(target).total
                    for _ in range(10):
                        run.step({owner: (yaw, pitch, 1)})
                    self.assertLess(fight.vitals_of(target).total, health)
                    self.assertEqual(fight.frost(victim)[2], sample[2])

    def overlapping_blizzards(self, *, stacking=False, victim_mei=False):
        run, first, second, victim, target = self.scene(victim_mei=victim_mei)
        fight = run.match.combat
        run.match.game_map = dataclasses.replace(run.match.game_map, free_for_all=False)
        first.team, second.team, victim.team = 0, 0, 1
        commands = {}
        for source in (first, second):
            source.body_script.component.set(mei_frost.STACKING, stacking)
            source.body_script.set_ult_charge(1610.)
            commands[source] = (*fixtures.aim(fight._eye(source), tuple(target.feet)), 32)
        run.step(commands)
        for _ in range(150):
            run.step({})
            if len(fight.frost_sources.get(victim.body, ())) == 2:
                break
        return run, first, second, victim, target

    def test_two_actual_blizzards_compose_independent_area_frost(self):
        for stacking in (False, True):
            with self.subTest(stacking=stacking):
                run, first, second, victim, target = self.overlapping_blizzards(stacking=stacking)
                fight = run.match.combat
                self.assertEqual(len(fight.blizzard.areas), 2)
                areas = list(fight.blizzard.areas.values())
                self.assertEqual({area.body for area in areas}, {first.body, second.body})
                samples = fight.frost_sources[victim.body]
                self.assertEqual(set(samples), {(area.body, area.entity) for area in areas})
                self.assertEqual([sample[0] for sample, _ in samples.values()], [.5, .5])
                sample = fight.frost(victim)
                self.assertAlmostEqual(sample[0], 1. if stacking else .5)
                self.assertEqual(fight.frozen(victim), stacking)
                if stacking:
                    self.assertEqual(sample[2] - sample[1], 1300)
                self.assertTrue(all(source.client.messages(combat.HIT) for source in (first, second)))
                vitals = fight.vitals_of(target)
                self.assertLess(vitals.total, 200.)
                retired = next(area for area in areas if area.body == first.body)
                live = next(area for area in areas if area.body == second.body)
                health = vitals.total
                retired_hits = first.client.messages(combat.HIT)
                live_hits = len(second.client.messages(combat.HIT))
                self.assertTrue(run.match.switch_hero(first, victim.hero))
                self.assertEqual(set(fight.blizzard.areas), {live.entity})
                if stacking:
                    deadline = sample[2]
                    while run.frame * 16 < deadline:
                        self.assertTrue(fight.frozen(victim))
                        self.assertEqual(fight.frost(victim)[2], deadline)
                        run.step({})
                    self.assertFalse(fight.frozen(victim))
                    for _ in range(15):
                        run.step({})
                    self.assertFalse(fight.frozen(victim))
                    self.assertAlmostEqual(fight.frost(victim)[0], .5)
                else:
                    for _ in range(75):
                        run.step({})
                    self.assertGreater(fight.frost(victim)[0], .5)
                self.assertNotIn((retired.body, retired.entity), fight.frost_sources[victim.body])
                self.assertEqual(set(fight.frost_sources[victim.body]), {(live.body, live.entity)})
                self.assertLess(vitals.total, health)
                self.assertEqual(first.client.messages(combat.HIT), retired_hits)
                self.assertGreater(len(second.client.messages(combat.HIT)), live_hits)

    def test_cryo_cleanses_both_actual_blizzards_and_restores_fresh_samples(self):
        run, first, second, victim, target = self.overlapping_blizzards(victim_mei=True)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        self.assertLess(vitals.total, 250.)
        for _ in range(15):
            run.step({})
        old = fight.frost_sources[victim.body]
        self.assertEqual(len(old), 2)
        self.assertTrue(all(sample[0] > .5 for sample, _ in old.values()))
        run.step({victim: (0, 0, 8)})
        self.assertTrue(mei.protected(victim))
        self.assertNotIn(victim.body, fight.frost_sources)
        self.assertIsNone(fight.frost(victim))
        hits = [len(source.client.messages(combat.HIT)) for source in (first, second)]
        for _ in range(75):
            run.step({})
        self.assertEqual(vitals.total, 250.)
        self.assertNotIn(victim.body, fight.frost_sources)
        self.assertEqual([len(source.client.messages(combat.HIT)) for source in (first, second)], hits)

        run.step({victim: (0, 0, 2)})
        self.assertFalse(mei.protected(victim))
        for _ in range(20):
            run.step({})
            if len(fight.frost_sources.get(victim.body, ())) == 2:
                break
        entries = fight.frost_sources[victim.body]
        self.assertEqual(set(entries), set(old))
        self.assertEqual([sample[0] for sample, _ in entries.values()], [.5, .5])
        self.assertFalse(fight.frozen(victim))
        self.assertLess(vitals.total, 250.)

    def test_expired_strong_source_drops_to_fresh_weaker_source(self):
        run, first, second, victim, target = self.scene()
        self.hit(run, first, target, 10)
        self.assertAlmostEqual(run.match.combat.frost(victim)[0], .4565, places=5)
        for _ in range(60):
            run.step({})
        self.hit(run, second, target)
        for _ in range(4):
            run.step({})
        self.assertAlmostEqual(run.match.combat.frost(victim)[0], .2)
        entries = run.match.combat.frost_sources[victim.body]
        self.assertNotIn((first.body, 0), entries)
        self.assertIn((second.body, 0), entries)
        self.assertAlmostEqual(victim.body_script.component.entity_var(2427).value(), .2)

    def test_completed_freeze_discards_all_old_sources_before_next_hit(self):
        run, first, second, victim, target = self.scene()
        self.hit(run, first, target, 20)
        self.hit(run, second, target, 20)
        self.assertFalse(run.match.combat.frozen(victim))
        self.hit(run, first, target, 10)
        sample = run.match.combat.frost(victim)
        self.assertTrue(sample[2])
        while run.frame * 16 <= sample[2]:
            run.step({})
        self.assertIsNone(run.match.combat.frost(victim))
        self.assertNotIn(victim.body, run.match.combat.frost_sources)
        self.hit(run, second, target)
        self.assertAlmostEqual(run.match.combat.frost(victim)[0], .2)

    def test_explicit_source_minimum_rate_and_duration_are_separate_inputs(self):
        run, first, _second, victim, target = self.scene()
        component = first.body_script.component
        component.set(mei_frost.MINIMUM, .3)
        component.set(mei_frost.RATE, 2.)
        component.set(mei_frost.DURATION, .5)
        self.hit(run, first, target)
        self.assertAlmostEqual(run.match.combat.frost(victim)[0], .3)
        self.hit(run, first, target)
        self.assertAlmostEqual(run.match.combat.frost(victim)[0], .357)
        for _ in range(20):
            if run.match.combat.frozen(victim):
                break
            self.hit(run, first, target)
        sample = run.match.combat.frost(victim)
        self.assertEqual(sample[2] - sample[1], 650)

    def test_zero_source_rate_stays_at_minimum_while_actual_damage_continues(self):
        run, first, _second, victim, target = self.scene()
        first.body_script.component.set(mei_frost.MINIMUM, .3)
        first.body_script.component.set(mei_frost.RATE, 0.)
        self.hit(run, first, target, 35)
        self.assertAlmostEqual(run.match.combat.frost(victim)[0], .3)
        self.assertFalse(run.match.combat.frozen(victim))
        self.assertAlmostEqual(run.match.combat.vitals_of(target).total, 200. - 35 * 2.75)

    def test_held_cryo_activates_once_on_thaw_and_released_input_does_not_replay(self):
        for held in (False, True):
            with self.subTest(held=held):
                run, attacker, _, victim, target = self.scene(victim_mei=True)
                fight = run.match.combat
                sample = frost_fixtures.FrostTests().freeze(run, attacker, target)
                cryo = mei.cryo_instance(victim)
                vitals = fight.vitals_of(target)
                health = vitals.total
                for frame in range(100):
                    run.step({victim: (0, 0, 8 if held or frame < 10 else 0)})
                    if not fight.frozen(victim):
                        break
                    self.assertFalse(mei.protected(victim))
                    state = cryo.states.get(6)
                    self.assertEqual(state.counter if state else 0, 0)
                    self.assertLessEqual(vitals.total, health)
                    health = vitals.total
                self.assertFalse(fight.frozen(victim))
                self.assertGreaterEqual(run.frame * 16, sample[2])
                self.assertEqual(mei.protected(victim), held)
                if not held:
                    for _ in range(10):
                        run.step({})
                    state = cryo.states.get(6)
                    self.assertEqual(state.counter if state else 0, 0)
                    run.step({victim: (0, 0, 8)})
                self.assertTrue(mei.protected(victim))
                self.assertEqual(cryo.states[6].counter, 1)
                health = vitals.total
                for _ in range(20):
                    run.step({victim: (0, 0, 8)})
                self.assertGreater(vitals.total, health)
                self.assertEqual(cryo.states[6].counter, 1)
                self.assertIsNone(fight.frost(victim))

    def test_cryo_and_target_body_replacement_clear_all_source_samples(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                run, first, second, victim, target = self.scene(victim_mei=True)
                self.hit(run, first, target, 5)
                self.hit(run, second, target, 5)
                old = victim.body
                if replace:
                    run.match.switch_hero(victim, victim.hero)
                else:
                    run.step({victim: (0, 0, 8)})
                    self.assertTrue(mei.protected(victim))
                self.assertNotIn(old, run.match.combat.frost_sources)
                self.assertIsNone(run.match.combat.frost(victim))

    def test_source_body_replacement_does_not_inherit_old_progress(self):
        run, first, _second, victim, target = self.scene()
        self.hit(run, first, target, 10)
        old = first.body
        for _ in range(30):
            run.step({})
        run.match.switch_hero(first, first.hero)
        self.assertNotEqual(first.body, old)
        self.hit(run, first, target)
        entries = run.match.combat.frost_sources[victim.body]
        self.assertIn((old, 0), entries)
        self.assertIn((first.body, 0), entries)
        self.assertAlmostEqual(run.match.combat.frost(victim)[0], .4565, places=5)
        for _ in range(40):
            run.step({})
        self.assertNotIn((old, 0), entries)
        self.assertAlmostEqual(run.match.combat.frost(victim)[0], .2)

    def test_mixed_stacking_policy_has_explicit_order_independent_composition(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                run, first, second, victim, target = self.scene()
                second.body_script.component.set(mei_frost.STACKING, True)
                sources = ((second, 3), (first, 5)) if reverse else ((first, 5), (second, 3))
                for source, count in sources:
                    self.hit(run, source, target, count)
                self.assertAlmostEqual(run.match.combat.frost(victim)[0], .571, places=5)

    def test_projected_shield_cleanses_source_storage_as_well_as_visible_frost(self):
        import test_mei_shields as shields

        test = shields.ShieldTests()
        run, first, zarya, victim = test.scene()
        target = test.target(run, victim)
        self.hit(run, first, target, 5)
        self.assertIn(victim.body, run.match.combat.frost_sources)
        test.cast(run, zarya, victim)
        self.assertNotIn(victim.body, run.match.combat.frost_sources)
        self.assertIsNone(run.match.combat.frost(victim))

    def test_passive_storage_observes_source_progress_and_stacking_policy(self):
        import test_server_snapshot as snapshots

        from ow174.game.server_snapshot import snapshot_digest

        test = snapshots.ServerSnapshotTests()
        session, _peers, players = test.scene()
        owner = players[0]
        fight = owner.match.combat
        at = owner.match.tick * 16
        sample = (.2, at, 0, 0, 0)
        fight.frost_hits[owner.body] = sample
        entries = {(owner.body, 0): (sample, False)}
        fight.frost_sources[owner.body] = entries
        baseline = snapshot_digest(test.capture(session))
        entries[(owner.body, 0)] = (sample, True)
        stacked = snapshot_digest(test.capture(session))
        self.assertNotEqual(baseline, stacked)
        entries[(owner.body, 0)] = ((.4, at, 0, 0, 0), True)
        progressed = snapshot_digest(test.capture(session))
        self.assertNotEqual(stacked, progressed)
        entries[(owner.body, 1)] = entries.pop((owner.body, 0))
        self.assertNotEqual(progressed, snapshot_digest(test.capture(session)))


if __name__ == '__main__':
    unittest.main()
