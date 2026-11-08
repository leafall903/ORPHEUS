"""Ana's reconstructed rifle healing through actual body commands and combat geometry.

These checks do not accept native filters, delivery timing or original-client visuals.
"""

import unittest
from dataclasses import replace

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, stats
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import PRACTICE_RANGE, heroes

ANA = 0x02E000000000013B
TEAM_MAP = replace(PRACTICE_RANGE, team_sizes=(6, 6))


class AnaRifleTests(unittest.TestCase):
    def setup_fight(self, *, npc=False, enemy=False, ffa=False, distance=6.0):
        run = Run(KANEZAKA_DM if ffa else TEAM_MAP, ("Ana", "Target"))
        shooter, player = run.players
        match = run.match
        self.assertTrue(match.switch_hero(shooter, heroes()[ANA]))
        if npc:
            target = match.bots[0]
            match.bots[:] = [target]
            target.route = []
            target.firing = False
        else:
            target = player
            match.bots.clear()
        match._pending_bot_scripts.clear()
        shooter.team = 0
        target.team = 1 if enemy else 0
        x, y, z = shooter.mover.position
        if npc:
            target.position = (x, y, z + distance)
            player.mover.position = [x + 30, y, z]
        else:
            target.mover.position = [x, y, z + distance]
        for _ in range(25):
            run.step({})
        entry = next(t for t in match.combat._targets() if t.owner is target)
        return run, shooter, target, entry

    def shoot(self, run, shooter, entry, scoped):
        direction = aim(run.match.combat._eye(shooter),
                        (entry.feet[0], entry.feet[1] + 1.0, entry.feet[2]))
        if scoped:
            for _ in range(35):
                run.step({shooter: (*direction, SECONDARY)})
        run.step({shooter: (*direction, FIRE | (SECONDARY if scoped else 0))})
        for _ in range(35):
            run.step({shooter: (*direction, SECONDARY if scoped else 0)})

    def test_hip_and_scoped_heal_players_and_npcs_without_damage_notices(self):
        for npc in (False, True):
            for scoped in (False, True):
                with self.subTest(npc=npc, scoped=scoped):
                    run, shooter, _target, entry = self.setup_fight(npc=npc)
                    vitals = run.match.combat.vitals_of(entry)
                    vitals.damage(100)
                    before = vitals.total
                    self.shoot(run, shooter, entry, scoped)
                    self.assertEqual(vitals.total, before + 70)
                    self.assertFalse(shooter.client.messages(combat.HIT))
                    self.assertEqual(shooter.stats.value(stats.HEALING), 70)

    def test_enemies_take_damage_including_ffa_same_team(self):
        for npc, ffa in ((False, False), (True, False), (False, True)):
            for scoped in (False, True):
                with self.subTest(npc=npc, ffa=ffa, scoped=scoped):
                    run, shooter, _target, entry = self.setup_fight(npc=npc, enemy=not ffa, ffa=ffa)
                    vitals = run.match.combat.vitals_of(entry)
                    before = vitals.total
                    self.shoot(run, shooter, entry, scoped)
                    self.assertEqual(vitals.total, before - 70)
                    self.assertTrue(shooter.client.messages(combat.HIT))

    def test_healing_clamps_and_dead_targets_do_not_revive(self):
        run, shooter, _target, entry = self.setup_fight()
        fight = run.match.combat
        vitals = fight.vitals_of(entry)
        vitals.damage(10)
        self.shoot(run, shooter, entry, False)
        self.assertEqual(vitals.total, sum(vitals.most.values()))
        vitals.damage(1000)
        vitals.died_at = fight.now
        self.assertNotIn(entry, fight._shot_targets(shooter, combat.HitDef(70, ally_heal=70)))
        self.assertEqual(fight._heal(shooter, entry, 70), 0)
        self.assertEqual(vitals.total, 0)

    def test_full_health_ally_is_transparent_to_enemy_behind(self):
        run, shooter, _target, entry = self.setup_fight()
        fight = run.match.combat
        # Put an enemy NPC behind the healthy friendly player on the same ray.
        from ow174.game import bots

        npc = bots.Bot(0x9000, {"hero": "0x02E000000000013B", "team": 1,
                              "position": [entry.feet[0], entry.feet[1], entry.feet[2] + 4],
                              "rotation": [0, 0, 0, 1], "graphs": []},
                       {"name": "Enemy", "body": "0x0400000000001424", "health": 200}, {})
        run.match.bots.append(npc)
        for _ in range(2):
            run.step({})
        self.shoot(run, shooter, entry, False)
        enemy = next(t for t in fight._targets() if t.owner is npc)
        self.assertEqual(fight.vitals_of(enemy).total, 130)
        self.assertEqual(fight.vitals_of(entry).total, 200)

    def test_enemy_player_can_damage_ana_through_actual_weapon_commands(self):
        run, ana, attacker, _entry = self.setup_fight(enemy=True)
        fight = run.match.combat
        x, y, z = ana.mover.position
        direction = aim(fight._eye(attacker), (x, y + 1, z))
        target = next(t for t in fight._targets() if t.owner is ana)
        before = fight.vitals_of(target).total
        run.step({attacker: (*direction, FIRE)})
        for _ in range(5):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, before - 20)

    def test_ana_melee_damages_enemy_player_and_npc_but_not_ally(self):
        for npc, enemy in ((False, True), (True, True), (False, False)):
            with self.subTest(npc=npc, enemy=enemy):
                run, ana, _target, entry = self.setup_fight(npc=npc, enemy=enemy, distance=1.5)
                x, y, z = ana.mover.position
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                before = vitals.total
                direction = aim(fight._eye(ana), (x, y + 1, z + 1.5))
                run.step({ana: (*direction, 0x800)})
                for _ in range(35):
                    run.step({})
                self.assertEqual(vitals.total, before - (30 if enemy else 0))


if __name__ == "__main__":
    unittest.main()
