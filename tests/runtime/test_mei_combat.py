"""Mei combat regressions with the external 104319 graphs; no client acceptance."""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ow174.game import combat
from ow174.game.combat import HitDef, Rocket, Vitals
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from tests.runtime.test_game_combat import KANEZAKA_DM, Run, aim

MEI = 0x02E00000000000DD


class MeiCombatTests(unittest.TestCase):
    def duel(self):
        run = Run(KANEZAKA_DM, ("Mei", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        mei, enemy = run.players
        self.assertTrue(match.switch_hero(mei, heroes()[MEI]))
        x, y, z = mei.mover.position
        enemy.mover.position = [x, y, z + 5.0]
        for _ in range(30):
            run.step({})
        target = next(t for t in match.combat._targets() if t.owner is enemy)
        return run, mei, enemy, target

    def fire(self, run, player, target, button, height, frames=45):
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(player), (
            target.feet[0], target.feet[1] + height, target.feet[2],
        ))
        for frame in range(frames):
            run.step({player: (yaw, pitch, button if frame < 20 else 0)})

    def test_icicle_body_damage_and_noncritical_notice(self):
        run, mei, _enemy, target = self.duel()
        self.fire(run, mei, target, 2, 0.9)
        self.assertEqual(run.match.combat.vitals_of(target).total, 125.0)
        hits = mei.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["+0x78"]["+0x4"], 75)
        self.assertEqual(hits[0]["+0x78"]["+0x8"], 0)

    def test_icicle_reconstructed_headshot_reaches_health_and_notice(self):
        run, mei, _enemy, target = self.duel()
        self.fire(run, mei, target, 2, 1.55)
        self.assertEqual(run.match.combat.vitals_of(target).total, 50.0)
        hits = mei.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["+0x78"]["+0x4"], 150)
        self.assertEqual(hits[0]["+0x78"]["+0x8"], combat.CRITICAL)

    def test_primary_spray_deals_damage_without_critical(self):
        run, mei, _enemy, target = self.duel()
        self.fire(run, mei, target, FIRE, 1.55)
        hits = mei.client.messages(combat.HIT)
        self.assertGreater(len(hits), 0)
        self.assertAlmostEqual(run.match.combat.vitals_of(target).total, 200 - len(hits) * 2.75)
        self.assertTrue(all(hit["+0x78"]["+0x8"] == 0 for hit in hits))

    def test_enemy_rifle_damages_mei(self):
        run, mei, enemy, _target = self.duel()
        target = next(t for t in run.match.combat._targets() if t.owner is mei)
        self.fire(run, enemy, target, FIRE, 0.9, frames=3)
        self.assertEqual(run.match.combat.vitals_of(target).total, 230.0)

    def test_icicle_respects_team_filter(self):
        run, mei, enemy, target = self.duel()
        # This map is FFA. Switch to a team rule without changing the geometry.
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        enemy.team = mei.team
        self.fire(run, mei, target, 2, 1.55)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200.0)
        self.assertEqual(mei.client.messages(combat.HIT), [])

    def test_icicle_lethal_headshot_marks_death_once(self):
        run, mei, enemy, target = self.duel()
        vitals = run.match.combat.vitals_of(target)
        vitals.damage(100.0)
        self.fire(run, mei, target, 2, 1.55)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0.0)
        self.assertEqual(len(mei.client.messages(combat.KILL_NOTICE)), 1)
        self.assertEqual(len(enemy.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertEqual(mei.client.messages(combat.HIT)[0]["+0x78"]["+0x8"],
                         combat.CRITICAL | combat.KILLING)

    def test_icicle_drains_shields_then_armour_then_health(self):
        run, mei, _enemy, target = self.duel()
        fight = run.match.combat
        vitals = Vitals(target.entity, 200.0, armour=50.0, shields=50.0)
        fight.vitals[target.entity] = vitals
        self.fire(run, mei, target, 2, 1.55)
        self.assertEqual(vitals.total, 155.0)
        self.assertEqual(mei.client.messages(combat.HIT)[0]["+0x78"]["+0x4"], 145)

    def test_projectile_falloff_uses_entire_flight_distance(self):
        run, mei, _enemy, target = self.duel()
        fight = run.match.combat
        # Explicit synthetic hit definition isolates the shared projectile path.
        rocket = Rocket(mei, mei.team, fight._eye(mei), (0.0, 0.0, 1.0), 10.0, 2.0,
                        HitDef(100.0, falloff_start=0.0, falloff_end=10.0,
                                 falloff_floor=0.0, critical=1.0), None, scale_direct=True)
        fight.rockets.append(rocket)
        for _ in range(40):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        expected = 100.0 * (1.0 - rocket.flown * rocket.speed / 10.0)
        self.assertAlmostEqual(fight.vitals_of(target).total, 200.0 - expected)
        self.assertGreater(rocket.flown, 0.4)

    def test_quick_melee_damages_nearby_enemy(self):
        run, mei, enemy, _target = self.duel()
        x, y, z = mei.mover.position
        enemy.mover.position = [x, y, z + 1.5]
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        self.fire(run, mei, target, 0x800, 0.9)
        self.assertEqual(run.match.combat.vitals_of(target).total, 170.0)

    def test_icicle_cannot_damage_through_static_wall(self):
        from unittest.mock import patch
        run, mei, _enemy, target = self.duel()
        # The shared collision API reports a wall in every projectile sweep.
        with patch.object(combat, "ray_world", return_value=0.1):
            self.fire(run, mei, target, 2, 1.55)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200.0)
        self.assertEqual(mei.client.messages(combat.HIT), [])

    def npc_duel(self):
        run, mei, _enemy, _target = self.duel()
        stock = Run().match.bots[0]
        stock.body_script = None
        stock.route = []
        stock.firing = False
        stock.team = mei.team + 1
        stock.retain_corpse = True
        x, y, z = mei.mover.position
        stock.position = (x, y, z + 5.0)
        run.match.bots.append(stock)
        other = run.players[1]
        other.spawn = ((x + 20.0, y, z + 5.0), other.spawn[1])
        self.assertTrue(run.match.switch_hero(other, other.hero))
        target = next(t for t in run.match.combat._targets() if t.owner is stock)
        return run, mei, stock, target

    def test_icicle_kills_retained_training_npc_without_respawn(self):
        run, mei, npc, target = self.npc_duel()
        vitals = run.match.combat.vitals_of(target)
        vitals.damage(vitals.total - 100.0)
        self.fire(run, mei, target, 2, 1.55)
        self.assertTrue(npc.dead)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0.0)
        for _ in range(220):
            run.step({})
        self.assertIn(npc, run.match.bots)
        self.assertFalse(any(who is npc for _when, who in run.match.combat.respawns))
        self.assertFalse(any(t.owner is npc for t in run.match.combat._enemies(mei)))

    def test_npc_projectile_path_damages_mei(self):
        run, mei, npc, _target = self.npc_duel()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mei)
        x, y, z = npc.position
        # Explicitly inject a flight, not an AI decision or a native weapon volley.
        fight.rockets.append(Rocket(npc, npc.team, (x, y + 1.0, z), (0.0, 0.0, -1.0),
                                    40.0, 1.0, HitDef(19.0), None))
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 231.0)


if __name__ == "__main__":
    unittest.main()
