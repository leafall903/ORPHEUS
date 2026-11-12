"""Original 34924 live probe cases on the combined server and current ID storage."""
from dataclasses import replace
from pathlib import Path
import os
import unittest
import test_junkrat_npc_combat as fixtures
from test_game_mover import box, wall
from ow174.game import npc
from ow174.game.collision import World
from ow174.game.content import find_hero
from ow174.game.script import expr, graph, runtime, scene, scene_host


class ThunderstormCombatTests(unittest.TestCase):
    def test_original_live_ray_cases_and_mode_toggle(self):
        directory = os.environ.get('ORPHEUS_PVE_NPC_DATA')
        if directory is None:
            self.skipTest('prepared real NPC catalog not configured')
        case = fixtures.JunkratNpcCombatTests()
        case.catalog = npc.load_catalog(Path(directory)/'pve_npcs_174.json')
        definition = next(row for row in case.catalog.values() if row.name == 'Enforcer')
        mode_guid = 0x0230000000000043
        values = {34924: True}
        rows = []
        helpers = fixtures.helpers
        shared = graph.graph(0x2535)
        chain = ((71, 'm_outPlug', 67), (67, 'm_trueSubgraphPlug', 69),
                 (69, 'm_trueSubgraphPlug', 70), (70, 'm_subgraphPlug', 74),
                 (74, 'm_C41A9BE2', 77), (77, 'm_subgraphPlug', 76),
                 (76, 'm_trueSubgraphPlug', 68))
        for node, field, target in chain:
            assert any(link[0] == target for link in shared.node(node).field(field + '.links'))
        assert shared.node(70).field('m_radius.m_value') == 7
        assert shared.node(77).field('m_castLength.m_value') == 8
        assert shared.node(68).field('m_modifyHealth.m_amount.m_value') == 25
        run, player, old, _ = case.create_fight(definition, distance=5)
        match = run.match
        match.game_map = replace(match.game_map, mode_guid=mode_guid, free_for_all=False)
        assert npc.remove_spawned(match) == (old.entity,)
        assert match.switch_hero(player, find_hero('Junkrat'))
        world = scene.SceneWorld(mode=mode_guid)
        world.bind(runtime.GAME_MODE_ENTITY)
        host = scene_host.SceneHost(world)
        slots = values
        mode = host.add(runtime.GAME_MODE_ENTITY, {'$': 'STUStatescriptComponent', 'm_B634821A': []}, values=slots)
        match.scene_host = host
        bot = npc.spawn(match, definition, tuple(old.position), team=1, yaw=-32768)
        instance, = (i for i in bot.body_script.component.instances.values() if i.graph.index == 0x2535)
        enemy_team = player.team
        for label, distance, team, blocked, expected in (
                ('clear', 5, enemy_team, False, 6.4),
                ('wall', 5, enemy_team, True, 0),
                ('range', 12, enemy_team, False, 0),
                ('ally', 5, bot.team, False, 0),
                ('clear_again', 5, enemy_team, False, 6.4)):
            x, y, z = bot.position
            ground = box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            cover = wall(x + 2.5, z - 2, x + 2.5, z + 2, y0=y, y1=y + 4) if blocked else []
            match.collision_world = World(ground + cover)
            helpers.JunkratProjectileTests.place(case, run, player, (x + distance, y, z))
            player.team = team
            start = helpers.JunkratProjectileTests.health(case, run, player).total
            for _ in range(16):
                run.step({})
            health = helpers.JunkratProjectileTests.health(case, run, player)
            loss = start - health.total
            tracker = instance.states[instance.graph.node(70).state]
            tracked = player.body in set(tracker.targets)
            assert tracked == (label in ('clear','wall','clear_again'))
            assert not host.error
            row = dict(case=label, tracked=tracked, damage=round(loss, 4), expected=expected)
            assert abs(loss-expected) < 0.001, row
            rows.append(row)
        for enabled in (False, True):
            mode.set(34924, enabled)
            health = helpers.JunkratProjectileTests.health(case, run, player)
            before = health.total
            for _ in range(16):
                run.step({})
            loss = before - health.total
            expected = 6.4 if enabled else 0
            row = dict(case='re_enabled' if enabled else 'disabled', damage=round(loss, 4), expected=expected)
            assert abs(loss-expected) < 0.001, row
            rows.append(row)
