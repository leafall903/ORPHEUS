"""Actual Ana attacks and Tracer Recall in the isolated match."""

import unittest

import test_game_ana as fixture
from test_game_combat import aim
import test_game_ana_orisa_barrier as placement

from ow174.game import stats, tracer_recall
from ow174.game.commands import Command
from ow174.game.content import heroes


class AnaTracerRecallTests(unittest.TestCase):
    def arena(self, distance=5):
        run, ana, tracer, _ = fixture.AnaRifleTests().setup_fight(enemy=True, distance=distance)
        self.assertTrue(run.match.switch_hero(tracer, heroes()[tracer_recall.HERO]))
        x, y, z = ana.mover.position
        placement.AnaOrisaBarrierTests.place(tracer, (x, y, z + distance))
        for _ in range(200):
            run.step({})
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is tracer)
        return run, ana, tracer, fight.vitals_of(target)

    @staticmethod
    def direction(fight, ana, tracer):
        x, y, z = tracer.mover.position
        return aim(fight._eye(ana), (x, y + 1, z))

    @staticmethod
    def step_without_tracer(run, ana, direction, buttons=0):
        run.frame += 1
        run.match.update(run.frame * .016, run.frame, frames=False)
        ana.take_commands([Command(run.frame, yaw=direction[0], pitch=direction[1], buttons=buttons)])

    def test_recall_restores_historical_pose_and_health_without_tracer_commands(self):
        run, ana, tracer, vitals = self.arena()
        fight = run.match.combat
        origin = tuple(tracer.mover.position)
        for _ in range(32):
            run.frame += 1
            run.match.update(run.frame * .016, run.frame, frames=False)
            tracer.take_commands([Command(run.frame, forward=127)])
        for _ in range(25):
            run.step({})
        self.assertGreater(tracer.mover.position[2], origin[2] + 1)
        entry = next(target for target in fight._targets() if target.owner is tracer)
        fixture.AnaRifleTests().shoot(run, ana, entry, scoped=True)
        self.assertEqual(vitals.total, 80)
        run.step({tracer: (0, 0, 16)})
        entry_frame = tracer.body_script.frame
        direction = self.direction(fight, ana, tracer)
        for _ in range(86):
            self.step_without_tracer(run, ana, direction)
        self.assertEqual(tracer.body_script.frame, entry_frame)
        self.assertEqual(vitals.total, 150)
        self.assertAlmostEqual(tracer.mover.position[2], origin[2], places=2)
        self.assertFalse(fight.recalls.locked(tracer))
        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 70)
        self.assertEqual(tracer.stats.value(stats.HEALING), 0)

    def test_skipped_world_ticks_keep_pose_samples_ordered_across_recall(self):
        run, _, tracer, _ = self.arena()
        fight = run.match.combat
        origin = tuple(tracer.mover.position)
        x, y, z = origin
        placement.AnaOrisaBarrierTests.place(tracer, (x, y, z + 2))
        for _ in range(30):
            run.step({})
        before = tuple(tracer.mover.position)
        run.step({tracer: (0, 0, 16)})
        for _ in range(48):
            run.step({})
        run.frame += 50
        run.match.update(run.frame * .016, run.frame, frames=False)
        target = next(target for target in fight._targets() if target.owner is tracer)
        self.assertAlmostEqual(target.feet[2], origin[2], places=2)
        history = fight.history[tracer.body]
        ticks = [sample[0] for sample in history]
        self.assertEqual(ticks, sorted(ticks))
        self.assertEqual(ticks[-1], run.frame)
        exit_tick = ticks[-2]
        self.assertAlmostEqual(fight.seen(target, exit_tick - .5).feet[2], before[2], places=2)
        self.assertAlmostEqual(fight.seen(target, exit_tick).feet[2], origin[2], places=2)

    def test_actual_dart_passes_during_rewind_and_hits_after_expiry(self):
        for elapsed, damage, asleep in ((28, 0, False), (85, 5, True)):
            with self.subTest(elapsed=elapsed):
                run, ana, tracer, vitals = self.arena()
                fight = run.match.combat
                direction = self.direction(fight, ana, tracer)
                run.step({tracer: (0, 0, 16)})
                for _ in range(elapsed):
                    run.step({})
                self.assertEqual(fight.recalls.active(tracer), not asleep)
                run.step({ana: (*direction, 8)})
                for _ in range(68):
                    run.step({})
                self.assertEqual(vitals.total, 150 - damage)
                self.assertEqual(fight.is_asleep(tracer), asleep)

    def test_actual_rifles_grenade_and_melee_do_no_damage_during_rewind(self):
        for button, held, distance in ((1, 0, 3), (1, 2, 3), (16, 0, 3), (0x800, 0, 1.5)):
            with self.subTest(button=button, scoped=bool(held)):
                run, ana, tracer, vitals = self.arena(distance=distance)
                fight = run.match.combat
                direction = self.direction(fight, ana, tracer)
                for _ in range(35 if held else 0):
                    run.step({ana: (*direction, held)})
                run.step({tracer: (0, 0, 16), ana: (*direction, held)})
                for _ in range(27):
                    run.step({ana: (*direction, held)})
                self.assertTrue(fight.recalls.active(tracer))
                run.step({ana: (*direction, held | button)})
                for _ in range(42):
                    run.step({ana: (*direction, held)})
                self.assertTrue(fight.recalls.active(tracer))
                self.assertEqual(vitals.total, 150)
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)

    def test_recall_cleanses_actual_grenade_antiheal_and_restores_health(self):
        run, ana, tracer, vitals = self.arena(distance=3)
        fight = run.match.combat
        direction = self.direction(fight, ana, tracer)
        run.step({ana: (*direction, 16)})
        for _ in range(60):
            run.step({})
            if vitals.healing_block_until > fight.now:
                break
        self.assertEqual(vitals.total, 100)
        self.assertGreater(vitals.healing_block_until, fight.now)
        run.step({tracer: (0, 0, 16)})
        for _ in range(30):
            run.step({})
        self.assertTrue(fight.recalls.active(tracer))
        self.assertEqual(vitals.healing_block_until, 0)
        for _ in range(60):
            run.step({})
        self.assertEqual(vitals.total, 150)

    def test_actual_sleep_prevents_recall_before_and_during_windup(self):
        for press_after_dart in (False, True):
            with self.subTest(press_after_dart=press_after_dart):
                run, ana, tracer, vitals = self.arena(distance=3)
                fight = run.match.combat
                direction = self.direction(fight, ana, tracer)
                if not press_after_dart:
                    run.step({ana: (*direction, 8)})
                    for _ in range(13):
                        run.step({})
                    run.step({tracer: (0, 0, 16)})
                else:
                    run.step({ana: (*direction, 8)})
                    for _ in range(42):
                        run.step({})
                    self.assertTrue(fight.is_asleep(tracer))
                    run.step({tracer: (0, 0, 16)})
                for _ in range(96):
                    run.step({})
                self.assertTrue(fight.is_asleep(tracer))
                self.assertFalse(fight.recalls.locked(tracer))
                self.assertEqual(vitals.total, 145)

    def test_recall_reentry_respects_cooldown_after_long_input_gaps(self):
        for elapsed, ready in ((700, False), (1000, True)):
            with self.subTest(elapsed=elapsed):
                run, ana, tracer, _ = self.arena()
                fight = run.match.combat
                direction = self.direction(fight, ana, tracer)
                run.step({tracer: (0, 0, 16)})
                for _ in range(elapsed):
                    self.step_without_tracer(run, ana, direction)
                self.assertFalse(fight.recalls.locked(tracer))
                run.step({tracer: (0, 0, 16)})
                self.assertEqual(fight.recalls.locked(tracer), ready)

    def test_lethal_windup_contact_and_body_replacement_retire_recall(self):
        for replace_body in (False, True):
            with self.subTest(replace_body=replace_body):
                run, ana, tracer, vitals = self.arena(distance=1.5)
                fight = run.match.combat
                direction = self.direction(fight, ana, tracer)
                old_body = tracer.body
                if not replace_body:
                    vitals.damage(149)
                run.step({tracer: (0, 0, 16)})
                self.assertTrue(fight.recalls.locked(tracer))
                if replace_body:
                    for _ in range(28):
                        run.step({})
                    self.assertTrue(fight.recalls.active(tracer))
                    self.assertTrue(run.match.switch_hero(tracer, heroes()[fixture.ANA]))
                else:
                    run.step({ana: (*direction, 0x800)})
                for _ in range(90):
                    run.step({})
                self.assertNotIn(old_body, fight.recalls.phases)
                self.assertNotIn(old_body, fight.recalls.history)
                if replace_body:
                    entry = next(target for target in fight._targets() if target.owner is tracer)
                    self.assertEqual(fight.vitals_of(entry).total, 200)
                else:
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.total, 0)

    def test_live_rewind_and_history_enter_the_server_snapshot(self):
        from dataclasses import replace

        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene()
        tracer, = players
        peer, = peers
        peer.message(21616, stored.pick(tracer.entity, tracer_recall.HERO), now=100.8)
        session.tick(100.816)
        peer.commands(tracer.body_script.frame + 1, 4, 16)
        for _ in range(8):
            peer.commands(tracer.body_script.frame + 1, 4, 0)
        recalls = tracer.match.combat.recalls
        instance = next(i for i in tracer.body_script.component.instances.values()
                        if i.graph.index == tracer_recall.GRAPH)
        self.assertTrue(instance.states[0].active)
        phase = recalls.phases[tracer.body]
        before = stored.snapshot_digest(case.capture(session))
        phase.sample = replace(phase.sample, health=phase.sample.health - 1)
        self.assertNotEqual(before, stored.snapshot_digest(case.capture(session)))
