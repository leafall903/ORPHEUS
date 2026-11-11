"""Actual McCree Flashbang input interrupts Symmetra weapon graphs offline."""

import unittest

import test_mccree_flashbang as flashbang_tests
import test_symmetra_barriers as barrier_tests
import test_symmetra_zarya as zarya_tests
from test_game_combat import aim
from test_symmetra_combat import health

from ow174.game import combat, projectile_tags, sigma_grasp, symmetra, transcendence
from ow174.game.content import heroes
from ow174.game.script import nodes


class SymmetraFlashbangTests(unittest.TestCase):
    arena = flashbang_tests.FlashbangTests.arena
    step = flashbang_tests.FlashbangTests.step
    cast = flashbang_tests.FlashbangTests.cast

    def test_flashbang_blocks_buffered_melee_without_late_damage(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                run, caster, owner = self.arena(distance=2)
                self.assertTrue(run.match.switch_hero(owner, heroes()[symmetra.HERO]))
                owner.mover.position[:] = (0, 1, 2)
                caster.mover.position[:] = (0, 1, 0)
                fight = run.match.combat
                melee = 0x800
                initial = health(run, caster).total
                self.step(run, caster, owner, target_buttons=melee)
                self.assertEqual(health(run, caster).total, initial - 30)
                first_frame = run.frame
                for age in range(1, 63):
                    self.step(run, caster, owner, target_buttons=melee if age == 54 else 0,
                              cast=16 if interrupted and age == 55 else 0)
                    if age == 54:
                        self.assertFalse(fight.is_stunned(owner))
                    if age < 62:
                        self.assertEqual(health(run, caster).total, initial - 30)

                self.assertEqual(run.frame - first_frame, 62)
                self.assertEqual(fight.is_stunned(owner), interrupted)
                self.assertEqual(health(run, owner).total, 200 if interrupted else 225)
                if not interrupted:
                    self.assertEqual(health(run, caster).total, initial - 60)
                    continue

                deadline = fight.stuns[owner.body]
                while run.frame <= deadline:
                    self.step(run, caster, owner)
                    self.assertEqual(health(run, caster).total, initial - 30)
                self.assertFalse(fight.is_stunned(owner))
                self.step(run, caster, owner, target_buttons=melee)
                self.assertEqual(health(run, caster).total, initial - 60)

    def test_flashbang_blocks_buffered_melee_reconstructed_after_stun_expiry(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                run, caster, owner = self.arena(distance=2)
                self.assertTrue(run.match.switch_hero(owner, heroes()[symmetra.HERO]))
                owner.mover.position[:] = (0, 1, 2)
                caster.mover.position[:] = (0, 1, 0)
                fight = run.match.combat
                melee = 0x800
                initial = health(run, caster).total
                self.step(run, caster, owner, target_buttons=melee)
                first_frame = run.frame
                for _ in range(53):
                    self.step(run, caster, owner)
                self.step(run, caster, owner, target_buttons=melee)
                self.step(run, caster, owner, cast=16 if interrupted else 0)
                self.assertEqual(run.frame - first_frame, 55)
                self.assertEqual(health(run, caster).total, initial - 30)
                run.players.remove(owner)
                for _ in range(63):
                    run.step({})
                    if run.frame == first_frame + 62:
                        self.assertEqual(fight.is_stunned(owner), interrupted)
                run.players.append(owner)
                self.assertFalse(fight.is_stunned(owner))
                self.step(run, caster, owner)
                self.assertEqual(run.frame - first_frame, 119)
                expected = initial - (30 if interrupted else 60)
                self.assertEqual(health(run, caster).total, expected)
                for _ in range(10):
                    self.step(run, caster, owner)
                self.step(run, caster, owner, target_buttons=melee)
                self.assertEqual(health(run, caster).total, expected - 30)

    def test_actual_enemy_shot_kills_buffered_melee_before_respawn(self):
        run, caster, owner = self.arena(distance=2)
        self.assertTrue(run.match.switch_hero(owner, heroes()[symmetra.HERO]))
        owner.mover.position[:] = (0, 1, 2)
        caster.mover.position[:] = (0, 1, 0)
        fight = run.match.combat
        shot = aim(fight._eye(caster), (0, 2, 2))
        back = aim(fight._eye(owner), (0, 2, 0))
        melee = 0x800
        initial = health(run, caster).total
        for expected in (155, 85, 15):
            run.step({caster: (*shot, 1)})
            self.assertEqual(health(run, owner).total, expected)
            if expected > 15:
                for _ in range(40):
                    run.step({})
        run.step({owner: (*back, melee)})
        first_frame = run.frame
        self.assertEqual(health(run, caster).total, initial - 30)
        for _ in range(53):
            run.step({})
        run.step({owner: (*back, melee)})
        self.assertEqual(run.frame - first_frame, 54)
        self.assertFalse(fight.is_dead(owner))
        self.assertEqual(health(run, caster).total, initial - 30)

        body = owner.body
        run.step({caster: (*shot, 1)})
        self.assertTrue(fight.is_dead(owner))
        self.assertEqual(health(run, owner).killer, caster.body)
        [deadline] = [when for when, actor in fight.respawns if actor is owner]
        while owner.body == body and run.frame * combat.TICK <= deadline + 1:
            run.step({})
            self.assertEqual(health(run, caster).total, initial - 30)
        self.assertNotEqual(owner.body, body)
        self.assertEqual(health(run, owner).total, 225)
        x, y, z = caster.mover.position
        barrier_tests.PhotonBarrierTests().move(owner, (x, y, z + 2))
        for _ in range(12):
            run.step({})
        self.assertEqual(health(run, caster).total, initial - 30)
        self.step(run, caster, owner, target_buttons=melee)
        self.assertEqual(health(run, caster).total, initial - 60)

    def test_projected_barrier_absorbs_flashbang_and_preserves_live_orb_charge(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                run, attacker, zarya, owner, _, bubble, pet = zarya_tests.SymmetraZaryaTests().scene(True)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000042]))
                self.assertTrue(run.match.switch_hero(owner, heroes()[symmetra.HERO]))
                attacker.mover.position[:] = (0, 1, 2)
                owner.mover.position[:] = (0, 1, 5)
                fight = run.match.combat

                forward = aim(fight._eye(attacker), (0, 2, 5))
                back = aim(fight._eye(owner), (0, 2, 2))
                for frame in range(49):
                    run.step({
                        attacker: (*forward, 16 if frame == 26 else 0),
                        zarya: (-16384, 0, 16 if protected and frame == 25 else 0),
                        owner: (*back, 2),
                    })
                    if frame == 25:
                        self.assertEqual(bubble.state(pet).active, protected)
                self.assertEqual(fight.is_stunned(owner), not protected)
                self.assertEqual(health(run, owner).total, 225 if protected else 200)
                energy = zarya.body_script.component.entity_var(87).value() or 0
                self.assertEqual(energy, 5 if protected else 0)
                run.step({owner: (*back, 0)})
                self.assertEqual(sum(rocket.shooter is owner for rocket in fight.rockets), int(protected))

    def test_transcendence_prevents_flashbang_damage_and_stun(self):
        run, caster, target = self.arena(distance=3)
        position = tuple(target.mover.position)
        self.assertTrue(run.match.switch_hero(target, heroes()[transcendence.HERO]))
        target.mover.position[:] = position
        script = target.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, caster, target, target_buttons=32)
        self.assertTrue(transcendence.invulnerable(target))
        fight = run.match.combat
        self.step(run, caster, target, cast=16)
        self.assertTrue(fight.rockets)
        for _ in range(20):
            self.step(run, caster, target)
        self.assertFalse(fight.rockets)
        self.assertEqual(health(run, target).total, 200)
        self.assertFalse(fight.is_stunned(target))

    def test_breaking_flashbang_does_not_stun_through_personal_barrier(self):
        run, attacker, zarya, _, _, instance, pet = zarya_tests.SymmetraZaryaTests().scene(False)
        self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000042]))
        attacker.mover.position[:] = (0, 1, 2)
        fight = run.match.combat
        run.step({zarya: (0, 0, 8)})
        _, _, bubble = fight.zarya_barriers.contact(zarya)
        # Pre-damaged barrier isolates the breaking damage event and its control.
        bubble.remaining = 10
        angles = aim(fight._eye(attacker), (0, 2, 5))
        run.step({attacker: (*angles, 16)})
        for _ in range(22):
            run.step({attacker: (*angles, 0)})
        self.assertEqual(bubble.remaining, 0)
        self.assertFalse(instance.state(pet).active)
        self.assertEqual(zarya.body_script.component.entity_var(87).value(), 2)
        self.assertEqual(health(run, zarya).total, 400)
        self.assertFalse(fight.is_stunned(zarya))

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires authored projectile tags")
    def test_ally_grasp_consumes_flashbang_before_it_stuns_symmetra(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                run, attacker, sigma, owner, _, _, _ = zarya_tests.SymmetraZaryaTests().scene(True)
                for player, hero, position in (
                    (attacker, 0x02E0000000000042, (0, 1, 0)),
                    (sigma, sigma_grasp.SIGMA, (0, 1, 3)),
                    (owner, symmetra.HERO, (0, 1, 4)),
                ):
                    self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
                    player.mover.position[:] = position
                fight = run.match.combat
                angles = aim(fight._eye(attacker), (0, 2, 3))
                run.step({sigma: (-32768, 0, 8 if protected else 0)})
                run.step({attacker: (*angles, 16), sigma: (-32768, 0, 0)})
                self.assertTrue(fight.rockets)
                for _ in range(20):
                    run.step({attacker: (*angles, 0), sigma: (-32768, 0, 0)})
                self.assertFalse(fight.rockets)
                self.assertEqual(health(run, owner).total, 225 if protected else 200)
                self.assertEqual(fight.is_stunned(owner), not protected)
                grasp = next(i for i in sigma.body_script.component.instances.values()
                             if i.graph.index == sigma_grasp.GRAPH)
                self.assertEqual(grasp.find_var(0, 21312).value() or 0, 25 if protected else 0)

    def setup(self, **options):
        run, caster, target = self.arena(**({"distance": 3} | options))
        position = list(target.mover.position)
        self.assertTrue(run.match.switch_hero(target, heroes()[symmetra.HERO]))
        target.mover.position[:] = position
        return run, caster, target

    def test_beam_stops_during_stun_and_actual_input_resumes_after_expiry(self):
        run, caster, target = self.setup()
        starting = health(run, caster).total
        for _ in range(5):
            self.step(run, caster, target, target_buttons=1)
        self.assertLess(health(run, caster).total, starting)
        before_shields = health(run, target).now[combat.SHIELDS_POOL]
        self.cast(run, caster, target, target_buttons=1)
        self.assertEqual(health(run, target).now[combat.SHIELDS_POOL], before_shields - 25)
        fight = run.match.combat
        deadline = fight.stuns[target.body]
        before = health(run, caster).total
        position = tuple(target.mover.position)
        while run.frame < deadline - 1:
            self.step(run, caster, target, target_buttons=1, forward=127)
            self.assertEqual(health(run, caster).total, before)
            self.assertFalse(target.body_script.component.held)
            self.assertEqual(tuple(target.mover.position), position)
        self.step(run, caster, target, target_buttons=1)
        self.assertFalse(fight.is_stunned(target))
        self.assertLess(health(run, caster).total, before)

    def test_orb_charge_is_cancelled_without_queued_shot_then_recharges(self):
        run, caster, target = self.setup()
        for _ in range(20):
            self.step(run, caster, target, target_buttons=2)
        fight = run.match.combat
        self.assertFalse(fight.rockets)
        self.cast(run, caster, target, target_buttons=2)
        deadline = fight.stuns[target.body]
        before = health(run, caster).total
        while run.frame < deadline:
            self.step(run, caster, target, target_buttons=2)
            self.assertFalse(fight.rockets)
            self.assertFalse(target.body_script.component.shots)
        self.assertEqual(health(run, caster).total, before)
        for _ in range(90):
            self.step(run, caster, target, target_buttons=2)
        self.step(run, caster, target)
        self.assertEqual(len(fight.rockets), 1)
        for _ in range(30):
            self.step(run, caster, target)
        self.assertAlmostEqual(before - health(run, caster).total, 120, places=4)

    def test_wall_allies_and_range_protect_symmetra_from_damage_and_control(self):
        for options in ({"wall": True}, {"ally": True}, {"distance": 20}):
            with self.subTest(options=options):
                run, caster, target = self.setup(**options)
                before = health(run, target).total
                self.step(run, caster, target, cast=16)
                for _ in range(100):
                    self.step(run, caster, target)
                    self.assertFalse(run.match.combat.is_stunned(target))
                self.assertEqual(health(run, target).total, before)

    def test_flashbang_interrupts_teleporter_placement_and_actual_input_reopens_it(self):
        run, caster, target = self.setup()
        for _ in range(5):
            self.step(run, caster, target, target_buttons=16)
        instance = next(i for i in target.body_script.component.instances.values()
                        if i.graph.index == 0x1571)
        ability = instance.state(23)
        self.assertIsInstance(ability, nodes.Ability)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.cast(run, caster, target, target_buttons=16)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        fight = run.match.combat
        deadline = fight.stuns[target.body]
        while run.frame < deadline - 1:
            self.step(run, caster, target, target_buttons=16)
            self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.step(run, caster, target, target_buttons=16)
        self.assertFalse(fight.is_stunned(target))
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        # This verifies the authored placement ability, not portal creation or
        # the still-incomplete right-click cancel/confirmation world services.

    def test_replacement_retires_control_without_transferring_it_to_new_symmetra(self):
        run, caster, target = self.setup()
        self.cast(run, caster, target)
        old = target.body
        fight = run.match.combat
        position = list(target.mover.position)
        self.assertTrue(run.match.switch_hero(target, heroes()[symmetra.HERO]))
        target.mover.position[:] = position
        self.assertNotEqual(old, target.body)
        self.assertNotIn(old, fight.stuns)
        self.assertFalse(fight.is_stunned(target))
        before = health(run, caster).total
        for _ in range(4):
            self.step(run, caster, target, target_buttons=1)
        self.assertLess(health(run, caster).total, before)


if __name__ == "__main__":
    unittest.main()
