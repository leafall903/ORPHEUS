"""Actual authored Enforcer/Assassin inputs; manual context remains reconstruction."""
import gzip
import json
import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_mccree_flashbang as flashbang_fixtures
import test_mei_phases as phases
import test_mei_pve as pve
import test_mei_shields as shield_fixtures
import test_mei_wall as wall_fixtures

from ow174.game import assassin, combat, enforcer, mei, npc, zarya_shields
from ow174.game.content import heroes
from ow174.game.script import expr, graph


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_DATA_DIR"), "explicit NPC data required")
class MeiNpcIncomingTests(unittest.TestCase):
    setUpClass = classmethod(pve.MeiPveTests.setUpClass.__func__)
    setUp = pve.MeiPveTests.setUp
    reset_graphs = pve.MeiPveTests.reset_graphs
    scene = pve.MeiPveTests.scene

    def attack(self, name, *, engage=True):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, owner, bot, target = self.scene(definition, 2.)
        if engage:
            npc.engage(run.match, bot, owner)
        return run, owner, bot, target

    def advance(self, run, frames):
        for _ in range(frames):
            run.step({})

    def health(self, run, owner):
        target = next(t for t in run.match.combat._targets() if t.owner is owner)
        return run.match.combat.vitals_of(target).total

    def autonomous_assassin(self):
        path = os.environ.get("ORPHEUS_ASSASSIN_GRAPH_BUNDLE")
        if not path:
            self.skipTest("explicit 104319 Assassin controller graph bundle required")
        bundle = json.loads(gzip.decompress(Path(path).read_bytes()))
        graph_patch = patch.object(graph, "_data", return_value=bundle)
        graph_patch.start()
        self.addCleanup(graph_patch.stop)
        graph.graph.cache_clear()
        graph._records.cache_clear()
        run, owner, bot, _ = self.attack("Assassin", engage=False)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        for player in run.match.players:
            player.team = 0 if player is owner else bot.team
        controller = bot.body_script.controller
        self.assertIsNotNone(controller)
        attack = next(i for i in controller.instances.values() if i.graph.index == assassin.CONTROLLER_GRAPH)
        return run, owner, bot, attack

    def test_autonomous_assassin_repins_same_target_after_wall_and_recovery(self):
        run, owner, bot, attack = self.autonomous_assassin()
        fight = run.match.combat
        body = bot.body_script.component
        self.advance(run, 3)
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(self.health(run, owner), 225.)
        ally = run.players[1]
        self.assertTrue(run.match.switch_hero(ally, heroes()[mei.HERO]))
        x, y, z = owner.mover.position
        fight.walls.place_player(ally, (x + 4., y, z + 1.))
        walls = wall_fixtures.WallTests()
        angles = pve.fixtures.aim(fight._eye(ally), (x, y, z + 1.))
        for button in (16, 2, 1):
            walls.tap(run, ally, button, angles)
        self.assertEqual(len(fight.walls.pieces), 5)
        self.advance(run, 2)
        self.assertFalse(fight.pins)
        self.assertTrue(assassin.recovering(body))
        walls.tap(run, ally, 16, angles)
        self.assertFalse(fight.walls.pieces)
        self.assertTrue(assassin.recovering(body))
        for _ in range(150):
            if not assassin.recovering(body):
                break
            self.assertFalse(fight.pins)
            self.assertEqual(self.health(run, owner), 225.)
            run.step({})
        self.assertFalse(assassin.recovering(body))
        for _ in range(20):
            if fight.pins:
                break
            run.step({})
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(attack.context().var(expr.INSTANCE, 536), expr.Entity(owner.body))
        self.assertEqual(self.health(run, owner), 200.)
        self.assertIsNone(body.world.manual_target)

    def test_autonomous_assassin_waits_for_cryo_then_applies_fresh_initial_hit(self):
        run, owner, bot, attack = self.autonomous_assassin()
        fight = run.match.combat
        x, y, z = bot.position
        bot.position = (x, y, z + 4.)
        run.step({owner: (0, 0, 8)})
        for _ in range(100):
            run.step({})
            self.assertTrue(mei.protected(owner))
            self.assertFalse(fight.pins)
            self.assertEqual(self.health(run, owner), 250.)
        self.assertLessEqual(math.dist(bot.position, owner.mover.position), 2.5)
        self.assertEqual(attack.context().var(expr.INSTANCE, 536), expr.Entity(owner.body))
        self.assertNotIn(36, bot.body_script.component.held)
        run.step({owner: (0, 0, 2)})
        self.assertFalse(mei.protected(owner))
        for _ in range(20):
            if fight.pins:
                break
            run.step({})
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(self.health(run, owner), 225.)
        self.assertIsNone(bot.body_script.component.world.manual_target)

    def test_autonomous_assassin_waits_for_projected_barrier_then_pins(self):
        run, owner, bot, _ = self.autonomous_assassin()
        fight = run.match.combat
        ally = run.players[1]
        self.assertTrue(run.match.switch_hero(ally, heroes()[zarya_shields.HERO]))
        ally.team = owner.team
        bot.body_script.controller.set(14365, expr.Entity(owner.body))
        x, y, z = owner.mover.position
        fight.walls.place_player(ally, (x + 4., y, z))
        bot.position = (x, y, z + 45.)
        self.advance(run, 30)
        bubble = shield_fixtures.ShieldTests().cast(run, ally, owner)
        bot.position = (x, y, z + 6.)
        for _ in range(130):
            if fight.shields.shielded(owner) is None:
                break
            run.step({})
            if fight.shields.shielded(owner) is not None:
                self.assertFalse(fight.pins)
                self.assertEqual(self.health(run, owner), 250.)
        self.assertIsNone(fight.shields.shielded(owner))
        self.assertEqual(bubble.health, 200.)
        for _ in range(30):
            if fight.pins:
                break
            run.step({})
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(self.health(run, owner), 225.)

    def first_volley(self, run, bot, *, frames=100):
        for _ in range(frames):
            run.step({})
            pellets = [r for r in run.match.combat.rockets if r.shooter is bot]
            if pellets:
                return pellets
        self.fail('actual Enforcer input did not reach a projectile volley')

    def test_enforcer_authored_25_pellets_consume_one_ammo_and_deal_50_close_damage(self):
        run, owner, bot, _target = self.attack('Enforcer')
        pellets = self.first_volley(run, bot)
        self.assertEqual(len(pellets), 25)
        self.assertEqual(len({r.direction for r in pellets}), 25)
        state = next(s for i in bot.body_script.component.instances.values() for s in i.states.values()
                     if s.node.cls == 'STUStatescriptStateWeaponVolley' and s.counter)
        self.assertEqual(state.instance.vars[53].value(), 3)
        self.assertEqual(state.instance.vars[230].value(), 4)
        forward = combat.aim(round(bot.yaw), 0)
        tangents = []
        for pellet in pellets:
            self.assertAlmostEqual(math.sqrt(sum(v * v for v in pellet.direction)), 1.)
            dot = sum(a * b for a, b in zip(forward, pellet.direction, strict=True))
            tangents.append(math.sqrt(max(0., 1. - dot * dot)) / dot)
            self.assertEqual(pellet.direct.amount, 2.)
            self.assertIsNone(pellet.shot)  # authored25-pellet SHOTS_ONLY policy
        if enforcer.dispersion_fingerprint() is None:
            unit = math.tan(math.radians(4))
            self.assertEqual(sum(t < 1e-7 for t in tangents), 1)
            self.assertEqual(sum(abs(t - .5 * unit) < 1e-7 for t in tangents), 8)
            self.assertEqual(sum(abs(t - unit) < 1e-7 for t in tangents), 16)
        else:
            self.assertGreater(tangents[0], 0.)
            self.assertLessEqual(max(tangents), math.tan(math.radians(6.)))
        npc.engage(run.match, bot, None)
        self.advance(run, 10)
        self.assertEqual(self.health(run, owner), 200.)
        self.advance(run, 100)
        self.assertEqual(self.health(run, owner), 200.)

    def test_enforcer_refire_jitter_and_early_repress_follow_authored_gate(self):
        traces = {}
        for seed, repressed in ((11, False), (11, True), (29, False)):
            with self.subTest(seed=seed, repressed=repressed):
                run, owner, bot, _target = self.attack("Enforcer", engage=False)
                component = bot.body_script.component
                weapon = next(i for i in component.instances.values() if i.graph.index == 0x13EB)
                volley, gate = weapon.state(14), weapon.vars[173]
                component.rng.seed(seed)
                uniform = component.rng.uniform
                samples, shots = [], []

                def sample(low, high, draw=uniform, values=samples):
                    self.assertAlmostEqual(low, -.2)
                    self.assertAlmostEqual(high, .6)
                    value = draw(low, high)
                    values.append(value)
                    return value

                with patch.object(component.rng, "uniform", side_effect=sample):
                    npc.engage(run.match, bot, owner)
                    first = None
                    for tick in range(750):
                        prior = gate.value() or 0
                        run.step({})
                        if volley.counter > len(shots):
                            expected = weapon.vars[8085].value() + samples[-1]
                            self.assertAlmostEqual(gate.value(), expected, places=6)
                            shots.append((volley.start, weapon.vars[53].value()))
                            if first is None:
                                first = tick
                                bot.yaw = 0
                            else:
                                self.assertLessEqual(prior, 0)
                        if repressed and first is not None:
                            if tick == first + 12:
                                self.assertGreater(gate.value(), 0)
                                primary = component.world.ai_weapon_button(0)
                                component.edges([(primary, False)])
                            if tick == first + 16:
                                self.assertGreater(gate.value(), 0)
                                self.assertEqual(volley.counter, 1)
                                npc.engage(run.match, bot, owner)
                                bot.yaw = 0
                        if volley.counter == 4:
                            break
                self.assertEqual([ammo for _, ammo in shots], [3, 2, 1, 0])
                self.assertEqual(len(samples), 4)
                self.assertEqual(self.health(run, owner), 200.)
                traces[seed, repressed] = shots
        self.assertEqual(traces[11, False], traces[11, True])
        self.assertNotEqual(traces[11, False], traces[29, False])

    def test_actual_enforcer_mode_difficulty_scales_weapon_via_spawn_graph(self):
        from test_game_combat import KANEZAKA_DM

        mode = f"0x{KANEZAKA_DM.mode_guid:016X}"
        mode_data = self.graph_data.get("mode_variables", {})
        self.addCleanup(graph.mode_variables.cache_clear)
        for level, damage in enumerate((2., 4., 6., 8.)):
            with self.subTest(level=level):
                modes = {**mode_data, mode: {**mode_data.get(mode, {}), "9948": level}}
                with patch.dict(self.graph_data, {"mode_variables": modes}):
                    graph.mode_variables.cache_clear()
                    run, owner, bot, _target = self.attack("Enforcer", engage=False)
                component = bot.body_script.component
                self.assertIn(0x143A, {i.graph.index for i in component.instances.values()})
                self.assertEqual(component.entity_var(9948).value(), level)
                npc.engage(run.match, bot, owner)
                pellets = self.first_volley(run, bot)
                self.assertEqual(len(pellets), 25)
                self.assertEqual({pellet.direct.amount for pellet in pellets}, {damage})
                weapon = next(i for i in component.instances.values() if i.graph.index == 0x13EB)
                self.assertEqual(weapon.vars[53].value(), 3)
                npc.engage(run.match, bot, None)
                self.advance(run, 10)
                self.assertEqual(self.health(run, owner), 250. - 25 * damage)

    def test_enforcer_missed_cone_and_allied_target_take_no_pellet_damage(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, owner, bot, _target = self.attack('Enforcer')
                if allied:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    bot.team = owner.team
                else:
                    bot.yaw += 16384
                self.assertEqual(len(self.first_volley(run, bot)), 25)
                npc.engage(run.match, bot, None)
                self.advance(run, 90)
                self.assertEqual(self.health(run, owner), 250.)

    def test_enforcer_25_pellets_stop_at_actual_ice_wall(self):
        run, owner, bot, _target = self.attack('Enforcer', engage=False)
        walls = wall_fixtures.WallTests()
        pieces, aim = walls.place(run, owner)
        centre = pieces[2]
        bot.position = (centre.feet[0], bot.position[1], centre.feet[2] + 2.)
        npc.engage(run.match, bot, owner)
        self.assertEqual(len(self.first_volley(run, bot)), 25)
        self.advance(run, 10)
        self.assertEqual(centre.health, 350.)
        self.assertEqual(self.health(run, owner), 250.)
        walls.tap(run, owner, 16, aim)
        self.assertFalse(run.match.combat.walls.pieces)
        self.assertEqual(len(self.first_volley(run, bot, frames=180)), 25)
        self.advance(run, 10)
        self.assertLess(self.health(run, owner), 250.)

    def test_enforcer_actual_projected_shield_absorbs_all_25_pellets(self):
        run, owner, bot, _target = self.attack('Enforcer', engage=False)
        caster = pve.MeiPveTests().shield_caster(run, owner, same_team=True)
        bubble = pve.shield_fixtures.ShieldTests().cast(run, caster, owner)
        npc.engage(run.match, bot, owner)
        self.assertEqual(len(self.first_volley(run, bot)), 25)
        npc.engage(run.match, bot, None)
        self.advance(run, 10)
        self.assertEqual(bubble.health, 150.)
        self.assertEqual(self.health(run, owner), 250.)

    def test_enforcer_zero_spread_fixture_keeps_25_separate_falloff_hits(self):
        run, owner, bot, _target = self.attack('Enforcer', engage=False)
        bot.position = (bot.position[0], bot.position[1], owner.mover.position[2] + 16.)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        # Zero both spread inputs to isolate the authored 8..15m falloff.
        weapon.write((0, 230), 0.)
        pattern = weapon.graph.node(enforcer.NODE).field("m_5EC1EB38")
        with patch.dict(pattern, {"m_92308789": {"$": "STUConfigVarInt", "m_value": 0}}):
            npc.engage(run.match, bot, owner)
            pellets = self.first_volley(run, bot)
        self.assertEqual(len(pellets), 25)
        self.assertEqual(len({r.direction for r in pellets}), 1)
        npc.engage(run.match, bot, None)
        self.advance(run, 20)
        self.assertAlmostEqual(self.health(run, owner), 240., places=5)

    def test_enforcer_static_wall_blocks_all_pellets(self):
        run, owner, bot, _target = self.attack('Enforcer', engage=False)
        pve.fixtures.static_wall(run, owner)
        npc.engage(run.match, bot, owner)
        self.assertEqual(len(self.first_volley(run, bot)), 25)
        npc.engage(run.match, bot, None)
        self.advance(run, 90)
        self.assertEqual(self.health(run, owner), 250.)

    def test_enforcer_native_release_does_not_fire_later_volleys(self):
        run, owner, bot, _target = self.attack('Enforcer')
        self.first_volley(run, bot)
        component = bot.body_script.component
        component.edges([(component.world.ai_weapon_button(0), False)])
        # Leave the VM advancing: this checks authored release, not engage(None)'s
        # explicit simulation switch. In-flight pellets may still arrive.
        self.advance(run, 180)
        health = self.health(run, owner)
        ammo = next(i for i in component.instances.values() if i.graph.index == 0x13EB).vars[53].value()
        self.assertEqual(health, 200.)
        self.advance(run, 180)
        self.assertEqual(self.health(run, owner), health)
        self.assertEqual(next(i for i in component.instances.values()
                              if i.graph.index == 0x13EB).vars[53].value(), ammo)

    def test_enforcer_primary_reload_deadline_and_reentry(self):
        for input_mode in ('held', 'released', 'repressed_during_reload', 'flashbang'):
            with self.subTest(input_mode=input_mode):
                self.check_enforcer_reload(input_mode)

    def check_enforcer_reload(self, input_mode):
        run, owner, bot, target = self.attack('Enforcer', engage=False)
        fight = run.match.combat
        if input_mode == 'flashbang':
            ally = run.players[1]
            self.assertTrue(run.match.switch_hero(ally, heroes()[0x02E0000000000042]))
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            ally.team = owner.team
            x, y, z = owner.mover.position
            fight.walls.place_player(ally, (x + 1.5, y, z))
            self.advance(run, 30)
        weapon, facing = self.empty_enforcer_magazine(run, owner, bot)
        component = weapon.component
        primary = component.world.ai_weapon_button(0)
        ammo, capacity = weapon.vars[53], weapon.vars[198]
        volley, reload_wait = weapon.state(14), weapon.state(19)
        duration = reload_wait.evaluate(reload_wait.node.field('m_timeout'), watch=False)
        self.assertEqual(reload_wait.ms, round(duration * 1000))
        deadline = reload_wait.start + reload_wait.ms
        if input_mode in ('released', 'repressed_during_reload'):
            component.edges([(primary, False)])
        if input_mode == 'repressed_during_reload':
            self.advance(run, 10)
            self.assertEqual(ammo.value(), 0)
            self.assertEqual(volley.counter, 4)
            component.edges([(primary, True)])
        if input_mode == 'flashbang':
            health = fight.vitals_of(target).total
            flashbang_fixtures.FlashbangTests().cast(run, ally, bot)
            self.assertEqual(fight.vitals_of(target).total, health - 25.)
            self.assertNotIn(primary, component.held)
            self.assertEqual(ammo.value(), 0)
            self.assertEqual(volley.counter, 4)
        self.assertTrue(reload_wait.active)
        self.assertEqual(reload_wait.start + reload_wait.ms, deadline)
        while component.now + component.frame_ms < deadline:
            run.step({})
            self.assertEqual(ammo.value(), 0)
            self.assertEqual(volley.counter, 4)
        run.step({})
        self.assertGreaterEqual(component.now, deadline)
        self.assertEqual(ammo.value(), capacity.value())
        self.assertEqual(volley.counter, 4)
        self.assertFalse(reload_wait.active)

        bot.yaw = facing
        if input_mode in ('released', 'flashbang'):
            self.advance(run, 200)
            self.assertFalse(fight.is_stunned(bot))
            self.assertEqual(ammo.value(), 4)
            self.assertEqual(volley.counter, 4)
            self.assertEqual(self.health(run, owner), 200.)
            component.edges([(primary, True)])
        for _ in range(250):
            run.step({})
            if volley.counter > 4:
                break
        self.assertEqual(ammo.value(), 3)
        self.assertEqual(volley.counter, 5)
        self.advance(run, 10)
        self.assertEqual(self.health(run, owner), 150.)

    def empty_enforcer_magazine(self, run, owner, bot):
        npc.engage(run.match, bot, owner)
        component = bot.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x13EB)
        ammo = weapon.vars[53]
        self.assertEqual(ammo.value(), 4)
        self.assertEqual(weapon.vars[198].value(), 4)
        self.assertEqual(len(self.first_volley(run, bot)), 25)
        self.advance(run, 10)
        self.assertEqual(self.health(run, owner), 200.)
        facing = bot.yaw
        bot.yaw = 0  # Keep Mei alive while the remaining held shots spend ammo.
        magazine = [ammo.value()]
        reload_wait = weapon.state(19)
        for _ in range(750):
            run.step({})
            if ammo.value() != magazine[-1]:
                magazine.append(ammo.value())
            if reload_wait.active:
                break
        self.assertEqual(magazine, [3, 2, 1, 0])
        self.assertEqual(weapon.state(14).counter, 4)
        self.assertTrue(reload_wait.active)
        return weapon, facing

    def test_enforcer_death_and_removal_retire_pending_reload(self):
        for death in (True, False):
            with self.subTest(death=death):
                run, owner, bot, target = self.attack('Enforcer', engage=False)
                fight = run.match.combat
                weapon, facing = self.empty_enforcer_magazine(run, owner, bot)
                reload_wait = weapon.state(19)
                deadline = reload_wait.start + reload_wait.ms
                if death:
                    fight.vitals_of(target).now.update({
                        combat.HEALTH_POOL: 60.,
                        combat.ARMOUR_POOL: 0.,
                        combat.SHIELDS_POOL: 0.,
                    })
                    pve.fixtures.MeiCombatTests().fire(run, owner, target, 2, .9, frames=35)
                    self.assertTrue(bot.dead)
                    self.assertIn(bot, run.match.bots)
                    self.assertTrue(owner.client.death_events)
                else:
                    self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                    self.assertNotIn(bot, run.match.bots)
                    self.assertFalse(weapon.component.instances)
                self.assertLess(run.frame * 16, deadline)
                self.assertFalse(reload_wait.active)
                bot.yaw = facing
                self.advance(run, 120)
                self.assertGreater(run.frame * 16, deadline)
                self.assertEqual(weapon.vars[53].value(), 0)
                self.assertEqual(weapon.state(14).counter, 4)
                self.assertEqual(self.health(run, owner), 200.)

    def test_actual_spray_freeze_blocks_enforcer_damage_during_lock(self):
        run, owner, bot, target = self.attack('Enforcer')
        pve.frost_fixtures.FrostTests().freeze(run, owner, target)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        health, ammo = self.health(run, owner), weapon.vars[53].value()
        self.advance(run, 60)
        self.assertTrue(run.match.combat.frozen(bot))
        self.assertEqual(self.health(run, owner), health)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_both_authored_attacks_damage_mei_and_sync_source_health(self):
        for name in ("Enforcer", "Assassin"):
            with self.subTest(name=name):
                run, owner, bot, target = self.attack(name)
                self.advance(run, 120)
                self.assertLess(self.health(run, owner), 250.)
                self.assertEqual(bot.body_script.component.entity_var(253).value(),
                                 run.match.combat.vitals_of(target).total)
                self.assertTrue(any(s.counter for i in bot.body_script.component.instances.values()
                                    for s in i.states.values()))

    def test_assassin_stop_releases_control_and_damage(self):
        run, owner, bot, _target = self.attack("Assassin")
        self.advance(run, 70)
        self.assertEqual(run.match.combat.pins, {bot.entity: owner.body})
        self.assertTrue(run.match.combat.is_stunned(owner))
        npc.engage(run.match, bot, None)
        before = self.health(run, owner)
        self.advance(run, 100)
        self.assertEqual(self.health(run, owner), before)
        self.assertFalse(run.match.combat.is_stunned(owner))
        self.assertFalse(run.match.combat.pins)

    def test_assassin_manual_stop_clocks_authored_release_wait_while_idle(self):
        run, owner, bot, _target = self.attack("Assassin")
        self.advance(run, 70)
        fight = run.match.combat
        self.assertEqual(fight.pins, {bot.entity: owner.body})
        component = bot.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x1157)

        npc.engage(run.match, bot, None)
        before = self.health(run, owner)
        release = instance.states[instance.graph.node(31).state]
        category = instance.states[instance.graph.node(36).state]
        wait = instance.states[instance.graph.node(37).state]
        self.assertTrue(release.active)
        self.assertTrue(category.active)
        self.assertTrue(wait.active)
        self.assertFalse(fight.pins)

        self.advance(run, 90)
        self.assertTrue(release.active)
        self.advance(run, 3)
        self.assertFalse(release.active)
        self.assertTrue(wait.active)
        self.advance(run, 27)
        self.assertTrue(wait.active)
        self.advance(run, 6)
        self.assertFalse(wait.active)
        self.assertFalse(category.active)
        self.assertEqual(self.health(run, owner), before)
        self.assertFalse(fight.pins)

        npc.engage(run.match, bot, owner)
        self.advance(run, 2)
        self.assertFalse(release.active)
        self.assertEqual(fight.pins, {bot.entity: owner.body})
        self.assertEqual(self.health(run, owner), before - 25.)

    def test_assassin_reengagement_obeys_release_animation_gate(self):
        run, owner, bot, _target = self.attack("Assassin")
        self.advance(run, 70)
        fight = run.match.combat
        self.assertEqual(fight.pins, {bot.entity: owner.body})
        component = bot.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x1157)

        npc.engage(run.match, bot, None)
        release = instance.states[instance.graph.node(31).state]
        wait = instance.states[instance.graph.node(37).state]
        before = self.health(run, owner)
        for frames in (1, 89):
            self.assertTrue(release.active)
            npc.engage(run.match, bot, owner)
            self.advance(run, frames)
            self.assertTrue(release.active)
            self.assertFalse(fight.pins)
            self.assertEqual(self.health(run, owner), before)

        self.advance(run, 4)
        self.assertFalse(release.active)
        self.assertTrue(wait.active)
        self.assertFalse(fight.pins)
        self.assertIsNone(component.world.manual_target)
        self.assertEqual(self.health(run, owner), before)

        npc.engage(run.match, bot, owner)
        self.advance(run, 1)
        self.assertTrue(wait.active)
        self.assertEqual(fight.pins, {bot.entity: owner.body})
        self.assertEqual(self.health(run, owner), before - 25.)

    def test_assassin_replacing_pinned_target_requires_fresh_input_after_release(self):
        run, owner, bot, _target = self.attack("Assassin")
        self.advance(run, 70)
        fight = run.match.combat
        self.assertEqual(fight.pins, {bot.entity: owner.body})
        other = run.players[1]
        x, y, z = owner.mover.position
        fight.walls.place_player(other, (x + 0.5, y, z))
        old_health, new_health = self.health(run, owner), self.health(run, other)

        npc.engage(run.match, bot, other)
        self.assertFalse(fight.pins)
        npc.engage(run.match, bot, other)
        self.advance(run, 94)
        self.assertFalse(fight.pins)
        self.assertEqual(self.health(run, owner), old_health)
        self.assertEqual(self.health(run, other), new_health)

        npc.engage(run.match, bot, other)
        self.advance(run, 1)
        self.assertEqual(fight.pins, {bot.entity: other.body})
        self.assertEqual(self.health(run, other), new_health - 25.)

    def test_projected_shield_blocks_or_releases_assassin_pin(self):
        for already_pinned in (False, True):
            with self.subTest(already_pinned=already_pinned):
                run, owner, bot, _target = self.attack('Assassin', engage=False)
                fight = run.match.combat
                caster = pve.MeiPveTests().shield_caster(run, owner, same_team=True)
                if already_pinned:
                    npc.engage(run.match, bot, owner)
                    self.advance(run, 20)
                    self.assertEqual(fight.pins, {bot.entity: owner.body})

                aim = pve.fixtures.aim(fight._eye(caster), fight._eye(owner))
                run.step({caster: (*aim, 16)})
                run.step({caster: (*aim, 0)})
                bubble = fight.shields.shielded(owner)
                self.assertIsNotNone(bubble)
                before = self.health(run, owner)
                if not already_pinned:
                    npc.engage(run.match, bot, owner)
                self.advance(run, 20)
                self.assertFalse(fight.pins)
                self.assertFalse(fight.is_stunned(owner))
                self.assertIsNone(bot.body_script.component.world.manual_target)
                self.assertEqual(self.health(run, owner), before)
                self.assertEqual(bubble.health, 200.)

                self.advance(run, 130)
                self.assertIsNone(fight.shields.shielded(owner))
                self.assertFalse(fight.pins)
                self.assertEqual(self.health(run, owner), before)
                npc.engage(run.match, bot, owner)
                self.advance(run, 20)
                self.assertEqual(fight.pins, {bot.entity: owner.body})
                self.assertLess(self.health(run, owner), before)

    def test_assassin_phase_target_requires_fresh_engagement_after_protection(self):
        for hero in (phases.REAPER, phases.MOIRA):
            with self.subTest(hero=hero):
                run, owner, bot, _target = self.attack('Assassin', engage=False)
                fight = run.match.combat
                position = tuple(owner.mover.position)
                self.assertTrue(run.match.switch_hero(owner, heroes()[hero]))
                fight.walls.place_player(owner, position)
                self.advance(run, 30)
                before = self.health(run, owner)
                phase = phases.MeiPhaseTests()
                phase.cast(run, owner)
                npc.engage(run.match, bot, owner)
                self.advance(run, 20)
                self.assertEqual(self.health(run, owner), before)
                self.assertFalse(fight.pins)
                self.assertIsNone(bot.body_script.component.world.manual_target)

                phase.finish(run, owner)
                self.advance(run, 30)
                self.assertEqual(self.health(run, owner), before)
                self.assertFalse(fight.pins)
                npc.engage(run.match, bot, owner)
                self.advance(run, 20)
                self.assertEqual(fight.pins, {bot.entity: owner.body})
                self.assertLess(self.health(run, owner), before)

    def test_assassin_wall_occlusion_retires_contact_until_fresh_engagement(self):
        run, owner, bot, _target = self.attack("Assassin", engage=False)
        fight = run.match.combat
        walls = wall_fixtures.WallTests()
        pieces, aim = walls.place(run, owner, distance=1.)
        self.assertEqual(len(pieces), 5)
        npc.engage(run.match, bot, owner)
        self.advance(run, 30)
        self.assertFalse(fight.pins)
        self.assertIsNone(bot.body_script.component.world.manual_target)
        self.assertEqual(self.health(run, owner), 250.)
        self.assertTrue(all(piece.health == 400. for piece in pieces))

        walls.tap(run, owner, 16, aim)
        self.assertFalse(fight.walls.pieces)
        self.advance(run, 30)
        self.assertFalse(fight.pins)
        self.assertEqual(self.health(run, owner), 250.)
        npc.engage(run.match, bot, owner)
        self.advance(run, 20)
        self.assertEqual(fight.pins, {bot.entity: owner.body})
        self.assertLess(self.health(run, owner), 250.)

    def test_teammate_ice_wall_breaks_an_active_assassin_pin(self):
        run, owner, bot, _target = self.attack("Assassin", engage=False)
        fight = run.match.combat
        ally = run.players[1]
        self.assertTrue(run.match.switch_hero(ally, heroes()[mei.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = owner.team
        x, y, z = owner.mover.position
        fight.walls.place_player(ally, (x + 4., y, z + 1.))
        self.advance(run, 10)
        npc.engage(run.match, bot, owner)
        self.advance(run, 2)
        self.assertEqual(fight.pins, {bot.entity: owner.body})
        self.assertTrue(fight.is_stunned(owner))

        walls = wall_fixtures.WallTests()
        aim = pve.fixtures.aim(fight._eye(ally), (x, y, z + 1.))
        walls.tap(run, ally, 16, aim)
        walls.tap(run, ally, 2, aim)
        walls.tap(run, ally, 1, aim)
        self.assertTrue(fight.walls.pieces)
        self.assertFalse(fight.pins)
        self.assertFalse(fight.is_stunned(owner))
        self.assertIsNone(bot.body_script.component.world.manual_target)
        health = self.health(run, owner)

        walls.tap(run, ally, 16, aim)
        self.assertFalse(fight.walls.pieces)
        self.advance(run, 160)
        self.assertFalse(fight.pins)
        self.assertEqual(self.health(run, owner), health)
        npc.engage(run.match, bot, owner)
        self.advance(run, 2)
        self.assertEqual(fight.pins, {bot.entity: owner.body})
        self.assertEqual(self.health(run, owner), health - 25.)

    def test_assassin_lost_contact_cannot_resume_without_new_engagement(self):
        for loss in ("range", "team", "disconnect"):
            with self.subTest(loss=loss):
                run, owner, bot, _target = self.attack("Assassin")
                self.advance(run, 70)
                old_position = bot.position
                if loss == "range":
                    bot.position = (old_position[0] + 10., old_position[1], old_position[2])
                elif loss == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    bot.team = owner.team
                else:
                    owner.gone = True
                before = self.health(run, owner)
                self.advance(run, 2)
                self.assertFalse(run.match.combat.pins)
                self.assertIsNone(bot.body_script.component.world.manual_target)
                bot.position, bot.team, owner.gone = old_position, 1, False
                self.advance(run, 60)
                self.assertEqual(self.health(run, owner), before)

    def test_cryo_before_attack_blocks_both_actual_attack_paths(self):
        for name in ("Enforcer", "Assassin"):
            with self.subTest(name=name):
                run, owner, bot, _target = self.attack(name, engage=False)
                fight = run.match.combat
                run.step({owner: (0, 0, 8)})
                self.assertTrue(mei.protected(owner))
                npc.engage(run.match, bot, owner)
                if name == "Enforcer":
                    self.assertEqual(len(self.first_volley(run, bot)), 25)
                    npc.engage(run.match, bot, None)
                    self.advance(run, 10)
                    self.assertFalse(any(shot.shooter is bot for shot in fight.rockets))
                else:
                    self.advance(run, 100)
                    npc.engage(run.match, bot, None)
                self.assertTrue(mei.protected(owner))
                self.assertEqual(self.health(run, owner), 250.)
                self.assertFalse(fight.pins)
                self.assertFalse(fight.is_stunned(owner))

                run.step({owner: (0, 0, 2)})
                self.assertFalse(mei.protected(owner))
                self.advance(run, 20)
                self.assertEqual(self.health(run, owner), 250.)
                npc.engage(run.match, bot, owner)
                if name == "Enforcer":
                    self.assertEqual(len(self.first_volley(run, bot)), 25)
                    npc.engage(run.match, bot, None)
                    self.advance(run, 10)
                    self.assertEqual(self.health(run, owner), 200.)
                else:
                    self.advance(run, 2)
                    self.assertEqual(fight.pins, {bot.entity: owner.body})
                    self.assertEqual(self.health(run, owner), 225.)

    def test_assassin_source_stun_releases_pin_and_requires_fresh_engagement(self):
        run, owner, bot, target = self.attack("Assassin", engage=False)
        fight = run.match.combat
        ally = run.players[1]
        self.assertTrue(run.match.switch_hero(ally, heroes()[0x02E0000000000042]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = owner.team
        x, y, z = owner.mover.position
        fight.walls.place_player(ally, (x + 1.5, y, z))
        self.advance(run, 30)
        npc.engage(run.match, bot, owner)
        self.advance(run, 70)
        self.assertEqual(fight.pins, {bot.entity: owner.body})
        source_health = fight.vitals_of(target).total
        flashbang_fixtures.FlashbangTests().cast(run, ally, bot)
        self.assertEqual(fight.vitals_of(target).total, source_health - 25.)
        self.assertFalse(fight.pins)
        with self.assertRaisesRegex(ValueError, 'uncontrolled source'):
            npc.engage(run.match, bot, owner)
        before = self.health(run, owner)
        self.advance(run, 100)
        self.assertFalse(fight.is_stunned(bot))
        self.assertEqual(self.health(run, owner), before)
        self.assertFalse(fight.pins)
        self.assertIsNone(bot.body_script.component.world.manual_target)

        npc.engage(run.match, bot, owner)
        self.advance(run, 20)
        self.assertEqual(fight.pins, {bot.entity: owner.body})
        self.assertLess(self.health(run, owner), before)

    def test_teammate_actual_spray_freeze_breaks_assassin_pin_and_victim_can_cryo(self):
        run, owner, bot, target = self.attack('Assassin', engage=False)
        ally = run.players[1]
        self.assertTrue(run.match.switch_hero(ally, heroes()[mei.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = owner.team
        x, y, z = owner.mover.position
        run.match.combat.walls.place_player(ally, (x + 1.5, y, z))
        self.advance(run, 30)
        npc.engage(run.match, bot, owner)
        self.advance(run, 10)
        self.assertTrue(run.match.combat.is_stunned(owner))
        pve.frost_fixtures.FrostTests().freeze(run, ally, target)
        self.assertFalse(run.match.combat.pins)
        self.assertFalse(run.match.combat.is_stunned(owner))
        self.assertIsNone(bot.body_script.component.world.manual_target)
        self.assertGreater(self.health(run, owner), 0.)
        run.step({owner: (0, 0, 8)})
        self.assertTrue(mei.protected(owner))

    def test_teammate_blizzard_freeze_releases_an_active_assassin_pin(self):
        run, owner, bot, target = self.attack("Assassin", engage=False)
        fight = run.match.combat
        ally = run.players[1]
        self.assertTrue(run.match.switch_hero(ally, heroes()[mei.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = owner.team
        x, y, z = owner.mover.position
        fight.walls.place_player(ally, (x + 2., y, z))
        self.advance(run, 10)

        blizzard = pve.blizzard_fixtures.BlizzardTests()
        blizzard.launch(run, ally)
        area = blizzard.landed(run)
        npc.engage(run.match, bot, owner)
        self.advance(run, 2)
        self.assertEqual(fight.pins, {bot.entity: owner.body})
        self.assertTrue(fight.is_stunned(owner))
        self.assertFalse(fight.frozen(bot))
        before = fight.vitals_of(target).total
        for _ in range(200):
            run.step({})
            if fight.frozen(bot):
                break
            self.assertEqual(fight.pins, {bot.entity: owner.body})
        self.assertTrue(fight.frozen(bot))
        self.assertLess(fight.vitals_of(target).total, before)
        self.assertFalse(bot.dead)
        self.assertFalse(fight.pins)
        self.assertFalse(fight.is_stunned(owner))
        self.assertIsNone(bot.body_script.component.world.manual_target)
        health = self.health(run, owner)
        blizzard.finish(run, area)
        self.advance(run, 150)
        self.assertFalse(fight.frozen(bot))
        self.assertFalse(fight.pins)
        self.assertEqual(self.health(run, owner), health)

    def test_assassin_removal_and_death_clear_pin(self):
        for attack, health in ((None, None), (2, 60.), (0x800, 25.)):
            with self.subTest(attack=attack):
                run, owner, bot, target = self.attack("Assassin", engage=False)
                fight = run.match.combat
                ally = run.players[1]
                self.assertTrue(run.match.switch_hero(ally, heroes()[mei.HERO]))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                ally.team = owner.team
                x, y, z = owner.mover.position
                fight.walls.place_player(ally, (x + 1.5, y, z + .5))
                self.advance(run, 10)
                npc.engage(run.match, bot, owner)
                self.advance(run, 2)
                self.assertEqual(fight.pins, {bot.entity: owner.body})
                self.assertTrue(fight.is_stunned(owner))
                if attack is not None:
                    vitals = fight.vitals_of(target)
                    vitals.now.update({combat.HEALTH_POOL: health, combat.ARMOUR_POOL: 0.,
                                       combat.SHIELDS_POOL: 0.})
                    pve.fixtures.MeiCombatTests().fire(run, ally, target, attack, 0.9)
                    self.assertTrue(vitals.dead)
                    self.assertTrue(bot.dead)
                    self.assertIn(bot, run.match.bots)
                    self.assertEqual({entity for entity, _ in ally.client.death_events}, {bot.entity})
                    self.assertEqual(len(ally.client.messages(combat.KILL_NOTICE)), 1)
                else:
                    self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                self.assertFalse(fight.pins)
                self.assertFalse(fight.is_stunned(owner))
                victim_health = self.health(run, owner)
                self.advance(run, 100)
                self.assertEqual(self.health(run, owner), victim_health)
                self.assertFalse(fight.pins)

    def test_manual_api_rejects_friendly_and_dead_sources(self):
        run, owner, bot, target = self.attack("Assassin")
        npc.engage(run.match, bot, None)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        bot.team = owner.team
        with self.assertRaises(ValueError):
            npc.engage(run.match, bot, owner)
        bot.team = 1
        run.match.combat._damage(owner, target, 10000., False, None, run.frame)
        with self.assertRaises(ValueError):
            npc.engage(run.match, bot, owner)

    def test_assassin_health_clock_and_bound_target_are_stored_without_evaluation(self):
        from unittest.mock import patch

        from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
        from ow174.game.script import runtime

        run, _owner, bot, _target = self.attack("Assassin")
        self.advance(run, 70)
        component = bot.body_script.component
        state = next(s for i in component.instances.values() for s in i.states.values()
                     if s.node.cls == "STUStatescriptStateModifyHealth" and s.active)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(export_component_vm(component))
            state.last_applied -= 1
            changed = snapshot_digest(export_component_vm(component))
            self.assertNotEqual(before, changed)
            state.last_applied += 1
            self.assertEqual(before, snapshot_digest(export_component_vm(component)))
            old = component.world.manual_target
            component.world.manual_target = None
            self.assertNotEqual(before, snapshot_digest(export_component_vm(component)))
            component.world.manual_target = old
