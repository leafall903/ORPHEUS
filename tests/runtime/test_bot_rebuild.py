"""The Training Bot puts itself together again after a respawn (its body graph 0BA8)."""

import unittest

from test_game_combat import Run

from ow174.game import bots, combat, observers
from ow174.game.content import PRACTICE_RANGE, PRACTICE_RANGE_MODE
from ow174.game.script import decode
from ow174.game.script.bot import REBUILD_GRAPH, BotScript
from ow174.game.statescript import chunk

REBUILD_STATE = 1  # 0BA8's second StateAnim: the branch taken while entity variable 7143 is off
SPAWNED = 100


def _rebuilding(script) -> bool:
    snapshot = observers.snapshot(script)
    rebuild = next(number for number, (index, _) in snapshot.instances.items() if index == REBUILD_GRAPH)
    return snapshot.states.get((rebuild, REBUILD_STATE), (None, False, None))[1]


class RebuildScriptTests(unittest.TestCase):
    def script(self, **options) -> BotScript:
        bot = next(bot for bot in bots.make(PRACTICE_RANGE.map_guid) if bot.name == "Training Bot")
        return BotScript(
            bot.body, bot.hero, bots.BOT_ENTITY, SPAWNED, PRACTICE_RANGE_MODE, bot.health, **options,
        )

    def test_a_first_spawn_plays_no_rebuild(self):
        self.assertFalse(_rebuilding(self.script()))

    def test_a_respawn_starts_the_rebuild_and_the_anim_stays_on(self):
        # The Anim is authored 2.5 s long with its automatic finish off (m_154DBA91), so the state stays.
        script = self.script(respawned=True)
        self.assertTrue(_rebuilding(script))
        for frame in range(SPAWNED + 1, SPAWNED + 250):  # 4 s
            script.advance_live(frame)
        self.assertTrue(_rebuilding(script))

    def test_the_respawned_bots_first_frame_has_the_anim_on(self):
        script = self.script(respawned=True)
        wire = chunk(script.initial[0], 0, 1)
        frame = decode.read_chunk(wire.getvalue(), wire.count, {})
        rebuild = next(item for item in frame.instances.values() if item.graph == REBUILD_GRAPH)
        self.assertTrue(rebuild.states[REBUILD_STATE][0])


class RebuildInMatchTests(unittest.TestCase):
    def test_only_the_bot_that_was_killed_comes_back_rebuilding(self):
        run = Run()
        player = run.players[0]
        fight = run.match.combat
        target = next(target for target in fight._enemies(player)
                      if isinstance(target.owner, bots.Bot) and target.owner.name == "Training Bot")
        bot = target.owner
        place = run.match.bots.index(bot)
        while bot.body_script is None:  # the match makes the bots' scripts a few at a time
            run.step({})
        self.assertFalse(_rebuilding(bot.body_script))
        fight._damage(player, target, bot.health + 100, False, None, run.frame)
        for _ in range(round(combat.BOT_RESPAWN / combat.TICK) + 10):
            run.step({})
        fresh = run.match.bots[place]
        self.assertIsNot(fresh, bot)
        self.assertTrue(_rebuilding(fresh.body_script))
        others = [item for item in run.match.bots if item is not fresh and item.name == "Training Bot"]
        self.assertFalse(any(_rebuilding(item.body_script) for item in others))


if __name__ == "__main__":
    unittest.main()
