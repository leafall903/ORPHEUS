"""Explicit manual controller eligibility and actual Enforcer projectile consumers.

Native controller allocation, AI, specials and complete Enforcer combat remain open.
"""

import hashlib
import json
import os
import struct
import unittest
from pathlib import Path

import test_orisa_barrier as barrier_fixture
import test_orisa_npc_incoming as fixture
import test_orisa_stored_state as stored_fixture
from test_game_combat import SOLDIER, aim, in_world

from ow174.game import combat, npc, npc_context, npc_control
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import expr, runtime


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_RUNTIME"), "actual external Talon graphs required")
class OrisaEnforcerContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.OrisaNpcIncomingTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixture.OrisaNpcIncomingTests.tearDownClass()

    def arena(self, **kwargs):
        t = fixture.OrisaNpcIncomingTests()
        t.catalog = fixture.OrisaNpcIncomingTests.catalog
        run, player, bot = t.arena("Enforcer", **kwargs)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        return t, run, player, bot, weapon

    def test_manual_predicate_matches_actual_native_component_bitset_cases(self):
        path = (Path(os.environ["ORPHEUS_NPC_RUNTIME"]).parent
                / "enforcer-controller-tail-comparison-20261109.json")
        fixture_data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(fixture_data["evaluator_rva"], "CC9BF0")
        self.assertTrue(fixture_data["all_pass"])
        for row in fixture_data["rows"]:
            with self.subTest(components=row["controller_components"]):
                self.assertEqual(npc_context.controller_predicate(row["controller_components"]),
                                 row["native_result"])

    def test_actual_owner_gate_preserves_component_and_scope_refusals(self):
        _, _run, player, bot, weapon = self.arena()
        world = weapon.component.world
        cfg = weapon.graph.node(1).field("m_condition")["m_configVars"][1]
        self.assertIs(npc_context.native(world, cfg, weapon), True)
        for components in ((), (92,), (75, 92)):
            world.controller_components = components
            self.assertIs(npc_context.native(world, cfg, weapon), False)
        world.controller_components = None
        self.assertIs(npc_context.native(world, cfg, weapon), NotImplemented)
        world.controller_components = (75,)
        self.assertIs(npc_context.native(world, cfg, None), NotImplemented)
        foreign = dict(cfg, m_entity={"$": "STUConfigVarDynamic", "m_identifier": 65500,
                                     "m_60DB8F99": 1})
        weapon.component.set(65500, expr.Entity(player.body))
        self.assertIs(npc_context.native(world, foreign, weapon), NotImplemented)
        other = next(i for i in weapon.component.instances.values() if i.graph.index != 0x13EB)
        self.assertIs(npc_context.native(world, cfg, other), NotImplemented)
        bot.spawner["manual_spawn"] = False
        self.assertIsNone(npc_context.manual_components(bot))

    def test_actual_25_pellet_input_hits_armour_and_fortify_without_forcing_gate(self):
        for fortified, per_hit in ((False, 1), (True, 0.6)):
            with self.subTest(fortify=fortified):
                t, run, player, bot, weapon = self.arena(fortify=fortified)
                volley = weapon.graph.node(24)
                self.assertEqual(weapon.evaluate(volley.field("m_numProjectilesPerShot")), 25)
                self.assertEqual(weapon.evaluate(volley.field("m_projectileSpeed")), 80)
                self.assertEqual(weapon.evaluate(volley.field("m_6396149F")["m_modifyHealth"]["m_amount"]), 2)
                ammo = weapon.vars[53].value()
                t.start(run, player, bot)
                for _ in range(200):
                    run.step({})
                    pellets = [r for r in run.match.combat.rockets if r.shooter is bot]
                    if pellets:
                        break
                self.assertEqual(len(pellets), 25)
                self.assertEqual(len({r.direction for r in pellets}), 25)
                self.assertEqual(weapon.vars[53].value(), ammo - 1)
                target = next(t for t in run.match.combat._targets() if t.owner is player)
                a, b = target.segment()
                hits = 0
                for rocket in pellets:
                    distance = combat.ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
                    if distance is not None and distance <= rocket.speed * rocket.left:
                        hits += 1
                self.assertGreater(hits, 0)
                self.assertLessEqual(hits, 25)
                # Allow the whole flight to reach the stationary body; misses
                # continue beyond it. No second1Hz volley can start in20 frames.
                before = t.vitals(run, player).total
                for _ in range(20):
                    run.step({})
                self.assertAlmostEqual(before - t.vitals(run, player).total, hits * per_hit, places=5)
                self.assertTrue(bot.attack_input)
                self.assertIsNotNone(bot.body_script)

    def test_emitted_enforcer_pellet_hits_before_range_limit_and_expires_beyond_it(self):
        for distance, outcome in ((99.5, "body"), (101.5, "expired")):
            with self.subTest(distance=distance):
                helper, run, player, bot, weapon = self.arena(ahead=60)
                fight = run.match.combat
                x, _, z = bot.position
                bot.position = (x, fight._eye(player)[1] - combat.EYE, z)
                volley = weapon.graph.node(24)
                reach = weapon.evaluate(volley.field("m_FEC435C6"))
                self.assertEqual(reach, 100)
                helper.start(run, player, bot)
                for _ in range(200):
                    run.step({})
                    pellets = [rocket for rocket in fight.rockets if rocket.shooter is bot]
                    if pellets:
                        break
                self.assertEqual(len(pellets), 25)
                pellet = min(pellets, key=lambda rocket: abs(rocket.direction[1]))
                self.assertAlmostEqual(pellet.left * pellet.speed + pellet.travelled, reach)
                origin = pellet.position
                direction = pellet.direction
                point = tuple(a + distance * b for a, b in zip(origin, direction, strict=True))
                floor = player.mover.position[1]
                barrier_fixture.BarrierTests.place(player, (point[0], floor, point[2]))
                target = next(t for t in fight._targets() if t.owner is player)
                a, b = target.segment()
                hit = combat.ray_capsule(origin, direction, a, b, target.radius)
                self.assertIsNotNone(hit, (origin, direction, point, a, b))
                self.assertEqual(hit <= pellet.left * pellet.speed, outcome == "body")
                bot.firing = False  # release new input without retiring this volley
                health = helper.vitals(run, player)
                before = health.total
                for _ in range(100):
                    if pellet.ended:
                        break
                    run.step({})
                self.assertEqual(pellet.ended, outcome)
                if outcome == "body":
                    self.assertLess(health.total, before)
                else:
                    self.assertAlmostEqual(pellet.travelled, reach, places=4)
                    self.assertEqual(health.total, before)

    def test_manual_enforcer_releases_and_reacquires_across_engagement_range(self):
        helper, run, player, bot, weapon = self.arena(ahead=8)
        fight = run.match.combat
        ammo = weapon.vars[53]
        initial_ammo = ammo.value()
        x, _, z = bot.position
        floor = player.mover.position[1]
        self.assertEqual(helper.start(run, player, bot), bot.entity)
        barrier_fixture.BarrierTests.place(player, (x, floor, z - 100.5))
        for _ in range(90):
            run.step({})
            self.assertFalse(bot.attack_input)
            self.assertFalse(fight.rockets)
        self.assertEqual(ammo.value(), initial_ammo)

        barrier_fixture.BarrierTests.place(player, (x, floor, z - 99.5))
        for _ in range(200):
            run.step({})
            pellets = [rocket for rocket in fight.rockets if rocket.shooter is bot]
            if pellets:
                break
        self.assertTrue(bot.attack_input)
        self.assertEqual(len(pellets), 25)
        self.assertEqual(ammo.value(), initial_ammo - 1)

        barrier_fixture.BarrierTests.place(player, (x, floor, z - 101.5))
        health = helper.vitals(run, player)
        before = health.total
        for _ in range(100):
            run.step({})
            self.assertFalse(bot.attack_input)
        self.assertTrue(all(pellet.ended is not None for pellet in pellets))
        self.assertTrue(any(pellet.ended == "expired" for pellet in pellets))
        self.assertEqual(health.total, before)
        self.assertEqual(ammo.value(), initial_ammo - 1)
        self.assertEqual(bot.attack_target, player.entity)
        self.assertTrue(bot.firing)

        barrier_fixture.BarrierTests.place(player, (x, floor, z - 8))
        self.assertGreater(helper.first_damage(run, player), 0)
        self.assertTrue(bot.attack_input)

    def test_held_enforcer_order_exhausts_reloads_and_hits_after_recovery(self):
        self.exercise_enforcer_reload(stop_during_reload=False)

    def test_enforcer_reload_finishes_after_stop_and_requires_fresh_order(self):
        self.exercise_enforcer_reload(stop_during_reload=True)

    def test_enforcer_reload_continues_during_actual_flashbang_stun(self):
        self.exercise_enforcer_reload(stop_during_reload=False, flash_during_reload=True)

    def exercise_enforcer_reload(self, *, stop_during_reload, flash_during_reload=False):
        helper, run, player, bot, weapon = self.arena(ahead=60)
        ammo = weapon.vars[53]
        magazine = weapon.vars[198].value()
        self.assertEqual(ammo.value(), magazine)
        reload_node = weapon.graph.node(32)
        fight = run.match.combat
        if flash_during_reload:
            caster = run.match.add_player(3, "Ally McCree", SOLDIER, 2, False)
            in_world(run.match, caster)
            run.players.append(caster)
            self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
            caster.team = player.team
            caster.client.bot_stream = lambda _: None
            x, y, z = bot.position
            barrier_fixture.BarrierTests.place(caster, (x + 3, player.mover.position[1], z))
            flash_yaw, flash_pitch = aim(fight._eye(caster), (x, y + 1, z))
            bot_health = fight.vitals[bot.entity]
            before_flash = bot_health.total
        helper.start(run, player, bot)
        counts = [ammo.value()]
        for _ in range(2000):
            run.step({})
            if ammo.value() != counts[-1]:
                counts.append(ammo.value())
            if not ammo.value():
                break
        self.assertEqual(counts, list(range(int(magazine), -1, -1)))
        emptied = run.frame
        in_flight = tuple(run.match.combat.rockets)
        last_volley_ids = {id(rocket) for rocket in in_flight}
        self.assertTrue(last_volley_ids)
        reloading = stopped = flash_cast = stunned_in_reload = False
        for _ in range(300):
            run.step({})
            state = weapon.states.get(reload_node.state)
            reloading |= state is not None and state.active
            if ammo.value():
                break
            if stop_during_reload and reloading and not stopped:
                self.assertEqual(npc_control.stop(run.match), 1)
                self.assertFalse(run.match.combat.rockets)
                stopped = True
            if flash_during_reload and reloading and not flash_cast:
                run.step({caster: (flash_yaw, flash_pitch, 16)})
                flash_cast = True
            if fight.is_stunned(bot):
                self.assertTrue(state.active)
                stunned_in_reload = True
            self.assertEqual(bot.attack_input, not stopped and not fight.is_stunned(bot))
            self.assertTrue(all(id(rocket) in last_volley_ids for rocket in fight.rockets if rocket.shooter is bot))
        self.assertTrue(reloading)
        if flash_during_reload:
            self.assertTrue(stunned_in_reload)
            self.assertEqual(before_flash - bot_health.total, 25)
        self.assertGreater(run.frame - emptied, 1)
        self.assertEqual(ammo.value(), magazine)
        self.assertEqual(stopped, stop_during_reload)
        self.assertEqual(bot.firing, not stopped)
        self.assertEqual(bot.attack_target, None if stopped else player.entity)

        x, _, z = bot.position
        barrier_fixture.BarrierTests.place(player, (x, player.mover.position[1], z - 8))
        while fight.is_stunned(bot):
            self.assertEqual(ammo.value(), magazine)
            run.step({})
        if stopped:
            self.assertEqual(helper.first_damage(run, player, limit=90), 0)
            self.assertEqual(ammo.value(), magazine)
            self.assertFalse(run.match.combat.rockets)
            self.assertFalse(bot.attack_input)
            self.assertEqual(helper.start(run, player, bot), bot.entity)
        self.assertGreater(helper.first_damage(run, player), 0)
        self.assertTrue(bot.attack_input)
        self.assertEqual(ammo.value(), magazine - 1)

    def test_actual_fusion_driver_kill_stops_enforcer_fire_during_reload(self):
        helper, run, player, bot, weapon = self.arena()
        fight = run.match.combat
        health = fight.vitals[bot.entity]
        x, y, z = bot.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        for _ in range(1000):
            if health.total <= 40:
                break
            run.step({player: (yaw, pitch, FIRE)})
        self.assertGreater(health.total, 0)
        self.assertLessEqual(health.total, 40)
        floor = player.mover.position[1]
        barrier_fixture.BarrierTests.place(player, (x, floor, z - 60))
        helper.start(run, player, bot)
        reload_node = weapon.graph.node(32)
        for _ in range(2000):
            run.step({})
            reload_state = weapon.states.get(reload_node.state)
            if reload_state is not None and reload_state.active:
                break
        self.assertTrue(reload_state.active)
        self.assertEqual(weapon.vars[53].value(), 0)

        barrier_fixture.BarrierTests.place(player, (x, floor, z - 8))
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        for _ in range(60):
            self.assertTrue(reload_state.active)
            run.step({player: (yaw, pitch, FIRE)})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertEqual(health.total, 0)
        retired_script = bot.body_script
        self.assertTrue(retired_script.component.owner_dead)
        death_frame = retired_script.frame
        old_pellets = tuple(rocket for rocket in fight.rockets if rocket.shooter is bot)
        old_ids = {id(rocket) for rocket in old_pellets}
        for _ in range(180):
            run.step({})
            self.assertTrue(all(id(rocket) in old_ids for rocket in fight.rockets if rocket.shooter is bot))
        self.assertLess(death_frame, retired_script.frame)
        self.assertLess(retired_script.frame, run.frame)
        magazine = weapon.vars[198].value()
        self.assertEqual(weapon.vars[53].value(), magazine)
        self.assertFalse(bot.attack_input)
        self.assertFalse(any(rocket.shooter is bot for rocket in fight.rockets))
        self.assertFalse(any(actor is bot for _, actor in fight.respawns))
        self.assertIn(bot, run.match.bots)

        fresh = npc.spawn(run.match, helper.definition("Enforcer"), bot.position, team=bot.team)
        self.assertNotEqual(fresh.entity, bot.entity)
        fresh_weapon = next(i for i in fresh.body_script.component.instances.values() if i.graph.index == 0x13EB)
        self.assertEqual(fresh_weapon.vars[53].value(), magazine)
        self.assertEqual(helper.start(run, player, fresh), fresh.entity)
        self.assertGreater(helper.first_damage(run, player), 0)
        self.assertEqual(weapon.vars[53].value(), magazine)

    def test_authored_enforcer_ammo_messages_refill_without_starting_fire(self):
        for message in (0x024000000000007D, 0x0240000000000013):
            with self.subTest(message=hex(message)):
                helper, run, player, bot, weapon = self.arena(ahead=60)
                ammo = weapon.vars[53]
                magazine = weapon.vars[198].value()
                helper.start(run, player, bot)
                for _ in range(200):
                    run.step({})
                    if ammo.value() < magazine:
                        break
                self.assertEqual(ammo.value(), magazine - 1)
                self.assertEqual(npc_control.stop(run.match), 1)
                component = bot.body_script.component
                self.assertEqual(runtime.send_instance_message(weapon, message, {}, bot.entity), 1)
                component.run_now()
                self.assertEqual(ammo.value(), magazine)
                health = helper.vitals(run, player)
                before = health.total
                for _ in range(90):
                    run.step({})
                self.assertEqual(ammo.value(), magazine)
                self.assertEqual(health.total, before)
                self.assertFalse(bot.attack_input)
                self.assertFalse(run.match.combat.rockets)

    def test_ring_model_matches_525_actual_native_direction_samples(self):
        from ow174.game.weapon_spread import float_table, ring_direction, uniform_table

        path = Path(os.environ["ORPHEUS_ORISA_HALT"]).parent / "enforcer-ring-native-expanded.json"
        samples = json.loads(path.read_text(encoding="utf-8"))
        tables = samples["table_checks"]
        self.assertEqual(hashlib.sha256(struct.pack('<16384I', *uniform_table())).hexdigest(),
                         tables["uint_sha256"])
        self.assertEqual(hashlib.sha256(struct.pack('<16384f', *float_table())).hexdigest(),
                         tables["float_sha256"])
        count = 0
        for row in samples["rows"]:
            for pellet, expected in enumerate(row["directions"]):
                actual = ring_direction(row.get("forward", (0, 0, 1)), row["spread"], row["jitter"],
                                        row["phase"], ((0, 1), (.5, 8), (1, 16)),
                                        row.get("base", 123), row.get("shot", 2), pellet)
                self.assertLess(max(abs(a-b) for a, b in zip(actual, expected, strict=True)), 2e-6)
                count += 1
        self.assertEqual(count, 525)

    def test_actual_server_state_captures_controller_eligibility_causally(self):
        stored = stored_fixture.OrisaStoredStateTests()
        session, fight = stored.scene()
        definition = next(d for d in fixture.OrisaNpcIncomingTests.catalog.values() if d.name == "Enforcer")
        position = fight.match.game_map.spawns[0].position
        bot = npc.spawn(fight.match, definition, position, team=1)
        world = bot.body_script.component.world
        self.assertEqual(world.controller_components, (75,))
        eligible = stored.digest(session)
        world.controller_components = (75, 92)
        self.assertNotEqual(stored.digest(session), eligible)
        world.controller_components = (75,)
        self.assertEqual(stored.digest(session), eligible)


if __name__ == "__main__":
    unittest.main()
