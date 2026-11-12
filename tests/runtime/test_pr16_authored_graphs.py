"""PR #16 regressions that use authored target-build hero and scene graphs."""

import unittest
from types import SimpleNamespace

from ow174.game.combat import Vitals
from ow174.game.junkenstein_dialogue import Playback
from ow174.game.script import expr, nodes, runtime, scene
from tests.public.test_script_scene_host import A, ValueWorld, component
from tests.public.test_script_voice_stimulus import OWNER


class DeadeyeJoinTests(unittest.TestCase):
    def arena(self):
        from test_game_combat import Run

        from ow174.game.content import heroes

        run = Run()
        player = run.players[0]
        run.match.bots.clear()
        self.assertTrue(run.match.switch_hero(player, heroes()[0x02E0000000000042]))
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.vars[338].value())
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x22F)
        ability = instance.state(instance.graph.node(1).state)
        return run, player, instance, ability

    def test_actual_deadeye_survives_warmup_and_expires_after_authored_six_seconds(self):
        run, player, instance, ability = self.arena()
        run.step({player: (0, 0, 32)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(300):
            run.step({})
            self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(instance.vars[567].value(), 6)
        self.assertEqual(player.body_script.component.vars[342].value(), 0)
        for _ in range(120):
            run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)

    def test_actual_secondary_cancel_leaves_normal_ammo_untouched(self):
        run, player, _, ability = self.arena()
        run.step({player: (0, 0, 32)})
        for _ in range(30):
            run.step({})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        run.step({player: (0, 0, 2)})
        for _ in range(30):
            run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1D2)
        self.assertEqual(weapon.vars[53].value(), 6)
        script = player.body_script
        script.set_ult_charge(script.component.vars[338].value())
        run.step({player: (0, 0, 32)})
        for _ in range(30):
            run.step({})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        run.step({player: (0, 0, 2)})
        for _ in range(30):
            run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(weapon.vars[53].value(), 6)


class JunkensteinGraphTests(unittest.TestCase):
    def test_junkenstein_narration_uses_authored_hero_variants(self):
        mode = runtime.GAME_MODE_ENTITY
        world = scene.SceneWorld()
        world.bind(mode)
        world.bind(OWNER, hero=0x02E000000000006E)
        world.bind_voice_set(mode, 0x07A000000000013B)
        component = runtime.Component(mode, world=world, strict=True)
        world.bind_script_component(mode, component)
        root = scene.graph.graph(0x0C8C)
        instance = component.create(root, init=False)
        playback = Playback(SimpleNamespace(players=[]), {}, 0)

        victory = playback.stimulus(0x0EE0000000000CB7)
        state = instance.node_state(root.node(221))
        component.set(8602, expr.Asset(0x02E0000000000016))
        self.assertEqual(playback.submit(state, victory, (mode,)), 0xA3EE)
        self.assertEqual(playback.duration(0xA3EE, victory.guid, True), 603456 / 48000)
        component.set(8602, None)
        self.assertEqual(playback.submit(state, victory, (mode,)), 0x8CF1)

        casualty = playback.stimulus(0x0EE0000000000924)
        state = instance.node_state(root.node(241))
        component.set(8619, expr.Entity(OWNER))
        self.assertEqual(playback.submit(state, casualty, (mode,)), 0xA3E6)
        world.unbind(OWNER)
        self.assertEqual(playback.submit(state, casualty, (mode,)), 0)

    def test_reaper_wave_health_uses_the_authored_difficulty_amount(self):
        for difficulty, amount in enumerate((750, 1000, 1350, 1750)):
            with self.subTest(difficulty=difficulty):
                world = ValueWorld()
                owner = runtime.GAME_MODE_ENTITY
                world.bind(owner)
                mode_vm, _ = component(world, owner)
                mode_vm.set(9948, difficulty)
                vitals = Vitals(A, 250)
                world.bind(A, pool_capacity=vitals.most.get)
                world.wave_health = lambda _, value, body=vitals: body.add_wave_health(value)
                model = scene.graph.graph(0x0CBC)
                _, instance = component(world, A, model)
                instance.write((expr.INSTANCE, 9948), difficulty)
                state = instance.state(model.node(1).state)
                state.begin()
                self.assertEqual(vitals.total, 250 + amount)
                state.end(False)
                self.assertEqual(vitals.total, 250)
