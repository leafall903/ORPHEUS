"""Retain the original0020 real-hit shield recovery assertions.

Only the arena adapter omits the unsupported explicit default difficulty=None.
Nondefault difficulty remains a separate unmerged NPC pool consumer.
"""

import math
import os
import unittest
from itertools import product
from pathlib import Path
from unittest.mock import patch
from test_game_combat import Run, aim
from test_game_mover import box, wall
from ow174.game import combat, npc, npc_context, npc_control, npc_shields, stats
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.mover import mover_data
from ow174.game.script import graph
from ow174.game.zenyatta import HERO

@unittest.skipUnless(os.environ.get('ORPHEUS_NPC_DATA'), 'needs explicit external prepared NPC data')
class ZenyattaNpcTests(unittest.TestCase):

    @classmethod
    def clear_graph_cache(cls):
        for name in ('_data', '_records', 'graph', 'bodies', 'entity_body', 'entity_script_component'):
            function = getattr(graph, name)
            if hasattr(function, 'cache_clear'):
                function.cache_clear()

    @classmethod
    def setUpClass(cls):
        cls.old_path = graph.GRAPHS_PATH
        folder = Path(os.environ['ORPHEUS_NPC_DATA'])
        cls.catalog = npc.load_catalog(folder / 'pve_npcs_174.json')
        dimensions = {'1': (1.7, 0.5), '2': (1.9, 0.5), '3': (1.75, 0.5), '4': (1.5, 0.5), '5': (2.3, 0.7)}
        for key, (height, radius) in dimensions.items():
            data = mover_data(cls.catalog[key].body)
            if not (math.isclose(data.stand_height, height, abs_tol=1e-05) and math.isclose(data.stand_radius, radius, abs_tol=1e-05)):
                raise AssertionError('NPC combat evidence requires actual104319 mover rows; generic fallback geometry is not accepted')
        graph.GRAPHS_PATH = folder / 'game_graphs_174.json.gz'
        cls.clear_graph_cache()

    @classmethod
    def tearDownClass(cls):
        graph.GRAPHS_PATH = cls.old_path
        cls.clear_graph_cache()

    def arena(self, definition, team=1, names=('A',), *, difficulty_index=None):
        run = Run(names=names)
        zen = run.players[0]
        run.match.switch_hero(zen, heroes()[HERO])
        run.match.bots.clear()
        run.step({})
        for player in run.players:
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
            player.client.bot_stream = lambda body: None
        x, y, z = zen.mover.position
        bot = npc.spawn(run.match, definition, (x, y, z + 8), team=team, yaw=-32768, **({'difficulty_index': difficulty_index} if difficulty_index is not None else {}))
        self.assertIsNotNone(bot.body_script)
        self.assertTrue(bot.retain_corpse)
        return (run, zen, bot, run.match.combat.vitals[bot.entity])

    def test_enforcer_authored_shield_wait_rearms_on_real_zen_hits_and_recovers(self):
        run, zen, bot, vitals = self.arena(self.catalog['2'])
        component = bot.body_script.component
        graph, = (i for i in component.instances.values() if i.graph.index == 3682)
        shields = combat.SHIELDS_POOL

        def fire(expected):
            run.step({zen: (0, 600, FIRE)})
            for _ in range(25):
                run.step({})
                if vitals.now[shields] == expected:
                    break
            self.assertEqual(vitals.now[shields], expected)
        fire(52)
        wait = graph.states[graph.graph.node(52).state]
        self.assertTrue(wait.active)
        self.assertEqual(wait.ms, 3000)
        first_deadline = wait.start + wait.ms
        for _ in range(80):
            run.step({})
        fire(4)
        deadline = wait.start + wait.ms
        self.assertGreater(deadline, first_deadline)
        while component.now + 16 < deadline:
            run.step({})
            self.assertEqual(vitals.now[shields], 4)
        for _ in range(3):
            run.step({})
            if vitals.now[shields] > 4:
                break
        self.assertFalse(wait.active)
        self.assertGreater(component.now, deadline)
        self.assertAlmostEqual(vitals.now[shields], 4 + 0.03 * (component.now - deadline), places=5)
        regen = graph.states[graph.graph.node(51).state]
        self.assertTrue(regen.active)
        charge = zen.body_script.component.entity_var(342).value()
        first_regen_frame = run.frame
        for _ in range(220):
            run.step({})
        self.assertEqual(vitals.now[shields], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 250)
        self.assertFalse(regen.active)
        self.assertAlmostEqual(zen.body_script.component.entity_var(342).value() - charge, (run.frame - first_regen_frame) * 0.08, places=2)
