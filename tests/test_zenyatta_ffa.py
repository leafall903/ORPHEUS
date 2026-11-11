"""Actual FFA input with same-number fixtures and normal deathmatch slots."""
import unittest
from dataclasses import replace

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.zenyatta import HERO


class ZenyattaFfaTests(unittest.TestCase):
    def test_third_deathmatch_slot_executes_zen_attacks_and_ultimate(self):
        run = Run(KANEZAKA_DM, ("Roadhog", "Soldier", "Zen"))
        tank, soldier, zen = run.players
        run.match.bots.clear()
        self.assertTrue(run.match.switch_hero(tank, heroes()[0x02E0000000000040]))
        self.assertTrue(run.match.switch_hero(zen, heroes()[HERO]))
        self.assertEqual(tuple(player.team for player in run.players), (0, 1, 2))
        x, y, z = KANEZAKA_DM.spawns[0].position
        for player, dx, dz in ((zen, 0, 0), (tank, 0, 2), (soldier, 4, 6)):
            player.mover.position = [x+dx, y, z+dz]
            player.mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        health = {target.owner: fight.vitals_of(target) for target in fight._targets()}
        script = zen.body_script
        charge = script.component.entity_var(342)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x164)

        def direction(player, target, height=1):
            tx, ty, tz = target.mover.position
            return aim(fight._eye(player), (tx, ty+height, tz))

        at_tank = direction(zen, tank)
        at_soldier = direction(zen, soldier)
        at_zen = direction(soldier, zen)
        start_charge, start_frame = charge.value(), run.frame
        run.step({zen: (*at_soldier, FIRE)})
        for _ in range(40):
            run.step({})
        self.assertEqual(health[soldier].total, 152)
        mark_aim = aim(fight._eye(zen), fight._eye(tank))
        run.step({zen: (*mark_aim, 0x08)})
        owner = fight.zenyatta.owners[zen.body]
        mark = owner.discord
        self.assertIsNotNone(mark)
        self.assertEqual(mark.target, tank.body)
        run.step({zen: (*at_tank, 0x800)})
        for _ in range(90):
            run.step({})
        self.assertEqual(health[tank].total, 562.5)
        tank.mover.position[2] = z+5
        tank.mover.body = None
        at_tank = direction(zen, tank, height=0.7)
        for _ in range(130):
            run.step({zen: (*at_tank, SECONDARY)})
        for _ in range(45):
            run.step({zen: (*at_tank, 0)})
        self.assertEqual(health[tank].total, 262.5)
        self.assertEqual(health[soldier].total, 152)
        self.assertIs(owner.discord, mark)
        self.assertEqual(weapon.vars[53].value(), 14)
        self.assertAlmostEqual(charge.value()-start_charge,
                               385.5+(run.frame-start_frame)*combat.TICK*5, places=3)
        hits = [message["+0x78"] for message in zen.client.messages(combat.HIT)]
        self.assertEqual([hit["+0x0"]["+0x0"] for hit in hits], [soldier.body]+[tank.body]*6)
        self.assertEqual([hit["+0x4"] for hit in hits[2:]], [60]*5)

        mark_aim = aim(fight._eye(zen), fight._eye(tank))
        run.step({zen: (*mark_aim, 0x10), soldier: (*at_zen, FIRE)})
        run.step({})
        run.step({zen: (*aim(fight._eye(zen), fight._eye(soldier)), 0x10)})
        self.assertIsNone(owner.harmony)
        self.assertNotIn("harmony", owner.ready)
        self.assertEqual(health[zen].total, 180)
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({zen: (*at_tank, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(health[zen].total, 200)
        self.assertEqual(charge.value(), 0)
        for _ in range(40):
            run.step({})
        run.step({soldier: (*at_zen, FIRE)})
        self.assertEqual(health[zen].total, 200)
        for _ in range(380):
            if not fight.zenyatta.ultimate(zen):
                break
            run.step({})
        self.assertFalse(fight.zenyatta.ultimate(zen))
        self.assertEqual(health[tank].total, 262.5)
        self.assertEqual(health[soldier].total, 152)
        run.step({soldier: (*at_zen, FIRE)})
        self.assertEqual(health[zen].total, 180)
        self.assertIs(zen.body_script, script)
        self.assertFalse(zen.body_script_failed)

    def test_same_team_number_allows_discord_and_damage_but_no_support_healing(self):
        game_map = replace(PRACTICE_RANGE, free_for_all=True, team_sizes=(8,))
        run = Run(game_map, ("Zen", "Opponent"))
        zen, opponent = run.players
        run.match.bots.clear()
        self.assertTrue(run.match.switch_hero(zen, heroes()[HERO]))
        zen.team = opponent.team = 0
        x, y, z = zen.mover.position
        opponent.mover.position = [x, y, z+5]
        opponent.mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        health = {t.owner: fight.vitals_of(t) for t in fight._targets()}
        health[opponent].damage(60)
        opponent_before = health[opponent].total
        angles = aim(fight._eye(zen), (x, y+1, z+5))
        run.step({zen: (*angles, 0x10)})
        for _ in range(20):
            run.step({})
        owner = fight.zenyatta.owners[zen.body]
        self.assertIsNone(owner.harmony)
        self.assertNotIn("harmony", owner.ready)
        self.assertEqual(health[opponent].total, opponent_before)
        run.step({zen: (*angles, 0x08)})
        run.step({})
        self.assertEqual(owner.discord.target, opponent.body)
        run.step({zen: (*angles, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(health[opponent].total, opponent_before-60)
        opponent_before = health[opponent].total

        health[zen].damage(100)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: (*angles, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        opponent_aim = aim(fight._eye(opponent), (x, y+1, z))
        run.step({opponent: (*opponent_aim, FIRE)})
        for _ in range(60):
            run.step({})
        self.assertEqual(health[zen].total, 200)
        self.assertEqual(health[opponent].total, opponent_before)
        self.assertEqual(owner.discord.target, opponent.body)
        for _ in range(400):
            if not fight.zenyatta.ultimate(zen):
                break
            run.step({})
        self.assertFalse(fight.zenyatta.ultimate(zen))
        run.step({opponent: (*opponent_aim, FIRE)})
        for _ in range(10):
            run.step({})
        self.assertLess(health[zen].total, 200)
        replaced_body = opponent.body
        self.assertEqual(owner.discord.target, replaced_body)
        self.assertTrue(run.match.switch_hero(opponent, heroes()[HERO]))
        run.step({})
        self.assertNotEqual(opponent.body, replaced_body)
        self.assertIsNone(owner.discord)
