"""Ana command-to-damage and projectile wire delivery regressions."""

import unittest
from unittest.mock import patch

from ow174.game import combat, projectiles
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.world import OP_CREATE, OP_DESTROY, EntityUpdate
from tests.runtime import test_game_ana as ana_fixture
from tests.runtime.test_game_combat import aim
from tests.runtime.test_game_projectiles import bits_of, read_shot


class AnaProjectileDeliveryTests(unittest.TestCase):
    def test_contact_damage_status_and_retirement_match_across_packet_sizes(self):
        for button in (1, 8, 16):
            baseline = None
            for batch in (1, 8, 32):
                with self.subTest(button=button, batch=batch):
                    run, ana, target, entry = ana_fixture.AnaRifleTests().setup_fight(enemy=True, distance=30)
                    fight = run.match.combat
                    point = (entry.feet[0], entry.feet[1] + 1, entry.feet[2])
                    direction = (ana_fixture.grenade_aim(run, ana, point) if button == 16
                                 else aim(fight._eye(ana), point))
                    landings = []
                    original = fight._landed

                    def landed(rocket, **kwargs):
                        landings.append((fight.tick, rocket.last_tick, *rocket.position))
                        return original(rocket, **kwargs)

                    start = run.frame
                    with patch.object(fight, "_landed", side_effect=landed):
                        for first in range(1, 97, batch):
                            last = min(first + batch - 1, 96)
                            run.frame = start + last
                            run.match.update(run.frame * combat.TICK, run.frame)
                            ana.take_commands([
                                Command(start + offset, yaw=direction[0], pitch=direction[1],
                                        buttons=button if offset == 1 else 0)
                                for offset in range(first, last + 1)
                            ])
                    vitals = fight.vitals_of(entry)
                    self.assertEqual(vitals.total, {1: 130, 8: 195, 16: 140}[button])
                    self.assertTrue(ana.client.messages(combat.HIT))
                    self.assertEqual(fight.is_asleep(target), button == 8)
                    self.assertEqual(len(landings), 1)
                    values = (vitals.total, vitals.sleep_until, fight.healing.factor(entry), landings)
                    if baseline is None:
                        baseline = values
                    self.assertEqual(values, baseline)
                    self.assertEqual((fight.tick, fight.now), (run.frame, run.frame * combat.TICK))

    def test_birth_epoch_preserves_input_and_maps_different_clock_origins(self):
        for offset in (0, 7000):
            run, ana, _, _ = ana_fixture.AnaRifleTests().setup_fight(enemy=True, distance=1000)
            start = run.frame
            create = EntityUpdate(ana.body)
            run.match.start_body_script(ana, create, start_frame=start + offset)
            ana.client.queue_entities([create])
            script = ana.body_script
            self.assertIsNone(script.world_frame_anchor)
            self.assertEqual(script.ana_birth, (start, start + offset))
            digest = snapshot_digest(export_body_vm(script))
            script.ana_birth = (start + 1, start + offset)
            self.assertNotEqual(snapshot_digest(export_body_vm(script)), digest)
            script.ana_birth = (start, start + offset)
            fight = run.match.combat
            run.match.collision_world = World([])
            with patch.object(fight, "_commit_attack", wraps=fight._commit_attack) as admitted:
                for first in range(1, 129, 32):
                    run.frame = start + first + 31
                    run.match.update(run.frame * combat.TICK, run.frame)
                    ana.take_commands([Command(start + offset + i, buttons=8 if i == 33 else 0)
                                       for i in range(first, first + 32)])
            self.assertEqual(script.frame, start + offset + 128)
            self.assertEqual(script.ana_birth, (start, start + offset))
            self.assertIsNone(script.world_frame_anchor)
            self.assertEqual(len(admitted.call_args_list), 1)
            event_frame = admitted.call_args.args[1]
            rocket = admitted.call_args.args[2]
            self.assertEqual(rocket.started_tick, start + event_frame - (start + offset))

    def test_launch_clock_and_flight_do_not_restart_at_packet_admission(self):
        for button in (1, 8, 16):
            baseline = None
            for batch in (1, 8, 32):
                with self.subTest(button=button, batch=batch):
                    run, ana, _, _ = ana_fixture.AnaRifleTests().setup_fight(enemy=True, distance=1000)
                    fight = run.match.combat
                    run.match.collision_world = World([])
                    start = run.frame
                    with patch.object(fight, "_commit_attack", wraps=fight._commit_attack) as admitted:
                        for first in range(1, 97, batch):
                            last = min(first + batch - 1, 96)
                            run.frame = start + last
                            run.match.update(run.frame * combat.TICK, run.frame)
                            ana.take_commands([Command(start + offset, buttons=button if offset == 1 else 0)
                                               for offset in range(first, last + 1)])
                    self.assertEqual(len(admitted.call_args_list), 1)
                    rocket = admitted.call_args.args[2]
                    values = (rocket.started_tick, rocket.flown, rocket.left, *rocket.position)
                    if baseline is None:
                        baseline = values
                    self.assertEqual(values[0], baseline[0])
                    for actual, expected in zip(values[1:], baseline[1:]):
                        self.assertAlmostEqual(actual, expected, places=8)

    def fire(self, button, *, enemy=True, miss=False):
        run, ana, target, entry = ana_fixture.AnaRifleTests().setup_fight(enemy=enemy, distance=12)
        fight = run.match.combat
        vitals = fight.vitals_of(entry)
        if not enemy:
            vitals.damage(150)
        point = (entry.feet[0], entry.feet[1] + 1, entry.feet[2])
        direction = ana_fixture.grenade_aim(run, ana, point) if button == 16 else aim(fight._eye(ana), point)
        if miss:
            direction = (0, -16384)
        ana.client.entities.clear()
        target.client.entities.clear()
        run.step({ana: (*direction, button)})
        for _ in range(40):
            if fight.rockets:
                break
            run.step({ana: (*direction, 0)})
        rocket, = fight.rockets
        self.assertIsNotNone(rocket.shot)
        return run, ana, target, entry, rocket

    def test_dart_and_grenade_deliver_single_launch_impact_and_retirement(self):
        for button, damage in ((8, 5), (16, 60)):
            with self.subTest(button=button):
                run, ana, target, entry, rocket = self.fire(button)
                fight = run.match.combat
                shot = rocket.shot
                self.assertEqual(shot.authoritative, button == 16)
                self.assertEqual(len(shot.observers), 2 if button == 16 else 1)
                self.assertNotIn(shot, fight.projectiles.shots)
                for _ in range(110):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(fight.vitals_of(entry).total, 200 - damage)
                self.assertTrue(ana.client.messages(combat.HIT))
                self.assertEqual(fight.is_asleep(target), button == 8)
                if button == 16:
                    self.assertEqual(fight.healing.factor(entry), 0)
                for viewer in ((ana, target) if button == 16 else (target,)):
                    launches = [update for update in viewer.client.entities
                                if update.entity == ana.body and update.ch2 is not None]
                    self.assertEqual(len(launches), 1)
                    wire = bits_of(launches[0].ch2)
                    self.assertEqual((wire.bit(), wire.bit()), (1, 1))
                    self.assertEqual(wire.var_b(), shot.frame)
                    record = read_shot(wire)
                    self.assertEqual(len(record['ids']), 1)
                    entity, = record['ids']
                    self.assertEqual(record['state'], shot.state)
                    self.assertEqual(sum(update.entity == entity and update.op == OP_CREATE
                                         for update in viewer.client.entities), 1)
                    retired = [update for update in viewer.client.entities
                               if update.entity == entity and update.op == OP_DESTROY]
                    self.assertEqual(len(retired), 1)
                    self.assertIsNotNone(retired[0].ch2)
                if button == 8:
                    self.assertFalse(any(update.op == OP_CREATE
                                         and projectiles.SEEN_FIRST <= update.entity
                                         < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN
                                         for update in ana.client.entities))

    def test_ally_dart_is_harmless_and_grenade_heals_and_amplifies(self):
        for button in (8, 16):
            with self.subTest(button=button):
                run, ana, target, entry, rocket = self.fire(button, enemy=False)
                for _ in range(rocket.shot.bound + 80 if button == 8 else 110):
                    run.step({})
                fight = run.match.combat
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(fight.vitals_of(entry).total, 150 if button == 16 else 50)
                self.assertFalse(fight.is_asleep(target))
                self.assertFalse(ana.client.messages(combat.HIT))
                self.assertEqual(fight.healing.factor(entry), 1.5 if button == 16 else 1)

    def test_missed_rifle_dart_and_grenade_expire_without_damage_or_status(self):
        for button in (1, 8, 16):
            with self.subTest(button=button):
                run, ana, target, entry, rocket = self.fire(button, miss=True)
                fight = run.match.combat
                shot = rocket.shot
                for _ in range(shot.bound + projectiles.DELIVERY_GRACE_TICKS + 80):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertNotIn(shot, fight.projectiles.shots)
                self.assertEqual(fight.vitals_of(entry).total, 200)
                self.assertFalse(fight.is_asleep(target))
                self.assertEqual(fight.healing.factor(entry), 1)
                self.assertFalse(ana.client.messages(combat.HIT))
                for delivery in shot.observers:
                    entity, = delivery.record.ids
                    records = delivery.viewer.client.entities
                    self.assertEqual(sum(update.entity == entity and update.op == OP_CREATE
                                         for update in records), 1)
                    self.assertEqual(sum(update.entity == entity and update.op == OP_DESTROY
                                         for update in records), 1)
