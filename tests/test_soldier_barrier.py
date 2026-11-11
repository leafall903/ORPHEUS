"""Actual0095 shield input and original mesh against Soldier combat reports."""

import hashlib
import os
import unittest
from pathlib import Path

from test_game_combat import Run, aim, in_world, first_canister_sweep

from ow174.game import barrier_mesh, combat, defenses, projectiles, reinhardt_barrier, replay_manifest, stats
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes


class SoldierBarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("ORPHEUS_REINHARDT_COLLISION"):
            raise unittest.SkipTest("explicit original192-triangle barrier descriptor required")

    def scene(self, *, raised=True, facing=-32768, distance=6):
        run = Run(names=("Soldier", "Rein"))
        soldier, rein = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        run.match.switch_hero(rein, heroes()[reinhardt_barrier.HERO])
        soldier.mover.position[:] = [0, 0, 0]
        rein.mover.position[:] = [0, 0, distance]
        rein.team = 1 - soldier.team
        for player in run.players:
            player.mover.body = None
            player.body_script.ult_seconds = 0
        for _ in range(20):
            self.step(run, soldier, rein, facing=facing)
        if raised:
            for _ in range(5):
                self.step(run, soldier, rein, held=2, facing=facing)
            self.assertTrue(self.shield(run, rein).active)
        return run, soldier, rein

    def step(self, run, soldier, rein, buttons=0, *, held=2, facing=-32768, angles=(0, 0)):
        run.step({soldier: (*angles, buttons), rein: (facing, 0, held)})

    def shield(self, run, rein):
        return run.match.combat.barriers.states[rein.body]

    def instance(self, rein):
        return next(i for i in rein.body_script.component.instances.values() if i.graph.index == 0x95)

    def pool(self, run, owner):
        return run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is owner))

    def helix(self, run, soldier, rein, **kwargs):
        self.step(run, soldier, rein, 2, **kwargs)
        rocket, = run.match.combat.rockets
        for _ in range(160):
            self.step(run, soldier, rein, **kwargs)
            if rocket not in run.match.combat.rockets:
                break
        self.assertNotIn(rocket, run.match.combat.rockets)
        return rocket

    def stage_field_above_shield(self, run, soldier, rein):
        # The shelf supports Soldier's feet but ends behind the canister's offset.
        z = rein.mover.position[2] - 1.7
        back, edge = z - 1, z + .1
        run.match.collision_world = World([
            (-20, 0, -20, -20, 0, 20, 20, 0, 20),
            (-20, 0, -20, 20, 0, 20, 20, 0, -20),
            (.25, 3, back, .25, 3, edge, 1.75, 3, edge),
            (.25, 3, back, 1.75, 3, edge, 1.75, 3, back),
        ])
        soldier.mover.position[:] = [1, 3, z]
        soldier.mover.body = None

    def test_field_canister_meets_raised_mesh_without_spending_shield_or_losing_healing(self):
        distances = {}
        for raised in (False, True):
            with self.subTest(raised=raised):
                run, soldier, rein = self.scene(raised=raised, distance=4.5)
                self.stage_field_above_shield(run, soldier, rein)
                fight = run.match.combat
                ally = run.match.add_player(3, "Ally", soldier.hero.guid, soldier.team, False)
                in_world(run.match, ally)
                run.players.append(ally)
                self.assertTrue(run.match.switch_hero(ally, soldier.hero))
                ally.mover.position = [0, 0, 3.8]
                ally.mover.body = None
                pool = self.pool(run, ally)
                pool.damage(20)
                before = pool.total
                defender_health = self.pool(run, rein).total
                held = 2 if raised else 0
                self.step(run, soldier, rein, buttons=16, held=held)
                for _ in range(60):
                    self.step(run, soldier, rein, held=held)
                    if fight.rockets:
                        break
                canister, = fight.rockets
                field = canister.shot
                self.assertEqual(field.spawn, projectiles.FIELD_ENTITY)
                ray = first_canister_sweep(canister)
                mesh = fight.barriers.trace(soldier, *ray)
                if raised:
                    self.assertIsNotNone(mesh)
                    self.assertIs(mesh[1], rein)
                else:
                    self.assertIsNone(mesh)
                for _ in range(60):
                    self.step(run, soldier, rein, held=held)
                    if field.field_tick is not None and run.frame > field.field_tick:
                        break
                self.assertNotIn(canister, fight.rockets)
                self.assertIsNotNone(field.field_tick)
                distances[raised] = canister.distance
                if raised:
                    self.assertAlmostEqual(distances[raised], mesh[0], places=5)
                    self.assertTrue(self.shield(run, rein).active)
                self.assertEqual(self.shield(run, rein).current, 1600)
                self.assertEqual(self.pool(run, rein).total, defender_health)
                self.assertIs(field.player, soldier)
                self.assertEqual(field.body, soldier.body)
                self.assertAlmostEqual(pool.total - before, 35 * combat.TICK)
                self.assertFalse(soldier.client.messages(combat.HIT))
        self.assertLess(distances[True], distances[False])

    def test_rifle_helix_and_visor_damage_mesh_shield_without_body_hit_or_charge(self):
        for weapon, damage in (("rifle", 20), ("helix", 120), ("visor", 20)):
            with self.subTest(weapon=weapon):
                run, soldier, rein = self.scene()
                if weapon == "visor":
                    soldier.body_script.set_ult_charge(soldier.body_script.component.vars[338].value())
                    self.step(run, soldier, rein, 0x20)
                    for _ in range(80):
                        self.step(run, soldier, rein)
                before = self.shield(run, rein).current
                if weapon == "helix":
                    self.helix(run, soldier, rein)
                else:
                    self.step(run, soldier, rein, 1)
                self.assertEqual(before - self.shield(run, rein).current, damage)
                self.assertEqual(self.pool(run, rein).total, 500)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(soldier.client.messages(combat.HIT))
                self.assertEqual(self.instance(rein).find_var(0, 586).value(), before - damage)

    def test_actual_field_canister_contact_with_shield_preserves_deployment(self):
        results = []
        for raised in (True, False):
            with self.subTest(raised=raised):
                run, soldier, rein = self.scene(raised=raised)
                self.stage_field_above_shield(run, soldier, rein)
                fight = run.match.combat
                pool = self.pool(run, soldier)
                pool.damage(50)
                before = pool.total
                held = 2 if raised else 0
                self.step(run, soldier, rein, 16, held=held)
                for _ in range(45):
                    self.step(run, soldier, rein, held=held)
                    if fight.rockets:
                        break
                canister, = fight.rockets
                field = canister.shot
                self.assertEqual(field.spawn, projectiles.FIELD_ENTITY)
                launch_where = field.where
                contact = fight._barrier_contact(soldier, *first_canister_sweep(canister))
                if raised:
                    self.assertIsNotNone(contact)
                    distance, owner, service = contact
                    self.assertIs(service, fight.barriers)
                    self.assertIs(owner, rein)
                    shield = self.shield(run, rein)
                    shield_hp = shield.current
                    mesh_stop = distance
                else:
                    self.assertIsNone(contact)

                for _ in range(55):
                    self.step(run, soldier, rein, held=held)
                    if field.field_tick is not None and run.frame > field.field_tick:
                        break
                else:
                    self.fail("Field did not open and begin healing")
                self.assertNotIn(canister, fight.rockets)
                if raised:
                    self.assertAlmostEqual(canister.distance, mesh_stop)
                    self.assertTrue(shield.active)
                    self.assertEqual(shield.current, shield_hp)
                    self.assertEqual(field.where, launch_where)
                else:
                    self.assertAlmostEqual(field.where[1], 0)
                    self.assertGreater(field.where[2], launch_where[2])
                self.assertAlmostEqual(pool.total, before + 35 * combat.TICK)
                self.assertEqual(self.pool(run, rein).total, 500)
                self.assertFalse(soldier.client.messages(combat.HIT))
                results.append((canister.distance, field.field_tick - field.frame, field.where))
        if len(results) == 2:
            stopped, open_path = results
            self.assertLess(stopped[0], open_path[0])
            self.assertEqual(stopped[1], open_path[1])
            self.assertNotEqual(stopped[2], open_path[2])

    def test_rifle_visor_and_melee_check_barrier_field_at_the_viewed_pose(self):
        for weapon, button in (("rifle", 1), ("visor", 1), ("melee", 0x800)):
            for viewed, current in ((0, 30), (30, 0)):
                with self.subTest(weapon=weapon, viewed=viewed, current=current):
                    run, soldier, rein = self.scene(distance=2.4 if weapon == "melee" else 6)
                    if weapon == "visor":
                        soldier.body_script.set_ult_charge(soldier.body_script.component.vars[338].value())
                        self.step(run, soldier, rein, 0x20)
                        for _ in range(80):
                            self.step(run, soldier, rein)
                    rein.mover.position[0] = viewed
                    rein.mover.body = None
                    for _ in range(12):
                        self.step(run, soldier, rein)
                    self.assertTrue(self.shield(run, rein).active)
                    rein.mover.position[0] = current
                    rein.mover.body = None
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    soldier.take_commands([Command(run.frame, buttons=button, extra=128)])
                    shield_loss = (30 if weapon == "melee" else 20) if viewed == 0 else 0
                    body_loss = 25 if weapon == "melee" and viewed == 0 else 0
                    self.assertEqual(self.shield(run, rein).current, 1600 - shield_loss)
                    self.assertEqual(self.pool(run, rein).total, 500 - body_loss)
                    self.assertEqual(len(soldier.client.messages(combat.HIT)), int(body_loss > 0))

    def test_lowered_and_rear_body_exposure_take_normal_damage(self):
        for raised, facing in ((False, -32768), (True, 0)):
            with self.subTest(raised=raised, facing=facing):
                run, soldier, rein = self.scene(raised=raised, facing=facing)
                angles = aim(run.match.combat._eye(soldier), (0, .8, 6))
                self.step(run, soldier, rein, 1, held=2 if raised else 0, facing=facing, angles=angles)
                self.assertEqual(self.pool(run, rein).total, 485)
                self.assertEqual(self.shield(run, rein).current, 1600)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 15)

    def test_breaking_helix_does_not_spill_through_and_following_rifle_hits_body(self):
        run, soldier, rein = self.scene()
        # Exhaust by actual rifle input; do not replace native capacity for this witness.
        for _ in range(1200):
            rounds = soldier.body_script.component.instances[11].vars[53].value()
            self.step(run, soldier, rein, 1 if rounds else 4)
            if self.shield(run, rein).current == 100:
                break
        self.assertEqual(self.shield(run, rein).current, 100)
        for _ in range(40):
            self.step(run, soldier, rein)
        self.helix(run, soldier, rein)
        self.assertEqual(self.shield(run, rein).current, 0)
        self.assertFalse(self.shield(run, rein).active)
        self.assertEqual(self.pool(run, rein).total, 500)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
        for _ in range(40):
            self.step(run, soldier, rein)
        self.step(run, soldier, rein, 1, angles=aim(run.match.combat._eye(soldier), (0, .8, 6)))
        self.assertEqual(self.pool(run, rein).total, 485)
        deadline = self.shield(run, rein).broken_until
        while run.match.combat.now + combat.TICK < deadline:
            self.step(run, soldier, rein)
            self.assertFalse(self.shield(run, rein).active)
        for _ in range(4):
            self.step(run, soldier, rein)
        self.assertTrue(self.shield(run, rein).active)
        before = self.shield(run, rein).current
        self.step(run, soldier, rein, 1)
        self.assertEqual(self.shield(run, rein).current, before - 20)
        self.assertEqual(self.pool(run, rein).total, 485)

    def test_release_delays_resource_regeneration_and_raise_stops_it(self):
        run, soldier, rein = self.scene()
        self.helix(run, soldier, rein)
        self.assertEqual(self.shield(run, rein).current, 1480)
        self.step(run, soldier, rein, held=0)
        for _ in range(120):
            self.step(run, soldier, rein, held=0)
        self.assertEqual(self.shield(run, rein).current, 1480)
        for _ in range(25):
            self.step(run, soldier, rein, held=0)
        recovered = self.shield(run, rein).current
        self.assertGreater(recovered, 1530)
        self.assertLess(recovered, 1570)
        for _ in range(5):
            self.step(run, soldier, rein, held=2)
        before = self.shield(run, rein).current
        for _ in range(30):
            self.step(run, soldier, rein, held=2)
        self.assertTrue(self.shield(run, rein).active)
        self.assertEqual(self.shield(run, rein).current, before)

    def test_in_flight_helix_orders_reinhardt_release_without_commands(self):
        for held_at_launch in (False, True):
            for jump in (False, True):
                with self.subTest(held_at_launch=held_at_launch, jump=jump):
                    run, soldier, rein = self.scene(distance=2.0)
                    fight = run.match.combat
                    launch = run.frame + 1
                    run.match.update(launch * combat.TICK, launch)
                    if held_at_launch:
                        rein.take_commands([Command(launch, yaw=-32768, buttons=2)])
                    yaw, pitch = aim(fight._eye(soldier), (0, .8, 2))
                    soldier.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=2)])
                    self.assertEqual(len(fight.rockets), 1)

                    end = launch + 29
                    for tick in ((end,) if jump else range(launch + 1, end + 1)):
                        run.match.update(tick * combat.TICK, tick)
                    shield = self.shield(run, rein)
                    self.assertFalse(shield.active)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(shield.current, 1480 if held_at_launch else 1600)
                    damage = 0 if held_at_launch else 110
                    self.assertEqual(self.pool(run, rein).total, 500 - damage)
                    self.assertEqual(soldier.body_script.component.vars[342].value(), damage)

    def test_idle_reinhardt_release_starts_regeneration_even_without_projectile(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, soldier, rein = self.scene()
                self.helix(run, soldier, rein)
                shield = self.shield(run, rein)
                self.assertEqual(shield.current, 1480)
                start = run.frame
                previous = start
                for offset, health in ((125, 1480), (145, 1537.6), (300, 1600)):
                    end = start + offset
                    for tick in ((end,) if jump else range(previous + 1, end + 1)):
                        run.match.update(tick * combat.TICK, tick)
                    self.assertFalse(shield.active)
                    self.assertAlmostEqual(shield.regen_after, (start + 2) * combat.TICK + 2)
                    self.assertAlmostEqual(shield.current, health, places=2)
                    previous = end

    def test_melee_damages_shield_and_penetrates_to_body(self):
        run, soldier, rein = self.scene(distance=2.4)
        self.step(run, soldier, rein, 0x800)
        for _ in range(30):
            self.step(run, soldier, rein)
        self.assertEqual(self.shield(run, rein).current, 1570)
        self.assertEqual(self.pool(run, rein).total, 475)

    def test_earlier_wall_wins_before_barrier(self):
        run, soldier, rein = self.scene()
        run.match.collision_world = World([
            (-20, 0, -20, -20, 0, 20, 20, 0, 20), (-20, 0, -20, 20, 0, 20, 20, 0, -20),
            (-10, -10, 1, 10, -10, 1, 10, 10, 1), (-10, -10, 1, 10, 10, 1, -10, 10, 1),
        ])
        self.step(run, soldier, rein, 1)
        self.helix(run, soldier, rein)
        self.assertEqual(self.shield(run, rein).current, 1600)
        self.assertEqual(self.pool(run, rein).total, 500)

    def test_original_mesh_width_and_side_miss_are_used(self):
        run, soldier, rein = self.scene()
        geometry = run.match.combat.barriers.geometry
        self.assertEqual(len(geometry.triangles), 192)
        self.assertEqual(geometry.content_sha256, barrier_mesh.DESCRIPTOR_SHA256)
        expected = hashlib.sha256(Path(os.environ["ORPHEUS_REINHARDT_COLLISION"]).read_bytes()).hexdigest()
        config = replay_manifest.runtime_configuration(quanta_us=16000, run_options={})
        self.assertEqual(config["ow174.game.barrier_mesh.DESCRIPTOR_SHA256"], expected)
        angles = aim(run.match.combat._eye(soldier), (2, 1.55, 6))
        self.step(run, soldier, rein, 1, angles=angles)
        self.assertEqual(self.shield(run, rein).current, 1580)
        angles = aim(run.match.combat._eye(soldier), (6, 1.55, 6))
        for _ in range(10):
            self.step(run, soldier, rein, angles=angles)
        self.step(run, soldier, rein, 1, angles=angles)
        self.assertEqual(self.shield(run, rein).current, 1580)

    def test_ground_explosion_damages_near_mesh_without_direct_hit(self):
        run, soldier, rein = self.scene()
        angles = aim(run.match.combat._eye(soldier), (0, 0, 4))
        self.helix(run, soldier, rein, angles=angles)
        loss = 1600 - self.shield(run, rein).current
        self.assertGreater(loss, 0)
        self.assertLessEqual(loss, 80)
        self.assertEqual(self.pool(run, rein).total, 500)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_body_replacement_retires_old_shield_and_new_body_starts_unraised(self):
        run, soldier, rein = self.scene()
        self.helix(run, soldier, rein)
        old = rein.body
        self.assertTrue(run.match.switch_hero(rein, rein.hero))
        self.assertNotEqual(rein.body, old)
        self.assertNotIn(old, run.match.combat.barriers.states)
        rein.mover.position[:] = [0, 0, 6]
        rein.mover.body = None
        rein.body_script.ult_seconds = 0
        for _ in range(35):
            self.step(run, soldier, rein, held=0)
        self.step(run, soldier, rein, 1, held=0, angles=aim(run.match.combat._eye(soldier), (0, .8, 6)))
        self.assertEqual(self.pool(run, rein).total, 485)

    def test_actual_reinhardt_passive_scales_helix_motion_and_removal_restores_full_response(self):
        peaks = []
        for enabled in (True, False):
            with self.subTest(actual_passive=enabled):
                run, soldier, rein = self.scene(raised=False)
                passive = next(i for i in rein.body_script.component.instances.values()
                               if i.graph.index == 0x00A4).states[4]
                self.assertTrue(passive.active)
                if not enabled:
                    passive.end(False)
                self.assertAlmostEqual(defenses.knockback_scale(rein), .7 if enabled else 1, places=6)
                self.helix(run, soldier, rein, held=0)
                peak = rein.mover.position[1]
                for _ in range(100):
                    self.step(run, soldier, rein, held=0)
                    peak = max(peak, rein.mover.position[1])
                self.assertGreater(peak, .1)
                self.assertEqual(self.pool(run, rein).total, 390)
                peaks.append(peak)
        self.assertGreater(peaks[1], peaks[0] + .2)

    def test_front_flashbang_hits_mesh_but_rear_stun_lowers_shield(self):
        for front in (True, False):
            with self.subTest(front=front):
                run, soldier, rein = self.scene()
                caster = run.match.add_player(3, "McCree", 0x02E0000000000042, 2, False)
                in_world(run.match, caster)
                run.players.append(caster)
                run.match.switch_hero(caster, heroes()[0x02E0000000000042])
                caster.team = soldier.team
                caster.mover.position[:] = [0, 0, 3 if front else 9]
                caster.mover.body = None
                caster.body_script.ult_seconds = 0
                angles = aim(run.match.combat._eye(caster), (0, 1, 6))
                for _ in range(4):
                    run.step({rein: (-32768, 0, 2), caster: (*angles, 0)})
                run.step({rein: (-32768, 0, 2), caster: (*angles, 16)})
                stunned = False
                for _ in range(40):
                    run.step({rein: (-32768, 0, 2), caster: (*angles, 0)})
                    stunned |= run.match.combat.is_stunned(rein)
                self.assertEqual(stunned, not front)
                if front:
                    self.assertEqual(self.shield(run, rein).current, 1575)
                    self.assertEqual(self.pool(run, rein).total, 500)
                    self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0)
                else:
                    self.assertEqual(self.pool(run, rein).total, 480)
                    self.assertFalse(self.shield(run, rein).active)

    def test_dead_owner_cannot_keep_or_restart_live_mesh(self):
        run, soldier, rein = self.scene()
        target = next(t for t in run.match.combat._targets() if t.owner is rein)
        # Terminal setup bypasses the mesh deliberately; actual attack/stun
        # delivery is covered elsewhere and is not inferred from this setup.
        run.match.combat._damage(soldier, target, 1000, False, None, run.frame)
        self.assertTrue(self.pool(run, rein).dead)
        for _ in range(20):
            self.step(run, soldier, rein, held=2)
        self.assertFalse(self.shield(run, rein).active)
        self.assertFalse(self.instance(rein).states[17].active)
