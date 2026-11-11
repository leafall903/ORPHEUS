"""Actual opposing Flashbang input against Sigma's authored ability lifecycles."""

import os
import unittest

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, sigma, tracer_pistol
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import nodes


class SigmaControlTests(unittest.TestCase):
    def arena(self, *, front=False, with_rifle=False):
        names = ("Sigma", "McCree", "Soldier") if with_rifle else ("Sigma", "McCree")
        run = Run(KANEZAKA_DM, names)
        player, enemy, *others = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[sigma.HERO])
        run.match.switch_hero(enemy, heroes()[0x02E0000000000042])
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + (3 if front else -3)]
        if with_rifle:
            others[0].mover.position = [x, y, z + 10]
        yaw, pitch = aim(run.match.combat._eye(enemy), (x, y + 1, z))
        return run, player, enemy, yaw, pitch

    def step(self, run, player, enemy, yaw, pitch, *, skill=0, attack=0):
        run.step({player: (0, 0, skill), enemy: (yaw, pitch, attack)})

    def stun(self, run, player, enemy, yaw, pitch, *, skill=0):
        self.step(run, player, enemy, yaw, pitch, skill=skill, attack=0x10)
        for _ in range(50):
            if run.match.combat.is_stunned(player):
                return
            self.step(run, player, enemy, yaw, pitch, skill=skill)
        self.fail("Actual opposing Flashbang did not stun Sigma")

    def instance(self, player, index):
        return next(i for i in player.body_script.component.instances.values() if i.graph.index == index)

    def test_rear_flashbang_interrupts_grasp_and_cooldown_prevents_restart(self):
        run, player, enemy, yaw, pitch = self.arena()
        self.step(run, player, enemy, yaw, pitch, skill=0x08)
        grasp = self.instance(player, sigma.GRASP)
        self.assertTrue(grasp.states[1].active)
        self.stun(run, player, enemy, yaw, pitch, skill=0x08)
        self.assertFalse(grasp.states[1].active)
        self.assertGreater(grasp.states[0].remaining(), 0)
        until = run.match.combat.stuns[player.body]
        while run.frame <= until + 20:
            self.step(run, player, enemy, yaw, pitch, skill=0x08)
        self.assertFalse(grasp.states[1].active)
        self.assertGreater(grasp.states[0].remaining(), 0)

    def test_front_grasp_absorbs_flashbang_before_it_can_apply_stun(self):
        run, player, enemy, yaw, pitch = self.arena(front=True)
        self.step(run, player, enemy, yaw, pitch, skill=0x08)
        grasp = self.instance(player, sigma.GRASP)
        self.step(run, player, enemy, yaw, pitch, attack=0x10)
        for _ in range(160):
            self.step(run, player, enemy, yaw, pitch)
            self.assertFalse(run.match.combat.is_stunned(player))
        self.assertEqual(grasp.vars[21312].value(), 25)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        self.assertAlmostEqual(run.match.combat.vitals_of(target).total, 415, places=4)

    def test_grasp_absorbs_front_peacekeeper_fire_and_fan_the_hammer(self):
        for attack, damage in ((FIRE, 70), (SECONDARY, 300)):
            for front in (False, True):
                with self.subTest(attack=attack, front=front):
                    run, player, enemy, yaw, pitch = self.arena(front=front)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is player)
                    vitals = fight.vitals_of(target)
                    self.step(run, player, enemy, yaw, pitch, skill=0x08)
                    grasp = self.instance(player, sigma.GRASP)
                    self.assertTrue(grasp.states[1].active)
                    self.step(run, player, enemy, yaw, pitch, attack=attack)
                    for _ in range(55):
                        self.step(run, player, enemy, yaw, pitch)
                    self.assertEqual(grasp.vars[21312].value(), damage if front else 0)
                    self.assertEqual(vitals.total, 400 if front else 400 - damage)
                    for _ in range(95):
                        self.step(run, player, enemy, yaw, pitch)
                    self.assertFalse(grasp.states[1].active)
                    if front:
                        self.assertEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, damage * 0.6)
                    else:
                        self.assertFalse(vitals.temporary_shields)

    def test_grasp_absorbs_authored_hitscan_bullets_across_weapon_families(self):
        weapons = (
            (0x02E0000000000003, False, 12),   # Tracer, one two-bullet report
            (0x02E0000000000002, False, 120),  # Reaper, one 20-pellet shell
            (0x02E000000000000A, False, 13),   # Widowmaker, unscoped
            (0x02E000000000000A, True, 120),   # Widowmaker, fully charged
            (0x02E0000000000015, False, 20),   # Bastion, Recon
            (0x02E000000000012E, False, 8),    # Sombra
            (0x02E000000000013B, True, 70),    # Ana, scoped
            (0x02E0000000000200, True, 75),    # Ashe, scoped
            (0x02E0000000000221, False, 72),   # Baptiste, three-round burst
        )
        for hero, scoped, damage in weapons:
            for front in (True, False):
                with self.subTest(hero=hero, scoped=scoped, front=front):
                    run, player, enemy, _, _ = self.arena(front=front)
                    position = list(enemy.mover.position)
                    run.match.switch_hero(enemy, heroes()[hero])
                    enemy.mover.position = position
                    fight = run.match.combat
                    x, y, z = player.mover.position
                    yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
                    target = next(t for t in fight._targets() if t.owner is player)
                    vitals = fight.vitals_of(target)
                    zoom = SECONDARY if scoped else 0
                    if scoped:
                        for _ in range(90):
                            self.step(run, player, enemy, yaw, pitch, attack=zoom)
                    self.step(run, player, enemy, yaw, pitch, skill=0x08, attack=zoom)
                    grasp = self.instance(player, sigma.GRASP)
                    self.assertTrue(grasp.states[1].active)
                    self.step(run, player, enemy, yaw, pitch, attack=FIRE | zoom)
                    for _ in range(35):
                        self.step(run, player, enemy, yaw, pitch, attack=zoom)
                    self.assertEqual(grasp.vars[21312].value(), damage if front else 0)
                    self.assertEqual(vitals.total, 400 if front else 400 - damage)

    def test_partial_wall_blocks_only_the_covered_reaper_pellets_before_grasp(self):
        run, player, enemy, _, _ = self.arena(front=True)
        run.match.switch_hero(enemy, heroes()[0x02E0000000000002])
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 6]
        floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                 (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
        a, b, c, d = ((x + 0.025, y, z + 4), (x + 4, y, z + 4),
                      (x + 4, y + 4, z + 4), (x + 0.025, y + 4, z + 4))
        run.match.collision_world = World([*floor, (*a, *b, *c), (*a, *c, *d)])
        for actor in run.players:
            actor.mover.use(world=run.match.collision_world)
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
        self.step(run, player, enemy, yaw, pitch, skill=0x08)
        grasp = self.instance(player, sigma.GRASP)
        weapon = self.instance(enemy, 0x0019)
        self.assertEqual(weapon.vars[53].value(), 8)
        self.step(run, player, enemy, yaw, pitch, attack=FIRE)
        for _ in range(135):
            self.step(run, player, enemy, yaw, pitch)
        self.assertEqual(grasp.vars[21312].value(), 72)  # 12 pellets reach Grasp
        self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 43.2, places=4)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertEqual(weapon.vars[53].value(), 7)
        self.assertFalse(enemy.client.messages(combat.HIT))

    def test_wall_splits_a_bloomed_tracer_pair_before_grasp(self):
        run, player, enemy, _, _ = self.arena(front=True)
        run.match.switch_hero(enemy, heroes()[0x02E0000000000003])
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 6]
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is player)
        vitals = fight.vitals_of(target)
        origin = fight._eye(enemy)
        yaw, pitch = aim(origin, (x, y + 1, z))
        self.step(run, player, enemy, yaw, pitch, skill=0x08)
        grasp = self.instance(player, sigma.GRASP)
        weapon = self.instance(enemy, 0x0016)
        for _ in range(8):
            self.step(run, player, enemy, yaw, pitch, attack=FIRE)
        volley = weapon.states[8]
        self.assertEqual(volley.reported, 3)
        self.assertEqual(grasp.vars[21312].value(), 36)
        self.assertEqual(weapon.vars[53].value(), 34)

        # Place a wall edge between the fourth pair's two deterministic rays.
        activation = (volley.counter, volley.start)
        time = volley.start + volley.offset + nodes.shot_time(3, volley.rate)
        rays = tracer_pistol.rays(volley, 3, time, activation, combat.aim(yaw, pitch), 2)
        face = z + 4
        crossing = [origin[0] + (face - origin[2]) * ray[0] / ray[2] for ray in rays]
        self.assertNotAlmostEqual(*crossing)
        cut = sum(crossing) / 2
        floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                 (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
        a, b, c, d = ((x - 4, y, face), (cut, y, face), (cut, y + 4, face), (x - 4, y + 4, face))
        run.match.collision_world = World([*floor, (*a, *b, *c), (*a, *c, *d)])
        for actor in run.players:
            actor.mover.use(world=run.match.collision_world)
        for _ in range(3):
            self.step(run, player, enemy, yaw, pitch, attack=FIRE)
        self.assertEqual(volley.reported, 4)
        self.assertEqual(grasp.vars[21312].value(), 42)
        self.assertEqual(weapon.vars[53].value(), 32)
        key = (*activation, 3, time)
        before = snapshot_digest(export_body_vm(enemy.body_script))
        angle = volley.shot_spread[key]
        volley.shot_spread[key] = 0.0
        self.assertNotEqual(snapshot_digest(export_body_vm(enemy.body_script)), before)
        volley.shot_spread[key] = angle
        self.assertEqual(snapshot_digest(export_body_vm(enemy.body_script)), before)

        for _ in range(135):
            self.step(run, player, enemy, yaw, pitch)
        self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 25.2, places=4)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertFalse(enemy.client.messages(combat.HIT))
        self.step(run, player, enemy, yaw, pitch, attack=FIRE)
        self.assertNotEqual((volley.counter, volley.start), activation)
        self.assertEqual(volley.shot_spread[key], angle)

    def test_flashbang_interrupts_flux_before_its_authored_end_without_refunding_cost(self):
        run, player, enemy, yaw, pitch = self.arena()
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, player, enemy, yaw, pitch, skill=0x20)
        ability = self.instance(player, 0x1E41).states[0]
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.stun(run, player, enemy, yaw, pitch)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        # The existing server policy resumes passive generation for this frame
        # as soon as the running ability ends. No cast-cost refund is applied.
        generated = script.component.entity_var(338).value() / script.ult_seconds * script.frame_ms / 1000
        self.assertAlmostEqual(script.component.entity_var(342).value(), generated, places=4)
        for _ in range(100):
            self.step(run, player, enemy, yaw, pitch, skill=0x20)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertLess(script.component.entity_var(342).value(), script.component.entity_var(338).value())

    def test_flashbang_during_accretion_windup_prevents_a_delayed_rock(self):
        run, player, enemy, yaw, pitch = self.arena()
        self.step(run, player, enemy, yaw, pitch, skill=0x10)
        self.stun(run, player, enemy, yaw, pitch)
        for _ in range(120):
            self.step(run, player, enemy, yaw, pitch)
            self.assertFalse(any(rocket.shooter is player for rocket in run.match.combat.rockets))
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        self.assertEqual(run.match.combat.vitals_of(target).total, 225)

    def test_flashbang_during_flux_hover_restores_gravity_and_lands(self):
        run, player, enemy, yaw, pitch = self.arena()
        floor = player.mover.position[1]
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, player, enemy, yaw, pitch, skill=0x20)
        for _ in range(80):
            run.step({})
        flux = self.instance(player, sigma.FLUX)
        self.assertTrue(flux.states[13].active)
        self.assertEqual(player.mover.gravity_scale, 0)
        x, y, z = player.mover.position
        yaw, pitch = aim(run.match.combat._eye(enemy), (x, y + 1, z))
        self.stun(run, player, enemy, yaw, pitch)
        self.assertFalse(flux.states[0].flags & nodes.Ability.RUNNING)
        self.assertFalse(flux.states[13].active)
        for _ in range(100):
            run.step({})
        self.assertEqual(player.mover.gravity_scale, player.mover.data.gravity_scale)
        self.assertAlmostEqual(player.mover.position[1], floor, delta=0.01)
        self.assertFalse(player.mover.airborne)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires external Barrier model")
    def test_rear_flashbang_stops_barrier_travel_and_recall_waits_for_recovery(self):
        run, player, enemy, yaw, pitch = self.arena(with_rifle=True)
        fight = run.match.combat
        rifle = run.players[2]
        target = next(entry for entry in fight._targets() if entry.owner is player)
        health = fight.vitals_of(target)
        for _ in range(18):
            self.step(run, player, enemy, yaw, pitch, skill=SECONDARY)
        barrier, = fight.barriers.values()
        self.assertEqual(barrier.phase, 2)
        self.assertTrue(barrier.active)
        self.stun(run, player, enemy, yaw, pitch, skill=SECONDARY)
        self.step(run, player, enemy, yaw, pitch, skill=SECONDARY)
        self.assertTrue(fight.is_stunned(player))
        self.assertEqual(barrier.phase, 3)
        self.assertTrue(barrier.active)
        self.assertEqual(health.total, 375)
        x, y, z = player.mover.position
        rifle_yaw, rifle_pitch = aim(fight._eye(rifle), (x, y + 0.8, z))
        run.step({rifle: (rifle_yaw, rifle_pitch, FIRE)})
        self.assertTrue(fight.is_stunned(player))
        self.assertEqual(barrier.vitals.total, 680)
        self.assertEqual(health.total, 375)
        position = barrier.position
        self.step(run, player, enemy, yaw, pitch)
        self.step(run, player, enemy, yaw, pitch, skill=SECONDARY)
        self.assertTrue(fight.is_stunned(player))
        self.assertTrue(barrier.active)
        while fight.is_stunned(player):
            self.step(run, player, enemy, yaw, pitch)
            self.assertEqual(barrier.position, position)
        self.assertTrue(barrier.active)
        self.step(run, player, enemy, yaw, pitch, skill=SECONDARY)
        self.step(run, player, enemy, yaw, pitch)
        self.assertFalse(barrier.active)
        self.assertEqual(barrier.vitals.total, 680)
        self.assertGreater(barrier.instance.states[111].remaining(), 1.9)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires external Barrier model")
    def test_deployed_barrier_blocks_flashbang_damage_and_status(self):
        run, player, enemy, yaw, pitch = self.arena(front=True)
        # Stop the shield close enough to intercept a grenade thrown from3m.
        for frame in range(40):
            self.step(run, player, enemy, yaw, pitch, skill=SECONDARY if frame < 4 else 0)
        actor, = run.match.combat.barriers.values()
        self.assertTrue(actor.active)
        self.step(run, player, enemy, yaw, pitch, attack=0x10)
        for _ in range(60):
            self.step(run, player, enemy, yaw, pitch)
            self.assertFalse(run.match.combat.is_stunned(player))
        self.assertEqual(actor.vitals.total, 675)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)


if __name__ == "__main__":
    unittest.main()
