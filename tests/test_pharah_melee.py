"""Actual melee inputs at the reconstructed sphere/capsule boundary; no native hitbox claim."""

import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_control as control
import test_pharah_defenses as defense
import test_pharah_orisa_barrier as orisa
import test_pharah_sigma_barrier as sigma
import test_pharah_weapon as weapon
import test_pharah_zarya_barrier as zarya
import test_reinhardt_resources as reinhardt

from ow174.game import barrier_pet, defenses, sigma_grasp
from ow174.game.combat import HIT
from ow174.game.script.health_queries import pool_sum


class PharahMeleeTests(unittest.TestCase):
    encounter = weapon.PharahWeaponTests.encounter
    health = weapon.PharahWeaponTests.health
    relocate = weapon.PharahWeaponTests.relocate
    step = weapon.PharahWeaponTests.step
    clear_graphs = staticmethod(reinhardt.ReinhardtResourceTests.clear_graphs)

    def test_melee_excludes_friendly_player_and_gives_no_charge_or_hit_notice(self):
        run, player, ally = self.encounter()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = player.team
        self.relocate(ally, z=1.8)
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        self.step(run, player, 1, 0x800)
        self.step(run, player, 70)
        self.assertEqual(self.health(run, ally).total, 200)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(player.client.messages(25001))

    def test_reconstructed_sphere_capsule_front_boundary_and_targets_behind(self):
        # This brackets the executable adapter, not retail TrackTargets geometry.
        for offset, expected in ((-0.002, 170), (0.002, 200), (None, 200)):
            with self.subTest(offset=offset):
                run, player, enemy = self.encounter()
                target = next(t for t in run.match.combat._targets() if t.owner is enemy)
                eye_y = run.match.combat._eye(player)[1]
                a, b = target.segment()
                vertical = max(a[1] - eye_y, eye_y - b[1], 0)
                boundary = 1.5 + math.sqrt((1.0 + target.radius) ** 2 - vertical ** 2)
                self.relocate(enemy, z=-1.8 if offset is None else boundary + offset)
                # Populate view history at this pose before the attack.
                self.step(run, player, 3)
                self.step(run, player, 1, 0x800)
                self.step(run, player, 20)
                self.assertEqual(self.health(run, enemy).total, expected)

    def test_actual_melee_consumes_the_track_targets_radius(self):
        for radius, expected in ((.5, 200), (1.0, 170)):
            with self.subTest(radius=radius):
                run, player, enemy = self.encounter()
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is enemy)
                self.relocate(enemy, z=1.5 + target.radius + .75)
                self.step(run, player, 3)
                melee = next(i for i in player.body_script.component.instances.values()
                             if i.graph.index == 0x43)
                fields = melee.graph.state(5).fields
                # Leave the authored unit-radius expression in place for the hit trial.
                value = fields["m_radius"] if radius == 1.0 else {"$": "STUConfigVarFloat", "m_value": radius}
                with patch.dict(fields, m_radius=value):
                    self.step(run, player, 1, 0x800)
                self.assertEqual(self.health(run, enemy).total, expected)

    def test_actual_melee_aims_up_to_an_elevated_target(self):
        for yaw, x, z in ((0, 0, .85), (16384, .85, 0)):
            for pitch, expected in ((-32768, 170), (-10923, 170), (0, 200), (10923, 200)):
                with self.subTest(yaw=yaw, pitch=pitch):
                    run, player, enemy = self.encounter()
                    self.relocate(enemy, x=x, z=z)
                    enemy.mover.position[1] = player.mover.position[1] + 3.0
                    self.step(run, player, 3)
                    run.step({player: (yaw, pitch, 0x800)})
                    self.assertEqual(self.health(run, enemy).total, expected)

    def test_holding_button_does_not_repeat_and_spam_cannot_bypass_recovery(self):
        for held in (True, False):
            with self.subTest(held=held):
                run, player, enemy = self.encounter()
                self.relocate(enemy, z=1.8)
                for frame in range(60):
                    self.step(run, player, 1, 0x800 if held or frame % 2 == 0 else 0)
                self.assertEqual(self.health(run, enemy).total, 170)
                for frame in range(60, 80):
                    self.step(run, player, 1, 0x800 if held or frame % 2 == 0 else 0)
                self.assertEqual(self.health(run, enemy).total, 170 if held else 140)

    def test_actual_genji_deflect_blocks_frontal_melee_only_until_expiry(self):
        for yaw, expected in ((-32768, 200), (0, 170)):
            with self.subTest(yaw=yaw):
                run, player, genji, other = defense.PharahDefenseTests().encounter("Genji")
                self.relocate(genji, z=1.8)
                self.relocate(other, z=20)
                run.step({genji: (yaw, 0, 16)})
                for _ in range(4):
                    run.step({genji: (yaw, 0, 0)})
                run.step({player: (0, 0, 0x800), genji: (yaw, 0, 0)})
                self.assertEqual(self.health(run, genji).total, expected)
                self.assertEqual(self.health(run, player).total, 200)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 200 - expected)
                if expected == 200:
                    self.assertFalse(player.client.messages(25001))
                for _ in range(160):
                    run.step({})
                run.step({player: (0, 0, 0x800), genji: (yaw, 0, 0)})
                self.assertEqual(self.health(run, genji).total, expected - 30)

    def test_actual_flashbang_suppresses_melee_and_requires_a_fresh_press(self):
        fixture = control.PharahControlTests()
        run, player, caster = fixture.arena()
        deadline = fixture.flash(run, player, caster)
        self.relocate(caster, z=1.8)
        while run.frame < deadline - 2:
            run.step({player: (0, 0, 0x800)})
        self.assertEqual(self.health(run, caster).total, 225)
        for _ in range(10):
            run.step({})
        self.assertEqual(self.health(run, caster).total, 225)
        run.step({player: (0, 0, 0x800)})
        self.assertEqual(self.health(run, caster).total, 195)

    def test_actual_flashbang_ends_an_active_melee_without_refunding_recovery(self):
        fixture = control.PharahControlTests()
        run, player, caster = fixture.arena()
        self.relocate(caster, z=-1.8)
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        for _ in range(5):
            fixture.step(run, player, caster)
        melee = next(i for i in script.component.instances.values() if i.graph.index == 0x43)
        fixture.step(run, player, caster, buttons=0x800)
        self.assertEqual(self.health(run, caster).total, 195)
        hit = melee.states[4]
        ability = melee.states[6]
        self.assertTrue(hit.active)
        initial_recovery = ability.remaining()
        swing_frame = run.frame
        self.assertGreater(initial_recovery, .9)
        deadline = fixture.flash(run, player, caster)
        self.assertFalse(hit.active)
        self.assertEqual(hit.counter, 1)
        self.assertGreater(ability.remaining(), 0)
        while run.frame < deadline - 1:
            fixture.step(run, player, caster)
        fixture.step(run, player, caster, buttons=0x800)
        self.assertFalse(run.match.combat.is_stunned(player))
        self.assertGreater(ability.remaining(), 0)
        elapsed = (run.frame - swing_frame) * .016
        self.assertAlmostEqual(ability.remaining(), initial_recovery - elapsed, places=3)
        self.assertEqual(hit.counter, 1)
        self.assertEqual(self.health(run, caster).total, 195)
        for _ in range(30):
            remaining = ability.remaining()
            fixture.step(run, player, caster, buttons=0x800)
            if hit.counter != 1:
                self.assertLessEqual(remaining, .017)
                break
            self.assertEqual(self.health(run, caster).total, 195)
        self.assertEqual(hit.counter, 2)
        self.assertEqual(self.health(run, caster).total, 165)
        self.assertEqual(script.component.entity_var(342).value(), 60)
        self.assertEqual([event["+0x78"]["+0x4"] for event in player.client.messages(HIT)], [30, 30])

    def test_quick_melee_skips_phased_front_body_and_hits_it_after_expiry(self):
        for name in ("Reaper", "Moira"):
            with self.subTest(hero=name):
                fixture = defense.PharahDefenseTests()
                run, player, front, rear = fixture.encounter(name)
                self.relocate(front, x=-.35, z=1.3)
                self.relocate(rear, x=.35, z=2.1)
                fixture.cast(run, front)
                script = player.body_script
                front_health = self.health(run, front)
                rear_health = self.health(run, rear)
                before = front_health.total
                self.assertEqual(defenses.phase(front), (True, True))
                self.step(run, player, 1, 0x800)
                self.assertEqual(front_health.total, before)
                self.assertEqual(rear_health.total, 170)
                self.assertEqual(script.component.entity_var(342).value(), 30)
                melee = next(i for i in script.component.instances.values() if i.graph.index == 0x43)
                for _ in range(220):
                    if not defenses.phase(front)[0] and melee.states[6].remaining() <= 0:
                        break
                    self.step(run, player, 1)
                self.assertEqual(defenses.phase(front), (False, False))
                self.assertEqual(melee.states[6].remaining(), 0)
                self.step(run, player, 1, 0x800)
                self.assertEqual(front_health.total, before - 30)
                self.assertEqual(rear_health.total, 140)
                self.assertEqual(script.component.entity_var(342).value(), 90)
                self.assertEqual([event["+0x78"]["+0x4"] for event in player.client.messages(HIT)], [30, 30, 30])

    def test_quick_melee_hits_through_actual_kinetic_grasp(self):
        run, player, sigma, rear = defense.PharahDefenseTests().encounter("Sigma")
        self.relocate(sigma, z=1.8)
        self.relocate(rear, z=20)
        run.step({sigma: (-32768, 0, 8)})
        for _ in range(7):
            run.step({sigma: (-32768, 0, 0)})
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(any(state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles"
                            for state in grasp.states.values()))
        before = self.health(run, sigma).total
        run.step({player: (0, 0, 0x800), sigma: (-32768, 0, 0)})
        self.assertEqual(self.health(run, sigma).total, before - 30)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 30)
        self.assertEqual(grasp.vars[21312].value(), 0)

    def test_quick_melee_crosses_an_actual_hostile_winston_barrier(self):
        for source_z, enemy_z, yaw in ((0, 1.8, 0), (1.8, 0, -32768)):
            with self.subTest(yaw=yaw):
                run, player, winston, enemy = defense.PharahDefenseTests().encounter("Winston")
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                player.team, winston.team, enemy.team = 0, 1, 1
                self.relocate(winston, z=6)
                self.relocate(player, z=source_z)
                self.relocate(enemy, z=enemy_z)
                run.step({winston: (0, 0, 16)})
                for _ in range(4):
                    run.step({})
                fight = run.match.combat
                bubble = fight.winston_bubbles[winston.body]
                origin = fight._eye(player)
                direction = (0, 0, 1 if yaw == 0 else -1)
                self.assertLess(bubble.ray(origin, direction, 1.8), 1.8)
                run.step({player: (yaw, 0, 0x800)})
                self.assertEqual(self.health(run, enemy).total, 170)
                self.assertEqual(bubble.health, 700)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 30)

    def test_projected_zarya_barrier_absorbs_quick_melee_for_its_target(self):
        _, run, player, caster, enemy, bubble = zarya.ZaryaProjectileTests().encounter(projected=True)
        self.relocate(player, z=6.2)
        self.step(run, player, 3)
        self.step(run, player, 1, 0x800)
        self.assertEqual(self.health(run, enemy).total, 200)
        self.assertEqual(bubble.health, 170)
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 6)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(player.client.messages(25001))

    def test_quick_melee_crosses_actual_hostile_orisa_and_sigma_meshes(self):
        for hero, asset in (("Orisa", "ORPHEUS_ORISA_GEOMETRY"), ("Sigma", "ORPHEUS_SIGMA_BARRIER_MODEL")):
            with self.subTest(hero=hero):
                if not os.environ.get(asset):
                    self.skipTest("prepared 104319 barrier mesh required")
                if hero == "Orisa":
                    _, run, player, _, enemy, barrier = orisa.OrisaProjectileTests().deployed()
                    side, yaw = 1, -32768
                else:
                    _, run, player, _, barrier = sigma.SigmaProjectileTests().setup_barrier()
                    enemy = run.players[2]
                    side, yaw = -1, 0
                x, _, z = barrier.position
                self.relocate(player, x=x, z=z + side * .9)
                self.relocate(enemy, x=x, z=z - side * .9)
                self.step(run, player, 3)
                origin = run.match.combat._eye(player)
                self.assertLess(barrier.ray(origin, (0, 0, -side), 1.8), 1.8)
                before = barrier.health if hero == "Orisa" else barrier.vitals.total
                run.step({player: (yaw, 0, 0x800)})
                self.assertEqual(self.health(run, enemy).total, 170)
                after = barrier.health if hero == "Orisa" else barrier.vitals.total
                self.assertEqual(after, before)

    def test_quick_melee_crosses_reinhardts_raised_barrier(self):
        if not os.environ.get("ORPHEUS_REINHARDT_COLLISION"):
            self.skipTest("prepared 104319 Reinhardt mesh required")
        reinhardt.ReinhardtResourceTests.use_pet_bundle(self)
        run, player, owner, _ = defense.PharahDefenseTests().encounter("Reinhardt")
        self.relocate(owner, z=1.8)
        for _ in range(20):
            run.step({owner: (-32768, 0, 2)})
        fight = run.match.combat
        before = self.health(run, owner).total
        colliders = barrier_pet.active_colliders(fight, fight._enemies(player))
        contact = barrier_pet.contact(colliders, fight._eye(player), (0, 0, 1), 1.8)
        self.assertIsNotNone(contact)
        pet = contact[2]
        run.step({player: (0, 0, 0x800), owner: (-32768, 0, 2)})
        self.assertEqual(self.health(run, owner).total, before - 25)
        self.assertEqual(pool_sum(pet.world.health_pools), 1600)

    def test_death_ends_active_melee_and_rejects_new_melee_presses(self):
        run, player, enemy = self.encounter()
        self.relocate(enemy, z=1.8)
        self.step(run, player, 1, 0x800)
        self.assertEqual(self.health(run, enemy).total, 170)
        script = player.body_script
        melee = next(i for i in script.component.instances.values() if i.graph.index == 0x43)
        self.assertTrue(melee.states[4].active)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        self.assertTrue(script.component.owner_dead)
        self.assertFalse(melee.states[4].active)
        for frame in range(90):
            self.step(run, player, 1, 0x800 if frame % 2 == 0 else 0)
        self.assertEqual(self.health(run, enemy).total, 170)


if __name__ == "__main__":
    unittest.main()
