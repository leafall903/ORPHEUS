"""Original0016 orb/actual-NPC route consumers and unchanged assertions."""
import math, os, unittest
from pathlib import Path
from unittest.mock import patch
from test_game_combat import FakeClient, aim
from test_symmetra_combat import arena, health
from test_symmetra_charge import charge
from ow174.game import combat, npc, stats
from ow174.game.script.bot import BotScript
from ow174.game.script.driver import OWNER_HEALTH

@unittest.skipUnless(os.environ.get('ORPHEUS_NPC_CATALOG'), 'requires explicit original five-NPC catalog')
class OrbRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definitions = npc.load_catalog(Path(os.environ['ORPHEUS_NPC_CATALOG']))

    def create(self, definition, *, distance=4, ally=False):
        run, player, other = arena()
        other.mover.position = [40, 1, 40]
        bot = npc.spawn(run.match, definition, (0, 1, distance),
                        team=player.team if ally else 1 - player.team)
        self.assertIsInstance(bot.body_script, BotScript)
        self.assertIs(bot.movement_data, definition.mover)
        self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(),
                         definition.health + definition.armour + definition.shields)
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        self.assertEqual((target.height, target.radius),
                         (definition.mover.stand_height, definition.mover.stand_radius))
        return run, player, bot, target

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_orb_uses_all_five_npc_route_poses_at_contact(self):
        for definition in self.definitions.values():
            for entering in (False, True):
                for move_early in (False, True):
                    with self.subTest(npc=definition.name, entering=entering, move_early=move_early):
                        args = (definition, entering, move_early)
                        stepped = self.orb_across_npc_route(*args, gap=False)
                        self.assertEqual(self.orb_across_npc_route(*args, gap=True), stepped)

    def orb_across_npc_route(self, definition, entering, move_early, *, gap):
        run, sym, bot, _ = self.create(definition, distance=12)
        fight = run.match.combat
        bot.position = (1.5 if entering else 0.0, 1.0, 12.0)
        bot.route = (bot.position,)
        bot.wait = 5.0
        angles = aim(fight._eye(sym), (0, 1.7, 12))
        starting_pool = health(run, bot).total
        for _ in range(90):
            run.step({sym: (*angles, 2)})
        run.step({sym: (*angles, 0)})
        run.step({})
        [orb] = fight.rockets
        start = run.frame
        self.assertEqual(orb.last_tick, start)
        self.assertEqual(bot.last_tick, start)
        start_charge = charge(sym)
        bot.route = ((0.0 if entering else 30.0, 1.0, 12.0),)
        bot.wait = 0.0 if move_early else 0.75
        end = start + 180
        damage_times = []
        damage = fight._damage
        def observe_damage(*args, **kwargs):
            before = health(run, bot).total
            outcome = damage(*args, **kwargs)
            if health(run, bot).total < before:
                damage_times.append(fight.now)
            return outcome
        with patch.object(fight, '_damage', side_effect=observe_damage):
            for tick in (end,) if gap else range(start + 1, end + 1):
                run.match.update(tick * combat.TICK, tick)
        self.assertFalse(fight.rockets)
        self.assertLess(orb.last_tick, end - combat.COMBAT_CATCH_UP + 1)
        if entering:
            self.assertAlmostEqual(bot.position[0], 0, places=3)
        else:
            self.assertGreater(bot.position[0], 3)
        vitals = health(run, bot)
        damage = starting_pool - vitals.total
        self.assertEqual(damage, 120 if entering == move_early else 0)
        self.assertEqual(sym.stats.value(stats.HERO_DAMAGE), damage)
        charge_gain = charge(sym) - start_charge
        self.assertLessEqual(damage + 10, charge_gain)
        self.assertLessEqual(charge_gain, damage + 15)
        if damage:
            self.assertAlmostEqual((damage_times[-1] if damage_times else None), orb.last_tick * combat.TICK)
        else:
            self.assertIsNone((damage_times[-1] if damage_times else None))
        return orb.last_tick, damage, charge_gain, bot.position, bot.velocity
