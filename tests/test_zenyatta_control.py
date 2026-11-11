"""Real Flashbang inputs against Zen's charging weapon and active Transcendence."""

import unittest

import test_mccree_flashbang as flashbang_tests
from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.zenyatta import HERO


class ZenyattaControlTests(unittest.TestCase):
    def projected_arena(self, *, orb_shooter=False):
        names = ("McCree", "Zen", "Zarya", "Orb shooter") if orb_shooter else ("McCree", "Zen", "Zarya")
        run = Run(names=names)
        caster, zen, zarya = run.players[:3]
        run.match.bots.clear()
        x, y, z = caster.mover.position
        actors = [(caster, 0x02E0000000000042, 0, (0, 0)), (zen, HERO, 1, (0, 3)),
                  (zarya, ZARYA, 1, (4, 3))]
        if orb_shooter:
            actors.append((run.players[3], HERO, 0, (2, -1)))
        for player, hero, team, offset in actors:
            run.match.switch_hero(player, heroes()[hero])
            player.team = team
            player.mover.position = [x+offset[0], y, z+offset[1]]
            player.mover.body = None
        for _ in range(20):
            run.step({})
        return flashbang_tests.FlashbangTests(), run, caster, zen, zarya

    def test_projected_barrier_blocks_new_flashbang_and_preserves_existing_stun_deadline(self):
        for shield_first in (True, False):
            with self.subTest(shield_first=shield_first):
                helper, run, caster, zen, zarya = self.projected_arena()
                fight = run.match.combat
                x, y, z = zen.mover.position
                if not shield_first:
                    helper.cast(run, caster, zen)
                deadline = fight.stuns.get(zen.body)
                cast_aim = aim(fight._eye(zarya), (x, y+1.5, z))
                run.step({zarya: (*cast_aim, 16)})
                bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                if shield_first:
                    helper.step(run, caster, zen, cast=16)
                    for _ in range(45):
                        helper.step(run, caster, zen)
                        self.assertFalse(fight.is_stunned(zen))
                    self.assertEqual(bubble.hp, 175)
                    self.assertEqual(helper.health(run, zen), 200)
                else:
                    self.assertEqual(fight.stuns[zen.body], deadline)
                    while run.frame < deadline-1:
                        helper.step(run, caster, zen)
                        self.assertTrue(fight.is_stunned(zen))
                    helper.step(run, caster, zen)
                    self.assertFalse(fight.is_stunned(zen))
                    self.assertEqual(bubble.hp, 200)
                    self.assertEqual(helper.health(run, zen), 175)

    def test_flashbang_that_breaks_projection_does_not_stun_zen(self):
        helper, run, caster, zen, zarya = self.projected_arena(orb_shooter=True)
        orb_shooter = run.players[3]
        fight = run.match.combat
        x, y, z = zen.mover.position
        cast_aim = aim(fight._eye(zarya), (x, y+1.5, z))
        fire_aim = aim(fight._eye(caster), (x, y+1, z))
        orb_aim = aim(fight._eye(orb_shooter), (x, y+1, z))
        run.step({zarya: (*cast_aim, 16)})
        bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
        run.step({caster: (*fire_aim, FIRE), orb_shooter: (*orb_aim, FIRE)})
        for _ in range(40):
            run.step({})
        self.assertEqual(bubble.hp, 82)
        run.step({caster: (*fire_aim, FIRE)})
        for _ in range(10):
            run.step({})
        self.assertEqual(bubble.hp, 12)
        helper.step(run, caster, zen, cast=16)
        for _ in range(45):
            helper.step(run, caster, zen)
            self.assertFalse(fight.is_stunned(zen))
        self.assertEqual(bubble.hp, 0)
        self.assertEqual(helper.health(run, zen), 200)
        self.assertNotIn((zarya.body, PROJECTED), fight.barriers.bubbles)
        instance = zarya.body_script.component.instances[bubble.instance]
        self.assertGreater(instance.vars[61].value(), 7)

    def arena(self):
        helper = flashbang_tests.FlashbangTests()
        run, caster, zen = helper.arena(distance=3)
        run.match.switch_hero(zen, heroes()[HERO])
        x, y, z = caster.mover.position
        zen.mover.position = [x, y, z+3]
        zen.mover.body = None
        helper.step(run, caster, zen)
        return helper, run, caster, zen

    def test_actual_flashbang_cannot_interrupt_damage_invulnerable_transcendence(self):
        helper, run, caster, zen = self.arena()
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        helper.step(run, caster, zen, target_buttons=0x20)
        fight = run.match.combat
        self.assertTrue(fight.zenyatta.ultimate(zen))
        helper.step(run, caster, zen, cast=0x10)
        for _ in range(60):
            helper.step(run, caster, zen)
            self.assertFalse(fight.is_stunned(zen))
            self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(helper.health(run, zen), 200)
        self.assertFalse(caster.client.messages(combat.HIT))

    def test_flashbang_detonates_at_range_end_before_reaching_zen(self):
        helper, run, caster, zen = self.arena()
        fight = run.match.combat
        x, y, z = caster.mover.position
        zen.mover.position = [x, y, z+8.2]
        zen.mover.body = None
        run.step({})
        run.step({caster: (0, 0, 16)})
        rocket, = fight.rockets
        self.assertGreater(rocket.velocity[1], 0)
        for _ in range(14):
            run.step({})
        self.assertIn(rocket, fight.rockets)
        self.assertLess(rocket.velocity[1], 0)
        self.assertLess(rocket.left, combat.TICK)
        self.assertEqual(helper.health(run, zen), 200)
        run.step({})
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(helper.health(run, zen), 175)
        self.assertTrue(fight.is_stunned(zen))

    def test_accelerated_flashbang_keeps_its_path_across_skipped_world_updates(self):
        flights = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                helper, run, caster, zen = self.arena()
                fight = run.match.combat
                x, y, z = caster.mover.position
                zen.mover.position = [x, y, z+8.2]
                zen.mover.body = None
                run.step({})
                run.step({caster: (0, 0, 16)})
                rocket, = fight.rockets
                run.step({})
                if skipped:
                    run.frame += 6
                    run.step({})
                else:
                    for _ in range(7):
                        run.step({})
                self.assertIn(rocket, fight.rockets)
                flights.append((rocket.position, rocket.velocity, rocket.left))
                for _ in range(8):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(helper.health(run, zen), 175)
                self.assertTrue(fight.is_stunned(zen))
        self.assertEqual(flights[0], flights[1])

    def test_actual_flashbang_cancels_charged_volley_and_weapon_recovers_after_expiry(self):
        helper, run, caster, zen = self.arena()
        for _ in range(90):
            helper.step(run, caster, zen, target_buttons=SECONDARY)
        helper.cast(run, caster, zen, target_buttons=SECONDARY)
        fight = run.match.combat
        self.assertEqual(helper.health(run, zen), 175)
        before = helper.health(run, caster)
        end = fight.stuns[zen.body]
        while run.frame < end-1:
            helper.step(run, caster, zen, target_buttons=FIRE | SECONDARY)
            self.assertEqual(helper.health(run, caster), before)
            self.assertFalse(any(r.shooter is zen for r in fight.rockets))
        helper.step(run, caster, zen)
        helper.step(run, caster, zen)
        helper.step(run, caster, zen, target_buttons=FIRE)
        for _ in range(20):
            helper.step(run, caster, zen)
        self.assertFalse(fight.is_stunned(zen))
        self.assertLess(helper.health(run, caster), before)


if __name__ == '__main__':
    unittest.main()
