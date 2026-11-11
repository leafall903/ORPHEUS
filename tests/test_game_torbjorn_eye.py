"""Authored104319 Torbjorn view origins and standing/crouched low-cover shots."""

import unittest

import test_game_torbjorn as torbjorn
from test_game_combat import aim

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import heroes


class TorbjornEyeTests(unittest.TestCase):
    @staticmethod
    def advance(run, owner, ticks, buttons=0):
        for _ in range(ticks):
            run.step({owner: (0, 0, buttons)})

    def test_actual_posture_eye_target_and_rivet_origin_share_authored_height(self):
        for crouched, expected in ((False, 1.0), (True, .7)):
            with self.subTest(crouched=crouched):
                run, owner, _ = torbjorn.TorbjornProjectileTests().arena()
                posture = CROUCH if crouched else 0
                self.advance(run, owner, 15, posture)
                self.assertEqual(owner.mover.crouched, crouched)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is owner)
                self.assertEqual(target.eye, expected)
                self.assertAlmostEqual(fight._eye(owner)[1] - owner.mover.position[1], expected)
                run.step({owner: (0, 0, 1 | posture)})
                rocket, = fight.rockets
                self.assertAlmostEqual(rocket.position[1] - owner.mover.position[1], expected)

    def test_actual_rivet_is_occluded_by_cover_between_standing_and_crouched_view(self):
        for crouched, cover, loss in ((False, .85, 70), (True, .85, 0), (False, 1.2, 0)):
            with self.subTest(crouched=crouched, cover=cover):
                run, owner, victim = torbjorn.TorbjornProjectileTests().arena()
                x, y, z = owner.mover.position
                front = z + 2
                run.match.collision_world = World([
                    (x-100, y, z-100, x-100, y, z+100, x+100, y, z+100),
                    (x-100, y, z-100, x+100, y, z+100, x+100, y, z-100),
                    (x-3, y, front, x+3, y, front, x+3, y+cover, front),
                    (x-3, y, front, x+3, y+cover, front, x-3, y+cover, front),
                ], name="authored-torbjorn-low-cover")
                posture = CROUCH if crouched else 0
                self.advance(run, owner, 15, posture)
                run.step({owner: (0, 0, 1 | posture)})
                self.advance(run, owner, 20, posture)
                self.assertEqual(run.match.combat.vitals[victim.body].total, 200 - loss)

    def test_actual_enemy_mccree_uses_authored_eye_and_hits_torbjorn_armour(self):
        for crouched in (False, True):
            with self.subTest(crouched=crouched):
                run, owner, enemy = torbjorn.TorbjornProjectileTests().arena()
                position = list(enemy.mover.position)
                run.match.switch_hero(enemy, heroes()[0x02E0000000000042])
                enemy.mover.position = position
                enemy.mover.body = None
                posture = CROUCH if crouched else 0
                self.advance(run, enemy, 35, posture)
                expected = .9975 if crouched else 1.425
                fight = run.match.combat
                self.assertAlmostEqual(fight._eye(enemy)[1] - enemy.mover.position[1], expected)
                target = next(t for t in fight._targets() if t.owner is enemy)
                self.assertEqual(target.eye, expected)
                x, y, z = owner.mover.position
                yaw, pitch = aim(fight._eye(enemy), (x, y + .65, z))
                run.step({enemy: (yaw, pitch, 1 | posture)})
                self.advance(run, enemy, 20, posture)
                self.assertEqual(fight.vitals[owner.body].total, 185)

    def test_host_null_critical_fields_apply_to_actual_rivet_and_secondary(self):
        for button, state_number, rounds, count, damage in ((1, 10, 1, 1, 70), (2, 13, 2, 10, 125)):
            for height, hit in ((.82, True), (1.56, True), (2.4, False)):
                with self.subTest(button=button, height=height):
                    self.exercise_host_null_critical(button, state_number, rounds, count, damage, height, hit)

    def exercise_host_null_critical(self, button, state_number, rounds, count, damage, height, hit):
        run, owner, victim = torbjorn.TorbjornProjectileTests().arena(2)
        fight = run.match.combat
        rifle = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x1683)
        state = rifle.state(state_number)
        self.assertIsNone(state.node.field("m_6396149F")["m_A872C70E"])
        x, y, z = victim.mover.position
        yaw, pitch = aim(fight._eye(owner), (x, y + height, z))
        run.step({owner: (yaw, pitch, button)})
        self.assertEqual(rifle.vars[53].value(), 18 - rounds)
        self.assertEqual(len(fight.rockets), count)
        self.assertTrue(all(r.shooter is owner and r.direct.critical == 1 for r in fight.rockets))
        self.advance(run, owner, 30)
        self.assertEqual(fight.vitals[victim.body].total, 200 - (damage if hit else 0))
        notices = owner.client.messages(combat.HIT)
        self.assertEqual(len(notices), count if hit else 0)
        self.assertFalse(any(notice["+0x78"]["+0x8"] & combat.CRITICAL for notice in notices))

    def test_unknown_body_retains_existing_fallback(self):
        self.assertEqual(combat.eye_height(0), combat.EYE)
        self.assertEqual(combat.eye_height(0, True), combat.EYE_CROUCHED_DEFAULT)

    def test_owned_mccree_primary_head_policy_and_explicit_fan_critical_are_scoped(self):
        for crouched in (False, True):
            with self.subTest(crouched=crouched):
                run, owner, enemy = torbjorn.TorbjornProjectileTests().arena()
                position = list(enemy.mover.position)
                run.match.switch_hero(enemy, heroes()[0x02E0000000000042])
                enemy.mover.position = position
                enemy.mover.body = None
                posture = CROUCH if crouched else 0
                self.advance(run, owner, 35, posture)
                fight = run.match.combat
                yaw, pitch = aim(fight._eye(enemy), fight._eye(owner))
                run.step({enemy: (yaw, pitch, 1), owner: (0, 0, posture)})
                self.advance(run, owner, 20, posture)
                self.assertEqual(fight.vitals[owner.body].total, 115)
                hit = enemy.client.messages(combat.HIT)[0]["+0x78"]
                self.assertTrue(hit["+0x8"] & combat.CRITICAL)
                weapon = next(i for i in enemy.body_script.component.instances.values()
                              if i.graph.index == 0x01D2)
                explicit = []
                for node in weapon.graph.nodes:
                    cfg = node.field("m_6396149F") or {}
                    if cfg.get("m_A872C70E") is not None:
                        explicit.append(combat.hit_def(weapon.state(node.state), cfg).critical)
                self.assertTrue(explicit)
                self.assertTrue(all(value == 1 for value in explicit))
                rivet = next(i for i in owner.body_script.component.instances.values()
                             if i.graph.index == 0x1683).state(10)
                self.assertEqual(combat.hit_def(rivet, rivet.node.field("m_6396149F")).critical, 1)
