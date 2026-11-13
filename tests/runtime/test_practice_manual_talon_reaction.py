"""Existing Talon reaction/voice consumers for eligible Practice manual bodies."""
import dataclasses
import unittest

from ow174.game import combat, mccree, npc, voicelines
from ow174.game.content import SOLDIER, heroes
from tests.runtime import test_mccree_roll as roll_cases


class PracticeTalonReactionTests(unittest.TestCase):
    def arena(self, hero):
        helper = roll_cases.McCreeRollTests()
        run, player = helper.arena()
        if player.hero.guid != hero:
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.mover.position[:] = (0, 0, 0)
            player.mover.velocity = (0, 0, 0)
            player.mover.body = None
        bot = npc.spawn(run.match, npc.Definition(npc.TROOPER_HERO, 0x0400000000001D40,
                                                'Reaction target', 200), (0, 0, 1.8),
                        team=1 - player.team)
        return run, player, bot

    def test_cassidy_melee_and_foreign_source_control_reuse_talon_animation_and_voice(self):
        for hero in (mccree.HERO, SOLDIER):
            with self.subTest(hero=hero):
                run, player, bot = self.arena(hero)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                run.step({player: (0, 0, 0x800)})
                for _ in range(30):
                    run.step({})
                    if fight.vitals_of(target).total < 200:
                        break
                self.assertEqual(fight.vitals_of(target).total, 170)
                if hero == SOLDIER:
                    # Preserve the existing melee policy: Soldier's normal hit
                    # does not request this scoped animation. The production
                    # control-effect adapter accepts a foreign source (e.g. a
                    # reflected/copied effect); it must not become Cassidy-only.
                    self.assertNotIn(bot.entity, fight.npc_hit_reactions)
                    fight._hit_effect(target, combat.FLASHBANG_EFFECT, shooter=player)
                    self.assertTrue(fight.is_stunned(bot))
                deadline, instance = fight.npc_hit_reactions[bot.entity]
                self.assertEqual(instance.graph.index, combat.TALON_HIT_REACTION)
                self.assertTrue(instance.node_state(instance.graph.node(0)).active)
                self.assertGreater(deadline, run.frame)
                notices = player.client.messages(voicelines.TALON_HIT_NOTICE)
                self.assertEqual(len(notices), 1)
                self.assertEqual(notices[0]['+0x90'], voicelines.TALON_HIT_LINE)
                fight._begin_npc_hit_reaction(target, 0.48, deadline + 10,
                                             restart=False, shooter=player)
                self.assertIs(fight.npc_hit_reactions[bot.entity][1], instance)
                self.assertEqual(len(player.client.messages(voicelines.TALON_HIT_NOTICE)), 1)
                for _ in range(60):
                    run.step({})
                self.assertNotIn(bot.entity, fight.npc_hit_reactions)

    def test_other_maps_nonmanual_and_unknown_bodies_keep_existing_guards(self):
        for kind in ('other_map', 'nonmanual', 'unknown_body'):
            with self.subTest(kind=kind):
                run, player, bot = self.arena(mccree.HERO)
                if kind == 'other_map':
                    run.match.game_map = dataclasses.replace(run.match.game_map, map_guid=0x080000000000FFFF)
                elif kind == 'nonmanual':
                    bot.spawner['manual_spawn'] = False
                else:
                    bot.body = player.hero.body
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                fight._begin_npc_hit_reaction(target, 0.48, run.frame + 30, shooter=player)
                self.assertNotIn(bot.entity, fight.npc_hit_reactions)
                self.assertFalse(player.client.messages(voicelines.TALON_HIT_NOTICE))
