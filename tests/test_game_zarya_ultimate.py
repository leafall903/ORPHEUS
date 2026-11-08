"""Actual cast/projectile/field combat tests; host force policy is reconstructed."""

import unittest
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import combat, graviton
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ZARYA = 0x02E0000000000068


class ZaryaUltimateTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=("Zarya", "Enemy", "Ally"))
        self.a, self.b, self.c = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.switch_hero(self.a, heroes()[ZARYA])
        self.c.team = self.a.team
        for player, position in ((self.a, [0, 1, 0]), (self.b, [0, 1, 5]), (self.c, [2, 1, 5])):
            player.mover.position[:] = position

    def health(self, player=None):
        player = self.b if player is None else player
        return self.vitals(player).total

    def vitals(self, player):
        target = next(t for t in self.fight._targets() if t.owner is player)
        return self.fight.vitals_of(target)

    def charge(self):
        return self.a.body_script.component.entity_var(ULT_CHARGE).value()

    def cost(self):
        return self.a.body_script.component.entity_var(ULT_COST).value()

    def cast(self, *, yaw=0):
        self.a.body_script.set_ult_charge(self.cost())
        self.run.step({self.a: (yaw, 0, 0x20)})

    def advance(self, frames):
        for _ in range(frames):
            self.run.step({})

    def until_field(self):
        for _ in range(100):
            self.run.step({})
            if self.fight.zarya_gravitons.fields:
                return self.fight.zarya_gravitons.fields[0]
        self.fail("cast never produced its field")

    def test_zero_charge_rejects_ultimate(self):
        self.a.body_script.ult_seconds = 0
        self.run.step({self.a: (0, 0, 0x20)})
        self.advance(30)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.fight.zarya_gravitons.fields)
        self.assertEqual(self.health(), 200)

    def test_below_cost_rejects_held_press_and_full_cost_allows_cast(self):
        self.assertEqual(self.cost(), 2100)
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(self.cost() - 1)
        for _ in range(20):
            self.run.step({self.a: (0, 0, 0x20)})
        self.assertFalse(self.fight.rockets)
        self.run.step({})
        self.cast()
        self.assertEqual(len(self.fight.rockets), 1)
        self.assertEqual(self.charge(), 0)

    def test_cast_consumes_charge_and_launches_authored_deployable(self):
        self.cast()
        self.assertEqual(self.charge(), 0)
        rocket, = self.fight.rockets
        self.assertEqual(rocket.spawn, graviton.FIELD_ENTITY)
        self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
        self.assertEqual(rocket.direct.amount, 5)

    def test_existing_passive_charge_advances_but_does_not_allow_early_cast(self):
        self.advance(100)
        self.assertGreater(self.charge(), 0)
        self.assertLess(self.charge(), self.cost())
        self.run.step({self.a: (0, 0, 0x20)})
        self.assertFalse(self.fight.rockets)

    def test_held_input_cannot_repeat_without_recharging(self):
        self.cast()
        self.a.body_script.ult_seconds = 0
        for _ in range(300):
            self.run.step({self.a: (0, 0, 0x20)})
        self.assertEqual(self.charge(), 0)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.fight.zarya_gravitons.fields)
        self.assertAlmostEqual(self.health(), 175)

    def test_direct_5_plus_field_20_damage_then_effect_ends(self):
        self.cast()
        field = self.until_field()
        self.assertEqual(field.end_tick - field.last_tick, 250)
        self.assertAlmostEqual(self.health(), 195)
        self.advance(270)
        self.assertAlmostEqual(self.health(), 175)
        self.assertFalse(self.fight.zarya_gravitons.fields)
        self.advance(30)
        self.assertAlmostEqual(self.health(), 175)

    def test_allies_and_caster_are_not_damaged_or_pulled(self):
        ally = tuple(self.c.mover.position)
        caster = tuple(self.a.mover.position)
        self.cast()
        self.advance(280)
        self.assertEqual(self.health(self.c), 200)
        self.assertEqual(self.health(self.a), 400)
        self.assertEqual(tuple(self.c.mover.position), ally)
        self.assertEqual(tuple(self.a.mover.position), caster)

    def test_aimed_miss_does_not_damage_enemy(self):
        self.cast(yaw=16384)
        self.advance(280)
        self.assertEqual(self.health(), 200)

    def test_static_wall_blocks_field_from_enemy(self):
        wall = World([(-10, 0, 2, 10, 0, 2, 10, 10, 2),
                      (-10, 0, 2, 10, 10, 2, -10, 10, 2)])
        with patch.object(self.fight, "_collision", return_value=wall):
            self.cast()
            self.advance(280)
        self.assertEqual(self.health(), 200)

    def test_enemy_outside_radius_is_not_affected(self):
        self.c.team = self.b.team
        self.c.mover.position[:] = [8, 1, 5]
        self.cast()
        self.advance(280)
        self.assertEqual(self.health(self.c), 200)
        self.assertEqual(self.c.mover.position[0], 8)

    def test_projectile_uses_vertical_acceleration(self):
        self.cast()
        rocket, = self.fight.rockets
        start = rocket.position[1]
        self.advance(1)
        self.assertAlmostEqual(rocket.position[1], start - 0.5 * 9.8 * 0.016**2, places=6)

    def test_upward_flight_reaches_range_lifetime_then_deploys_once(self):
        self.a.body_script.set_ult_charge(self.cost())
        self.run.step({self.a: (0, -16384, 0x20)})
        rocket, = self.fight.rockets
        self.assertEqual(rocket.left, 4)
        self.advance(260)
        self.assertFalse(self.fight.rockets)
        field, = self.fight.zarya_gravitons.fields
        self.assertAlmostEqual(field.position[1], 2.55 + 25 * 4 - 0.5 * 9.8 * 4**2, places=4)
        self.assertEqual(self.health(), 200)
        self.advance(260)
        self.assertFalse(self.fight.zarya_gravitons.fields)

    def test_zero_displacement_at_apex_does_not_freeze_flight_clock(self):
        rocket = combat.Rocket(self.a, self.a.team, (0, 50, 0), (0, 0.008, 0),
                               1, 1, combat.HitDef(), None, last_tick=self.fight.tick, gravity=1)
        self.fight.rockets.append(rocket)
        self.fight.tick += 1
        self.fight._fly(rocket)
        self.assertAlmostEqual(rocket.flown, 0.016)
        self.assertAlmostEqual(rocket.left, 0.984)
        self.fight.tick += 1
        self.fight._fly(rocket)
        self.assertLess(rocket.position[1], 50)

    def test_dead_victim_is_not_pulled_after_lethal_field_tick(self):
        self.cast()
        self.until_field()
        vitals = self.vitals(self.b)
        vitals.now[combat.HEALTH_POOL] = 0.1
        self.advance(2)
        self.assertTrue(vitals.dead)
        position = tuple(self.b.mover.position)
        self.advance(10)
        self.assertEqual(tuple(self.b.mover.position), position)

    def test_field_pulls_player_and_expires_before_control_resumes(self):
        self.cast()
        field = self.until_field()
        before = self.b.mover.position[2]
        self.advance(20)
        self.assertLess(self.b.mover.position[2], before)
        # Submit actual movement commands: the field takes priority over forward input.
        for _ in range(60):
            self.run.frame += 1
            self.run.match.update(self.run.frame * 0.016, self.run.frame)
            for player in self.run.players:
                player.take_commands([Command(self.run.frame, forward=127 if player is self.b else 0)])
        self.assertLessEqual(self.b.mover.position[2], field.position[2] + 0.1)
        self.advance(210)
        self.assertIsNone(self.fight.zarya_gravitons.movement_for(self.b))
        before = self.b.mover.position[2]
        for _ in range(20):
            self.run.frame += 1
            self.run.match.update(self.run.frame * 0.016, self.run.frame)
            for player in self.run.players:
                player.take_commands([Command(self.run.frame, forward=127 if player is self.b else 0)])
        self.assertGreater(self.b.mover.position[2], before)

    def test_duplicate_world_tick_cannot_repeat_field_damage(self):
        self.cast()
        self.until_field()
        self.advance(1)
        before = self.health()
        self.fight.update(self.fight.now, self.fight.tick, frames=False)
        self.assertEqual(self.health(), before)

    def test_skipped_scheduler_integrates_only_remaining_field_lifetime(self):
        self.cast()
        field = self.until_field()
        self.fight.update((field.end_tick + 20) * 0.016, field.end_tick + 20, frames=False)
        self.assertAlmostEqual(self.health(), 175)
        self.assertFalse(self.fight.zarya_gravitons.fields)

    def test_caster_death_does_not_cancel_existing_field(self):
        self.cast()
        self.until_field()
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight._damage(self.b, target, 500, False, None, self.run.frame)
        before = self.health()
        self.advance(20)
        self.assertLess(self.health(), before)

    def test_body_replacement_preserves_original_cast_killer_identity(self):
        self.cast()
        field = self.until_field()
        old = self.a.body
        self.run.match.switch_hero(self.a, self.a.hero)
        self.assertNotEqual(self.a.body, old)
        vitals = self.vitals(self.b)
        vitals.now[combat.HEALTH_POOL] = 0.1
        self.advance(3)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, old)
        self.assertEqual(field.source_entity, old)
        self.assertEqual(self.b.client.messages(combat.DEATH_NOTICE)[-1]["+0x7C"]["+0x0"], old)

    def test_departure_cancels_remaining_field(self):
        self.cast()
        self.until_field()
        self.a.gone = True
        before = self.health()
        self.advance(2)
        self.assertEqual(self.health(), before)
        self.assertFalse(self.fight.zarya_gravitons.fields)

    def test_inflight_ultimate_uses_cast_team_after_owner_team_change(self):
        self.cast()
        self.a.team = self.b.team
        self.advance(280)
        self.assertAlmostEqual(self.health(), 175)
        self.assertEqual(self.health(self.c), 200)

    def test_inflight_secondary_preserves_team_for_direct_and_splash(self):
        self.run.step({self.a: (0, 0, 2)})
        self.a.team = self.b.team
        self.advance(90)
        self.assertAlmostEqual(self.health(), 153)
        self.assertEqual(self.health(self.c), 200)

    def test_unleased_projectile_keeps_emission_source_after_body_replacement(self):
        # Explicit delivery-unavailable fixture: authoritative flight must still
        # retain its original source; no client presentation is claimed here.
        with patch.object(self.fight.projectiles, "fired", return_value=None):
            self.cast()
        rocket, = self.fight.rockets
        self.assertIsNone(rocket.shot)
        old = self.a.body
        self.run.match.switch_hero(self.a, self.a.hero)
        self.assertNotEqual(self.a.body, old)
        vitals = self.vitals(self.b)
        vitals.now[combat.HEALTH_POOL] = 3
        self.advance(30)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, old)
        self.assertEqual(self.b.client.messages(combat.DEATH_NOTICE)[-1]["+0x7C"]["+0x0"], old)

    def test_departure_before_impact_cancels_damage_and_deployment(self):
        self.cast()
        self.a.gone = True
        self.advance(40)
        self.assertEqual(self.health(), 200)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.fight.zarya_gravitons.fields)

    def test_match_end_before_impact_cancels_damage_and_deployment(self):
        self.cast()
        self.run.match.ended = True
        self.advance(40)
        self.assertEqual(self.health(), 200)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.fight.zarya_gravitons.fields)

    def test_departure_releases_pull_before_next_scheduler_update(self):
        self.cast()
        self.until_field()
        self.assertIsNotNone(self.fight.zarya_gravitons.movement_for(self.b))
        self.a.gone = True
        self.assertIsNone(self.fight.zarya_gravitons.movement_for(self.b))

    def test_match_end_releases_pull_before_next_scheduler_update(self):
        self.cast()
        self.until_field()
        self.run.match.ended = True
        self.assertIsNone(self.fight.zarya_gravitons.movement_for(self.b))


if __name__ == "__main__":
    unittest.main()
