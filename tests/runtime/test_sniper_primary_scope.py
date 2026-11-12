"""Public manual specials-only Sniper preserves the actual primary142C owner."""
import os
import sys
import math
import unittest
from pathlib import Path
from unittest.mock import patch

with patch.object(sys, "path", [*sys.path, str(Path(__file__).resolve().parents[2] / "tools")]):
    from tools import verify_sigma_npc_static as scenes
from ow174.game import npc, npc_control, sniper_reposition, sniper_retreat


class SniperPrimaryScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path(os.environ['ORPHEUS_PVE_NPC_DATA'])
        cls.definition = next(d for d in npc.load_catalog(directory / 'pve_npcs_174.json').values()
                              if d.name == 'Sniper')

    def test_specials_without_acquire_retreats_then_fires_original_primary(self):
        spawn = npc.spawn
        def manual(*args, **kwargs):
            return spawn(*args, **kwargs, specials=True, acquire=False, pursue=False)
        with patch.object(npc, 'spawn', side_effect=manual):
            result = scenes.sniper_retreat(self.definition, 'open')
        self.assertTrue(result['passed'], result)

    def test_ordinary_primary_and_stop_keep_original_tool_criteria(self):
        for mode in ('open', 'windup_stop'):
            with self.subTest(mode=mode):
                result = scenes.sniper_retreat(self.definition, mode)
                self.assertTrue(result['passed'], result)

    def test_explicit_and_acquired_modes_retain_same_component_ownership(self):
        for acquire, specials, explicit in ((False, False, True), (True, True, False)):
            with self.subTest(acquire=acquire, specials=specials, explicit=explicit):
                spawn = npc.spawn
                def configured(*args, **kwargs):
                    return spawn(*args, **kwargs, acquire=acquire, specials=specials)
                with patch.object(npc, 'spawn', side_effect=configured):
                    run, player, bot = scenes.arena(self.definition, 'heavy_melee')
                component = bot.body_script.component
                state = sniper_reposition.reposition_state(bot)
                self.assertEqual(sum(i.graph.index == 0x142C for i in component.instances.values()), 1)
                self.assertEqual(sniper_retreat.scoped(component.world, state.instance), acquire)
                if explicit:
                    x,y,z = bot.position
                    player.mover.yaw, player.mover.pitch = scenes.aim(run.match.combat._eye(player),(x,y+1.25,z))
                    npc_control.start(run.match, player, reposition=True)
                    self.assertFalse(sniper_reposition.primary_scoped(bot))
                else:
                    self.assertFalse(sniper_reposition.primary_scoped(bot))
                origin = bot.position
                for _ in range(165):
                    run.step({})
                self.assertGreater(math.dist(origin,bot.position), 1)
                self.assertIs(bot.body_script.component, component)
                self.assertIsNone(component.fault)
                self.assertEqual(sum(i.graph.index == 0x142C for i in component.instances.values()), 1)
