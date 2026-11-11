"""Actual NPC primary suppression while fighting D.Va; native immunity/AI OPEN."""

import unittest
from dataclasses import replace

import test_game_dva_npc_combat as incoming
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, npc
from ow174.game.commands import Command
from ow174.game.content import find_hero, heroes


class NpcControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        incoming.NpcIncomingTests.setUpClass.__func__(cls)

    def scenario(self, name):
        run = Run(KANEZAKA_DM, ("McCree", "DVa"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        caster, target = run.players
        self.assertTrue(match.switch_hero(caster, heroes()[0x02E0000000000042]))
        self.assertTrue(match.switch_hero(target, replace(find_hero("dva"), body=incoming.MECH)))
        caster.mover.position, target.mover.position = [0, 1, -3], [0, 1, 6]
        for player in run.players:
            player.mover.body = None
        for _ in range(100):
            run.step({})
        bot = npc.spawn(match, self.by_name[name], (0, 1, 0), team=1)
        body = next(t for t in match.combat._targets() if t.owner is target)
        return run, caster, bot, match.combat.vitals_of(body)

    def step(self, run, caster, bot, cast=0):
        run.frame += 1
        match = run.match
        match.update(run.frame * combat.TICK, run.frame)
        yaw, pitch = aim(match.combat._eye(caster),
                         (bot.position[0], bot.position[1] + 1, bot.position[2]))
        for player in run.players:
            player.client.queue_entities(match.remote_movements(player))
            player.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=cast)
                                  if player is caster else Command(run.frame)])

    def test_actual_grenade_stops_three_npc_primary_sources_and_explicit_repress_recovers(self):
        for name, (_, armour_damage, lead) in incoming.PRIMARY.items():
            with self.subTest(npc=name):
                run, caster, bot, target = self.scenario(name)
                script = bot.body_script
                script.component.edges([(20, True)])
                bot.firing = True
                for _ in range(lead):
                    self.step(run, caster, bot)
                self.assertEqual(target.total, 600)
                before = run.match.combat.vitals[bot.entity].total
                self.step(run, caster, bot, 16)
                for _ in range(60):
                    if run.match.combat.is_stunned(bot):
                        break
                    self.step(run, caster, bot)
                self.assertTrue(run.match.combat.is_stunned(bot))
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before - 25)
                end = run.match.combat.stuns[bot.entity]
                while run.frame < end:
                    self.assertNotIn(20, script.component.held)
                    self.assertEqual(target.total, 600)
                    self.step(run, caster, bot)
                self.assertFalse(run.match.combat.is_stunned(bot))
                script.component.edges([(20, True)])  # controlled primary input, not autonomous recovery
                for _ in range(300):
                    self.step(run, caster, bot)
                    if target.total < 600:
                        break
                self.assertEqual(target.total, 600 - armour_damage)
                self.assertIs(bot.body_script, script)


if __name__ == "__main__":
    unittest.main()
