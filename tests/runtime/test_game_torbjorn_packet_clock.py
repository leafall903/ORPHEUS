"""Compare the same production commands under different packet partitions."""

import os
import unittest

import test_game_torbjorn as fixtures

from ow174.game.collision import World
from ow174.game.commands import Command


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"), "needs prepared Torbjorn bundle")
class TorbjornPacketClockTests(unittest.TestCase):
    def replay(self, kind, batch, *, grounded=False, distance=1000, finish_offset=54):
        run, owner, _ = fixtures.TorbjornProjectileTests().arena(distance)
        fight = run.match.combat
        if kind == "molten":
            owner.body_script.set_ult_charge(10000)
            run.step({owner: (0, 4000, 32)})
            for _ in range(45):
                run.step({})
        start, event, finish = run.frame, run.frame + 6, run.frame + finish_offset
        owner.body_script.world_frame_anchor = start, start
        owner.body_script.create_arrived = True
        if not grounded:
            run.match.collision_world = World([])
        launches = []
        button = {"primary": 1, "secondary": 2, "turret": 8, "molten": 1}[kind]
        for begin in range(start + 1, finish + 1, batch):
            end = min(begin + batch - 1, finish)
            run.match.update(end * .016, end, frames=False)
            owner.take_commands([
                Command(frame, yaw=0, pitch=4000 if grounded else 0,
                        buttons=button if frame == event else 0)
                for frame in range(begin, end + 1)
            ])
            for rocket in fight.rockets:
                if all(rocket is not previous for previous in launches):
                    launches.append(rocket)
        return fight, launches

    def test_packet_partition_preserves_projectile_age_pose_and_launch_epoch(self):
        for kind, count in (("primary", 1), ("secondary", 10), ("turret", 1), ("molten", 1)):
            _, reference = self.replay(kind, 1)
            self.assertEqual(len(reference), count)
            for batch in (8, 32):
                with self.subTest(kind=kind, batch=batch):
                    _, actual = self.replay(kind, batch)
                    self.assertEqual(len(actual), count)
                    for expected, observed in zip(reference, actual, strict=True):
                        self.assertEqual(observed.launch_tick, expected.launch_tick)
                        self.assertEqual(observed.started_tick, expected.started_tick)
                        self.assertEqual(observed.last_tick, expected.last_tick)
                        self.assertAlmostEqual(observed.flown, expected.flown, places=8)
                        self.assertAlmostEqual(observed.left, expected.left, places=8)
                        for before, after in zip(expected.position, observed.position, strict=True):
                            self.assertAlmostEqual(after, before, places=8)

    def test_packet_partition_preserves_molten_landing_birth_and_effect_phase(self):
        fight, _ = self.replay("molten", 1, grounded=True)
        expected, = fight.molten_pools.values()
        for batch in (8, 32):
            with self.subTest(batch=batch):
                fight, _ = self.replay("molten", batch, grounded=True)
                actual, = fight.molten_pools.values()
                self.assertAlmostEqual(actual.born, expected.born, places=8)
                self.assertEqual(actual.position, expected.position)
                self.assertEqual(actual.phase, expected.phase)
                self.assertEqual(actual.script.frame, expected.script.frame)

    def test_packet_partition_preserves_registered_health_loss_and_owner_hits(self):
        from ow174.game import combat

        for kind, distance, damage, hits in (("primary", 5, 70, 1), ("secondary", 1.5, 125, 10)):
            for batch in (1, 8, 32):
                with self.subTest(kind=kind, batch=batch):
                    fight, _ = self.replay(kind, batch, distance=distance)
                    owner, victim = fight.match.players
                    target = next(target for target in fight._targets() if target.owner is victim)
                    self.assertAlmostEqual(200 - fight.vitals_of(target).total, damage, places=6)
                    self.assertEqual(len(owner.client.messages(combat.HIT)), hits)

    def test_packet_partition_preserves_turret_actor_and_vm_birth(self):
        from ow174.game import hero_torbjorn
        from ow174.game.script import expr

        fight, _ = self.replay("turret", 1, grounded=True)
        expected, = fight.turrets.values()
        expected_vm = expected.actor.body_script.component
        expected_instance = next(instance for instance in expected_vm.instances.values()
                                 if instance.graph.index == 0x1688)
        expected_health = fight.vitals[expected.actor.entity].total
        for batch in (8, 32):
            with self.subTest(batch=batch):
                fight, flights = self.replay("turret", batch, grounded=True)
                actual, = fight.turrets.values()
                script = actual.actor.body_script
                instance = next(instance for instance in script.component.instances.values()
                                if instance.graph.index == 0x1688)
                self.assertAlmostEqual(actual.spawned_at, expected.spawned_at, places=8)
                self.assertAlmostEqual(actual.build_end, expected.build_end, places=8)
                self.assertEqual(instance.initialized_frame, expected_instance.initialized_frame)
                self.assertEqual(script.component.frame, expected_vm.frame)
                self.assertAlmostEqual(fight.vitals[actual.actor.entity].total, expected_health, places=6)
                self.assertEqual(actual.actor.body, hero_torbjorn.TURRET)
                self.assertIs(actual.actor.combat_owner, actual.owner)
                self.assertEqual(script.component.entity, actual.actor.entity)
                published = actual.owner.body_script.component.entity_var(679).value()
                self.assertEqual(published, expr.Entity(actual.actor.entity))
                self.assertEqual(len(fight.match.bots), 1)
                self.assertTrue(all(rocket.shot not in fight.projectiles.shots for rocket in flights))
                finished = round(actual.build_end / .016) + 2
                fight.match.update(finished * .016, finished, frames=False)
                self.assertEqual(fight.vitals[actual.actor.entity].total, 250)
                self.assertIs(fight.turrets[actual.owner.entity], actual)

    def test_npc_birth_source_and_hero_target_options_coexist(self):
        from ow174.game import hero_torbjorn, npc

        fight, _ = self.replay("turret", 1, grounded=True)
        turret, = fight.turrets.values()
        born_tick = round(turret.spawned_at / .016)
        actor = npc.spawn(
            fight.match, npc.Definition(hero_torbjorn.HERO, hero_torbjorn.TURRET, "Joint options", 250),
            turret.actor.position, team=turret.owner.team, born_tick=born_tick,
            source_entity=turret.owner.body, hero_target=True,
        )
        self.assertEqual(actor.body_script.frame, born_tick)
        instance = next(instance for instance in actor.body_script.component.instances.values()
                        if instance.graph.index == 0x1688)
        self.assertEqual(instance.initialized_frame, born_tick)
        self.assertEqual(instance.source_entity(), turret.owner.body)
        self.assertTrue(actor.spawner["manual_hero_target"])

    def test_packet_partition_preserves_primary_impact_mark_deadline(self):
        fight, _ = self.replay("primary", 1, distance=5, finish_offset=36)
        expected, = fight.torbjorn_marks.values()
        self.assertLess(expected.born, fight.now)
        for batch in (8, 32):
            with self.subTest(batch=batch):
                fight, _ = self.replay("primary", batch, distance=5, finish_offset=36)
                actual, = fight.torbjorn_marks.values()
                self.assertAlmostEqual(actual.born, expected.born, places=8)
                self.assertAlmostEqual(actual.expires, expected.expires, places=8)
