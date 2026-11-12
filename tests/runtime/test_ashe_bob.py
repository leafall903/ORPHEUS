"""Actual ultimate -> wall-stopped B.O.B. -> actual gun; charge has separate checks.

Requires external, base-bound graph and phase-mover extensions. Native contact,
force and engine status controls remain separate OPEN acceptance items.
"""

import os
import unittest
from pathlib import Path

import test_ashe_combat as fixtures
from test_ashe_impulses import wall_scene

from ow174.game import combat, stats
from ow174.game.collision import World, flat_world
from ow174.game.content import SOLDIER, heroes

EXTENSION = Path(os.environ.get("ORPHEUS_GRAPH_EXTENSION", "."))


@unittest.skipUnless(EXTENSION.is_file(), "requires explicit external B.O.B. graph extension")
class BobTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight
    press = fixtures.AsheCombatTests.press

    def setup(self, *, team_game=False, distance=10):
        run, owner, enemy = self.fight(distance=distance, team_game=team_game)
        for player in run.players:
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        for target in run.match.combat._targets():
            run.match.combat.vitals_of(target)
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 10000)
        # Isolate the gun phase through a real charge collision, never by
        # mutating phase/ready or bypassing the physical charge implementation.
        run.match.collision_world = wall_scene(3)
        return run, owner, enemy

    def cast(self, *, team_game=False, distance=10):
        run, owner, enemy = self.setup(team_game=team_game, distance=distance)
        owner.body_script.set_ult_charge(2240)
        self.press(run, owner, 32, frames=45)
        actor, = run.match.combat.companions.active.values()
        self.assertEqual(actor.stopped_reason, "wall")
        run.match.collision_world = flat_world(1)
        return run, owner, enemy, actor

    def test_cast_gate_actual_body_and_no_early_shot(self):
        run, owner, _enemy = self.setup()
        owner.body_script.set_ult_charge(0)
        self.press(run, owner, 32, frames=45)
        self.assertFalse(run.match.combat.companions.active)
        self.assertGreater(owner.body_script.component.entity_var(342).value(), 0)
        owner.body_script.set_ult_charge(2240)
        self.press(run, owner, 32, frames=1)
        # The authored summon action runs during cast recovery, not after it.
        self.assertEqual(len(run.match.combat.companions.active), 1)
        for _ in range(44):
            run.step({})
        actor, = run.match.combat.companions.active.values()
        self.assertEqual(actor.bot.body, 0x04000000000025C3)
        self.assertEqual(actor.bot.hero, 0x02E000000000021D)
        self.assertIsNotNone(actor.bot.body_script)
        self.assertEqual(owner.body_script.component.entity_var(512).value().id, actor.bot.entity)
        self.assertEqual(run.match.combat.vitals[actor.bot.entity].total, 1200)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

    def test_authored_gun_cadence_damage_owner_credit_and_charge_lock(self):
        run, owner, enemy, actor = self.cast()
        while run.frame < actor.ready:
            run.step({})
        for _ in range(61):
            run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 10000 - 8 * 14)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 8 * 14)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 8)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(run.match.combat.vitals[owner.body].total, 200)
        self.assertEqual(run.match.combat.vitals[actor.bot.entity].total, 1200)

    def test_skipped_ticks_across_gun_start_preserve_volley_ticks_and_credit(self):
        results = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, enemy, actor = self.cast()
                fight = run.match.combat
                while run.frame < actor.ready - 12:
                    run.step({})
                self.assertEqual(actor.phase, "settle")
                self.assertFalse(actor.bot.firing)
                self.assertEqual(actor.shots, 0)
                if skipped:
                    run.frame += 36
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    for _ in range(36):
                        run.step({})
                self.assertEqual(actor.phase, "gun")
                self.assertEqual(actor.target, enemy.body)

                hits = owner.client.messages(combat.HIT)
                shots = actor.shots
                health = fight.vitals[enemy.body].total
                credit = owner.stats.value(stats.HERO_DAMAGE)
                results.append((shots, health, credit,
                                [(hit["+0x84"] - actor.ready, hit["+0x78"]["+0x4"]) for hit in hits]))
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(actor.shots, shots)
                self.assertEqual(fight.vitals[enemy.body].total, health)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), credit)
                self.assertEqual(owner.client.messages(combat.HIT), hits)
        self.assertEqual(results[0], (4, 10000 - 4 * 14, 4 * 14, [(frame, 14) for frame in (0, 7, 15, 23)]))
        self.assertEqual(results[1], results[0])

    def test_long_gap_fires_only_on_retained_ticks_before_expiry(self):
        run, owner, enemy, actor = self.cast()
        fight = run.match.combat
        self.assertEqual(actor.phase, "settle")
        self.assertFalse(actor.bot.firing)

        run.frame = actor.until + 8
        retained = run.frame - combat.COMBAT_CATCH_UP + 1
        self.assertGreater(retained, actor.ready)
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotIn(actor.bot, run.match.bots)
        self.assertFalse(fight.companions.active)
        hits = owner.client.messages(combat.HIT)
        offsets = (0, 7, 15, 23, 31, 39, 46, 54)
        self.assertEqual([hit["+0x84"] - retained for hit in hits], list(offsets))
        self.assertTrue(all(hit["+0x78"]["+0x4"] == 14 for hit in hits))
        self.assertLess(hits[-1]["+0x84"], actor.until)
        self.assertEqual(fight.vitals[enemy.body].total, 10000 - len(offsets) * 14)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), len(offsets) * 14)

    def test_duplicate_ultimate_cannot_bypass_active_actor_gate(self):
        run, owner, _enemy, actor = self.cast()
        owner.body_script.set_ult_charge(2240)  # GM resource writes do not bypass the actor gate
        self.press(run, owner, 32, frames=45)
        self.assertEqual(list(run.match.combat.companions.active.values()), [actor])
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 2240)

    def test_ally_exclusion_and_companion_healing(self):
        run, owner, ally, actor = self.cast(team_game=True)
        ally.team = owner.team
        for _ in range(150):
            run.step({})
        self.assertEqual(run.match.combat.vitals[ally.body].total, 10000)
        self.assertEqual(owner.client.messages(combat.HIT), [])
        target = next(item for item in run.match.combat._targets() if item.owner is actor.bot)
        vitals = run.match.combat.vitals_of(target)
        vitals.damage(100)
        run.match.combat.heal_area(owner, actor.bot.position, 5, 50)
        self.assertEqual(vitals.total, 1150)

    def test_shared_heal_area_keeps_companion_ally_semantics_in_ffa(self):
        run, owner, enemy, actor = self.cast()
        bob_health = run.match.combat.vitals[actor.bot.entity]
        enemy_health = run.match.combat.vitals[enemy.body]
        bob_health.damage(100)
        enemy_health.damage(100)
        run.match.combat.heal_area(owner, actor.bot.position, 20, 50)
        self.assertEqual(bob_health.total, 1150)
        self.assertEqual(enemy_health.total, 9900)

    def test_wall_prevents_target_acquisition_and_damage(self):
        run, owner, enemy, actor = self.cast()
        original = run.match.collision_world
        run.match.collision_world = World([
            (-1000, 1, -1000, -1000, 1, 1000, 1000, 1, 1000),
            (-1000, 1, -1000, 1000, 1, 1000, 1000, 1, -1000),
            (-10, -10, 5, 10, -10, 5, 10, 10, 5),
            (-10, -10, 5, 10, 10, 5, -10, 10, 5),
        ])
        for _ in range(150):
            run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 10000)
        self.assertIsNone(actor.target)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        wall = run.match.collision_world
        run.match.collision_world = original
        for _ in range(62):
            run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 10000 - 8 * 14)
        run.match.collision_world = wall
        for _ in range(62):
            run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 10000 - 8 * 14)

    def test_out_of_range_enemy_is_not_acquired_or_shot(self):
        run, owner, enemy, actor = self.cast(distance=110)
        for _ in range(150):
            run.step({})
        self.assertIsNone(actor.target)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 10000)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

    def test_owner_death_persists_actor(self):
        run, owner, enemy, actor = self.cast()
        target = next(item for item in run.match.combat._targets() if item.owner is owner)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        while run.frame < actor.ready + 20:
            run.step({})
        self.assertIn(actor.bot, run.match.bots)
        self.assertLess(run.match.combat.vitals[enemy.body].total, 10000)

    def test_expiry_removes_actor_without_respawn_and_restores_charge(self):
        run, owner, _enemy, actor = self.cast()
        while run.frame < actor.until - 1:
            run.step({})
        self.assertIn(actor.bot, run.match.bots)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        while run.frame < actor.until:
            run.step({})
        self.assertNotIn(actor.bot, run.match.bots)
        self.assertFalse(run.match.combat.companions.active)
        self.assertFalse(any(who is actor.bot for _, who in run.match.combat.respawns))
        self.assertEqual(owner.body_script.component.entity_var(512).value().id, 0)
        self.assertGreater(owner.body_script.component.entity_var(342).value(), 0)

    def test_hero_replacement_removes_companion_only(self):
        run, owner, _enemy, actor = self.cast()
        run.match.switch_hero(owner, heroes()[SOLDIER])
        self.assertNotIn(actor.bot, run.match.bots)
        self.assertFalse(run.match.combat.companions.active)

    def test_owner_weapons_and_dynamite_do_not_damage_own_actor_in_ffa(self):
        run, owner, _enemy, actor = self.cast()
        vitals = run.match.combat.vitals[actor.bot.entity]
        self.press(run, owner, 1, frames=20)
        self.assertEqual(vitals.total, 1200)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        self.press(run, owner, 16)
        grenade, = run.match.combat.dynamite.grenades
        grenade.position = (actor.bot.position[0], actor.bot.position[1] + 0.8, actor.bot.position[2])
        run.match.combat.dynamite.detonate(grenade)
        self.assertEqual(vitals.total, 1200)
        self.assertNotIn((owner.body, actor.bot.entity), run.match.combat.dynamite.burns)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_enemy_player_weapon_kills_actor_before_expiry_without_respawn(self):
        run, _owner, enemy, actor = self.cast()
        yaw, pitch = fixtures.aim(run.match.combat._eye(enemy),
                                  (actor.bot.position[0], actor.bot.position[1] + 1, actor.bot.position[2]))
        while actor.bot in run.match.bots and run.frame < actor.until:
            run.step({enemy: (yaw, pitch, 1)})
        self.assertLess(run.frame, actor.until)
        self.assertNotIn(actor.bot, run.match.bots)
        self.assertGreater(enemy.stats.value(stats.ELIMINATIONS), 0)
        self.assertFalse(any(who is actor.bot for _, who in run.match.combat.respawns))


if __name__ == "__main__":
    unittest.main()
