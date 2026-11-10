"""Reconstructed pin clock/control tests; synthetic1157 graph, actual Match/vitals/input."""

import unittest
from unittest.mock import patch

from test_game_combat import Run, aim
from test_game_graph_analysis import make_graph, node, output

from ow174.game import assassin, combat, npc, npc_control
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.content import heroes
from ow174.game.script import expr, graph, nodes


class AssassinPinTests(unittest.TestCase):
    def start(self, pin):
        pin.begin()
        pin.instance.component.run_now()

    def arena(self, *, mode=1):
        run = Run(names=("McCree", "ally"))
        match, player = run.match, run.players[0]
        match.switch_hero(player, heroes()[0x02E0000000000042])
        sample = match.bots[0]
        match.bots.clear()
        for participant in run.players:
            participant.client.queue_bot_death = lambda *args: None
            participant.client.queue_bot_death_update = lambda *args: None
        x, y, z = player.mover.position
        run.players[1].mover.position[:] = (x + 20, y, z)
        phase = 15 if mode else 25
        damage = {"$": "STUConfigVarFloat", "m_value": 100 if mode else 25}
        definition = make_graph(assassin.GRAPH, [
            node(23, state=0, fields={
                "m_subgraphPlug": {**output([phase, "m_beginPlug"]), "m_FF3DAF1E": [1 if mode else 2]},
                "m_onEndPlug": output([31, "m_beginPlug"]),
            }),
            *[node(pos, "STUStatescriptStateModifyHealth", state=index, fields={
                "m_FA3ECC43": {"$": "STUConfigVarDynamic", "m_identifier": 536},
                "m_95F1FB0B": {"$": "STUConfigVarInt", "m_value": mode},
                "m_modifyHealth": {"m_amount": damage},
            }) for index, pos in enumerate((15, 25), 1)],
            node(37, "STUStatescriptStateWait", state=3,
                 fields={"m_timeout": {"$": "STUConfigVarInt", "m_value": 2}}),
            node(31, state=4),
        ], states=[23, 15, 25, 37, 31])
        record = {"graphs": [{"graph": assassin.GRAPH}], "weapons": [], "manager": None,
                  "ai_weapon_buttons": {"0": 20}}
        original = graph.graph
        with (patch.object(graph, "entity_body", return_value=record),
              patch.object(graph, "graph", side_effect=lambda i:
                           definition if i == assassin.GRAPH else original(i))):
            actor = npc.spawn(match, npc.Definition(assassin.HERO, sample.body, "Pin fixture", 500),
                              (x, y, z + 2), team=1)
        actor.attack_target, actor.firing = player.entity, True
        component = actor.body_script.component
        world = component.world
        assassin.bind(world, component, player.body)
        instance = next(iter(component.instances.values()))
        instance.write((expr.INSTANCE, 536), expr.Entity(player.body))
        target = next(t for t in match.combat._targets() if t.owner is player)
        health = match.combat.vitals_of(target)
        pin = instance.state(0)
        return run, actor, player, world, component, instance, health, pin

    def test_authored_rate_uses_elapsed_time_without_entry_or_duplicate_frame_damage(self):
        _, _, player, world, component, instance, health, pin = self.arena()
        before = health.total
        self.start(pin)
        self.assertEqual(health.total, before)
        self.assertEqual(world.match.combat.pins[component.entity], player.body)
        frame = component.frame
        component.run_frame(frame + 1)
        self.assertAlmostEqual(health.total, before - 1.6, places=5)
        component.run_frame(frame + 1)
        self.assertAlmostEqual(health.total, before - 1.6, places=5)
        component.run_frame(frame + 11)
        self.assertAlmostEqual(health.total, before - 17.6, places=5)
        before_snapshot = snapshot_digest(export_component_vm(component))
        state = instance.state(instance.graph.node(15).state)
        state.last_applied += 1
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before_snapshot)

    def test_one_shot_applies_once_and_scope_exit_releases_control(self):
        _, _, player, world, component, _, health, pin = self.arena(mode=0)
        before = health.total
        self.start(pin)
        self.assertEqual(health.total, before - 25)
        component.run_frame(component.frame + 20)
        self.assertEqual(health.total, before - 25)
        self.assertTrue(world.match.combat.is_stunned(player))
        pin.end(False)
        self.assertFalse(world.match.combat.is_stunned(player))
        component.run_frame(component.frame + 20)
        self.assertEqual(health.total, before - 25)

    def test_stop_cancels_scope_and_preserves_independent_flashbang_deadline(self):
        _, _, player, world, component, _, health, pin = self.arena()
        self.start(pin)
        fight = world.match.combat
        fight.stuns[player.body] = fight.tick + 50
        npc_control.stop(world.match)
        self.assertFalse(pin.active)
        self.assertFalse(fight.pins)
        self.assertTrue(fight.is_stunned(player))
        before = health.total
        component.run_frame(component.frame + 100)
        self.assertEqual(health.total, before)

    def test_teammate_quick_melee_hits_and_releases_the_pin(self):
        run, actor, player, world, _, _, health, pin = self.arena()
        ally = run.players[1]
        ally.team = player.team
        x, y, z = actor.position
        ally.mover.position[:] = (x + 1.5, y, z)
        fight = world.match.combat
        yaw, pitch = aim(fight._eye(ally), (x, y + 1, z))
        self.start(pin)
        self.assertTrue(fight.is_stunned(player))
        run.step({ally: (yaw, pitch, 0x800)})
        for _ in range(40):
            if not pin.active:
                break
            run.step({ally: (yaw, pitch, 0)})
        self.assertFalse(pin.active)
        self.assertFalse(fight.is_stunned(player))
        self.assertTrue(fight.is_stunned(actor))
        self.assertEqual(actor.health - fight.vitals[actor.entity].total, 30)
        before = health.total
        for _ in range(100):
            run.step({})
        self.assertEqual(health.total, before)

    def test_pin_interrupts_deadeye_and_releases_held_inputs(self):
        run, _, player, world, _, _, _, pin = self.arena()
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.vars[338].value())
        run.step({player: (0, 0, 32)})
        ability = next(s for i in script.component.instances.values() if i.graph.index == 0x22F
                       for s in i.states.values() if isinstance(s, nodes.Ability))
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.start(pin)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertFalse(script.component.held)
        self.assertTrue(world.match.combat.is_stunned(player))

    def test_source_stun_and_death_release_victim_without_late_damage(self):
        for death in (False, True):
            with self.subTest(death=death):
                run, actor, player, world, component, _, health, pin = self.arena()
                self.start(pin)
                target = next(t for t in run.match.combat._targets() if t.owner is actor)
                if death:
                    run.match.combat._damage(run.players[1], target, 9999, False, None, run.frame)
                    self.assertTrue(actor.dead)
                    self.assertIn(actor, run.match.bots)
                else:
                    run.match.combat._hit_effect(target, combat.FLASHBANG_EFFECT)
                self.assertFalse(run.match.combat.is_stunned(player))
                self.assertIsNone(world.manual_target)
                before = health.total
                component.run_frame(component.frame + 30)
                self.assertEqual(health.total, before)

    def test_range_occlusion_and_team_changes_refuse_damage_and_release_control(self):
        for blocked in ("range", "wall", "team"):
            with self.subTest(blocked=blocked):
                _run, actor, player, world, component, _, health, pin = self.arena()
                self.start(pin)
                if blocked == "range":
                    actor.position = (actor.position[0], actor.position[1], actor.position[2] + 10)
                elif blocked == "team":
                    actor.team = player.team
                before = health.total
                with patch("ow174.game.combat.capsule_visible", return_value=blocked != "wall"):
                    component.run_frame(component.frame + 20)
                self.assertEqual(health.total, before)
                self.assertNotIn(actor.entity, world.match.combat.pins)

    def test_controller_context_is_data_only_and_replacement_retires_old_target(self):
        run, actor, player, world, component, instance, _, pin = self.arena()
        cfg = {"$": "STU_DB742151", "m_entity": {"$": "STU_B5A0CAF0"}}
        context = world.native(cfg, instance)
        self.assertEqual(context.get(1650), expr.Entity(player.body))
        self.assertEqual(world.ai_weapon_button(0), 36)
        self.start(pin)
        old_body = player.body
        run.match.switch_hero(player, player.hero)
        self.assertNotIn(old_body, run.match.combat.pins.values())
        assassin.bind(world, component, player.body)
        self.assertFalse(pin.active)
        self.assertNotEqual(world.manual_target, old_body)
        self.assertEqual(instance.vars[536].value(), 0)
        self.assertTrue(actor in run.match.bots)

    def test_explicit_removal_releases_pin_and_world_reference(self):
        run, actor, player, world, _, _, _, pin = self.arena()
        self.start(pin)
        self.assertEqual(npc.remove_spawned(run.match), (actor.entity,))
        self.assertFalse(run.match.combat.is_stunned(player))
        self.assertIsNone(world.match)
        self.assertIsNone(world.manual_target)

    def test_death_clears_queued_and_running_pin_release(self):
        for already_releasing in (False, True):
            with self.subTest(already_releasing=already_releasing):
                run, _, player, world, component, instance, _, pin = self.arena()
                release = instance.node_state(instance.graph.node(31))
                self.start(pin)
                if already_releasing:
                    assassin.bind(world, component, None)
                    component.run_now()
                    self.assertTrue(release.active)
                assassin.bind(world, component, None, dying=True)
                component.run_now()
                self.assertFalse(pin.active or release.active)
                self.assertFalse(run.match.combat.is_stunned(player))
                self.assertEqual(release.counter, int(already_releasing))
