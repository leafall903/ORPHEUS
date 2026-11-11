"""Actual Adaptive Shield against Roadhog's weapons and control."""

import unittest
from dataclasses import replace

import test_game_roadhog_lucio_barrier as sound_fixtures
import test_game_roadhog_piledriver as fixtures
from test_game_combat import aim

from ow174.game import adaptive_shield, combat, lucio_barrier
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr
from ow174.game.world import ARMOUR_POOL


class RoadhogAdaptiveShieldTests(unittest.TestCase):
    def scene(self, distance=2, *, wall=None):
        return fixtures.RoadhogPiledriverTests().scene(distance, wall=wall)

    def start(self, run, ball):
        run.step({ball: (0, 0, 16)})
        fight = run.match.combat
        vitals = fight.vitals[ball.body]
        self.assertIn(ball.body, fight.adaptive_shields)
        return vitals, vitals.temporary_shields[adaptive_shield.POOL]

    def look(self, run, hog, ball):
        x, y, z = ball.mover.position
        return aim(run.match.combat._eye(hog), (x, y + 1.05, z))

    def test_counts_living_hostiles_once_in_range_including_behind_static_cover(self):
        for mode, expected in (("near", 175), ("far", 100), ("ally", 100), ("wall", 175), ("two", 250)):
            with self.subTest(mode=mode):
                run, hog, ball, rear = self.scene(12 if mode == "far" else 5,
                                                 wall=2.5 if mode == "wall" else None)
                fight = run.match.combat
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = ball.team = 0
                elif mode == "two":
                    x, y, z = ball.mover.position
                    rear.mover.position[:] = (x + 4, y, z)
                    rear.mover.body = None
                    for _ in range(5):
                        run.step({})
                _, shield = self.start(run, ball)
                self.assertEqual(shield.now, expected)
                lease = fight.adaptive_shields[ball.body]
                self.assertEqual(lease.state.instance.find_var(expr.INSTANCE, 11398).value(),
                                 (expected - 100) / 75)
                rear.mover.position[0] += 30
                rear.mover.body = None
                for _ in range(20):
                    run.step({ball: (0, 0, 16)})
                self.assertIs(fight.adaptive_shields[ball.body], lease)
                self.assertEqual(shield.now, expected)

    def test_all_roadhog_attacks_spend_the_pool_and_hook_still_pulls(self):
        for name, button, distance in (("primary", 1, 2), ("slug", 2, 2), ("fragments", 2, 10),
                                       ("melee", 0x800, 1.4), ("hook", 8, 2), ("whole hog", 32, 2)):
            with self.subTest(name=name):
                run, hog, ball, rear = self.scene(distance)
                fight = run.match.combat
                if name in ("primary", "fragments"):
                    x, y, z = ball.mover.position
                    rear.mover.position[:] = (x + 4, y, z)
                    rear.mover.body = None
                    for _ in range(6):
                        run.step({})
                vitals, shield = self.start(run, ball)
                capacity = 250 if name == "primary" else 175
                self.assertEqual(shield.now, capacity)
                script = hog.body_script
                if button == 32:
                    script.set_ult_charge(script.component.entity_var(338).value())
                look = self.look(run, hog, ball)
                run.step({hog: (*look, button)})
                for _ in range(85):
                    if shield.now < capacity:
                        break
                    run.step({hog: (*look, 0)})
                self.assertLess(shield.now, capacity)
                self.assertEqual(sum(vitals.now.values()), 20000)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(fight.hooks.controlled(ball.body), button == 8)
                if button == 8:
                    self.assertEqual(shield.now, 145)
                self.assertIn(ball.body, fight.adaptive_shields)

    def test_after_depletion_a_real_slug_damages_armour_and_earns_base_charge(self):
        run, hog, ball, _ = self.scene()
        fight = run.match.combat
        fight.vitals.pop(ball.body)
        vitals, shield = self.start(run, ball)
        self.assertEqual(sum(vitals.now.values()), 600)
        self.assertEqual(vitals.now[ARMOUR_POOL], 100)
        look = self.look(run, hog, ball)
        charge = hog.body_script.component.entity_var(342)
        for expected in (125, 75, 25, 0):
            run.step({hog: (*look, 2)})
            for _ in range(65):
                run.step({hog: (*look, 0)})
            self.assertEqual(shield.now, expected)
            if expected:
                self.assertEqual(vitals.now[ARMOUR_POOL], 100)
                self.assertEqual(charge.value(), 0)
        self.assertEqual(vitals.now[ARMOUR_POOL], 80)
        self.assertEqual(charge.value(), 20)
        self.assertEqual(len(hog.client.messages(combat.HIT)), 4)

    def test_world_expiry_without_owner_input_and_no_refresh_before_cooldown(self):
        run, hog, ball, _ = self.scene()
        fight = run.match.combat
        vitals, shield = self.start(run, ball)
        lease = fight.adaptive_shields[ball.body]
        ability = lease.state.instance.state(0)
        look = self.look(run, hog, ball)
        run.step({hog: (*look, 2)})
        while fight.tick < lease.expires - 15:
            run.step({})
        self.assertEqual(shield.now, 125)
        for _ in range(15):
            run.frame += 1
            run.match.update(run.frame * .016, run.frame)
        self.assertEqual(fight.tick, lease.expires)
        self.assertTrue(lease.state.active)
        self.assertEqual(shield.now, 0)
        self.assertEqual(sum(vitals.now.values()), 20000)
        run.step({ball: (0, 0, 16)})
        self.assertEqual(shield.now, 0)
        self.assertGreater(ability.remaining(), 0)
        for _ in range(420):
            run.step({})
            if ability.remaining() <= 0:
                break
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(shield.now, 0)
        vitals, shield = self.start(run, ball)
        self.assertEqual(shield.now, 175)
        self.assertIsNot(fight.adaptive_shields[ball.body], lease)

    def test_replacement_retires_old_shield_and_expiry(self):
        run, _, ball, _ = self.scene()
        fight = run.match.combat
        old_body = ball.body
        _, shield = self.start(run, ball)
        self.assertTrue(run.match.switch_hero(ball, heroes()[SOLDIER]))
        self.assertNotEqual(ball.body, old_body)
        self.assertNotIn(old_body, fight.adaptive_shields)
        self.assertEqual(shield.now, 0)
        replacement = fight.vitals_of(next(t for t in fight._targets() if t.owner is ball))
        self.assertNotIn(adaptive_shield.POOL, replacement.temporary_shields)

    def test_projected_barrier_protects_adaptive_pool_until_the_bubble_ends(self):
        run, hog, ball, zarya = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, ball.team, zarya.team = 0, 1, 1
        hero = next(h for h in heroes().values() if h.name == "Zarya")
        self.assertTrue(run.match.switch_hero(zarya, hero))
        x, y, z = ball.mover.position
        zarya.mover.position[:] = (x + 5, y, z)
        zarya.mover.body = None
        for _ in range(45):
            run.step({})
        _, shield = self.start(run, ball)
        self.assertEqual(shield.now, 175)
        project = aim(fight._eye(zarya), (x, y + 1.05, z))
        run.step({zarya: (*project, 16)})
        bubble = fight.zarya_bubbles.for_target(ball)
        self.assertIsNotNone(bubble)
        look = self.look(run, hog, ball)
        run.step({hog: (*look, 2)})
        for _ in range(50):
            run.step({hog: (*look, 0)})
        self.assertEqual(bubble.health, 150)
        self.assertEqual(shield.now, 175)
        for _ in range(130):
            run.step({})
            if fight.zarya_bubbles.for_target(ball) is None:
                break
        self.assertIsNone(fight.zarya_bubbles.for_target(ball))
        run.step({hog: (*look, 2)})
        for _ in range(50):
            run.step({hog: (*look, 0)})
        self.assertEqual(shield.now, 125)

    def test_lethal_primary_spends_shield_then_retires_the_old_body_pool(self):
        run, hog, ball, _ = self.scene()
        fight = run.match.combat
        vitals = fight.vitals[ball.body]
        vitals.damage(vitals.total - 1)
        fight._sync_body_health(ball, vitals)
        old_body = ball.body
        _, shield = self.start(run, ball)
        look = self.look(run, hog, ball)
        run.step({hog: (*look, 1)})
        for _ in range(45):
            if vitals.dead:
                break
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, hog.body)
        self.assertEqual(shield.now, 0)
        self.assertNotIn(old_body, fight.adaptive_shields)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 1)

    def test_sound_barrier_expires_independently_of_adaptive_shield(self):
        run, hog, ball, lucio = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, ball.team, lucio.team = 0, 1, 1
        self.assertTrue(run.match.switch_hero(lucio, heroes()[lucio_barrier.HERO]))
        x, y, z = ball.mover.position
        lucio.mover.position[:] = (x + 6, y, z)
        lucio.mover.body = None
        for _ in range(45):
            run.step({})
        vitals, adaptive = self.start(run, ball)
        sound_fixtures.RoadhogSoundBarrierTests().cast(run, lucio)
        sound = vitals.temporary_shields[lucio_barrier.POOL]
        self.assertEqual((sound.now, adaptive.now), (750, 175))
        look = self.look(run, hog, ball)
        start = fight.tick
        run.step({hog: (*look, 2)})
        for _ in range(45):
            run.step({})
        self.assertEqual(sound.now, 750 - (fight.tick - start) * 2 - 50)
        self.assertEqual(adaptive.now, 175)
        for _ in range(375):
            if sound.now == 0:
                break
            run.step({})
        self.assertEqual(sound.now, 0)
        self.assertEqual(adaptive.now, 175)
        run.step({hog: (*look, 2)})
        for _ in range(45):
            run.step({})
        self.assertEqual(adaptive.now, 125)
        self.assertEqual(sum(vitals.now.values()), 20000)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
