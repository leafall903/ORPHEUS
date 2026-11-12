"""Ashe damage against five real external NPC definitions, without claiming enemy AI.

Set ORPHEUS_PVE_NPC_DIR to the read-only directory containing the prepared catalog
and graph bundle. Collision and transport remain the explicit in-memory fixtures.
The body VM remains permissive; these checks do not establish complete NPC actions.
"""

import math
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_ashe_combat as fixtures

from ow174.game import ashe, combat, heavy_charge, immunity, mover, npc, npc_control, npc_melee, world
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr, graph, nodes

INPUTS = Path(os.environ.get("ORPHEUS_PVE_NPC_DIR", os.environ.get("ORPHEUS_NPC_DATA_DIR", ".")))
CATALOG = INPUTS / "pve_npcs_174.json"
BUNDLE = graph.GRAPHS_PATH


@unittest.skipUnless(CATALOG.is_file() and BUNDLE.is_file(), "requires explicit external PvE inputs")
class AshePveNpcTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight
    press = fixtures.AsheCombatTests.press

    @classmethod
    def setUpClass(cls):
        cls.catalog = npc.load_catalog(CATALOG)
        # Use the actual composed graph provider and current locked mover table.
        # The explicit current-bound mover extension is already selected by the profile.
        cls.bundle = graph._data()
        mover._clear_movers()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    @classmethod
    def tearDownClass(cls):
        mover._clear_movers()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    def spawn(self, definition, distance, *, ally=False, x=0):
        run, player, unused = self.fight(distance=100, team_game=ally)
        run.match.leave(unused)
        run.players.remove(unused)
        player.client.queue_bot_death = lambda *args: None
        player.client.queue_bot_death_update = lambda *args: None
        player.client.bot_stream = lambda entity: None  # explicit fixture: no NPC projectile observer stream
        team = player.team if ally else 1 - player.team
        bot = npc.spawn(run.match, definition, (x, player.mover.position[1], distance), team=team)
        self.assertIsNotNone(bot.body_script)
        self.assertEqual(bot.body, definition.body)
        self.assertTrue(bot.retain_corpse)
        # Record the creation boundary and stable pose before firing.
        for _ in range(10):
            run.step({})
        return run, player, bot

    def test_viper_hip_ads_and_melee_hit_each_authored_enemy(self):
        for definition in self.catalog.values():
            for mode, damage in (("hip", 40), ("ads", 75), ("melee", 30)):
                with self.subTest(npc=definition.name, mode=mode):
                    run, player, bot = self.spawn(definition, 1 if mode == "melee" else 8)
                    vitals = run.match.combat.vitals[bot.entity]
                    before = vitals.total
                    _yaw, pitch = fixtures.aim(run.match.combat._eye(player),
                                               (0, bot.position[1] + 1, bot.position[2]))
                    if mode == "ads":
                        for _ in range(40):
                            run.step({player: (0, pitch, 2)})
                        for _ in range(20):
                            run.step({player: (0, pitch, 3)})
                    else:
                        self.press(run, player, 0x800 if mode == "melee" else 1, pitch=pitch)
                    self.assertAlmostEqual(before - vitals.total, damage)
                    self.assertEqual(vitals.now[world.SHIELDS_POOL], max(0, definition.shields - damage))
                    self.assertFalse(bot.dead)

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", ".")).is_file(),
                         "requires extracted NPC mover extension")
    def test_viper_critical_hits_kill_all_five_authored_npcs_from_full_health(self):
        self.assertEqual({item.name for item in self.catalog.values()},
                         {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"})
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.spawn(definition, 2)
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                script = bot.body_script
                weapon = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x14FC)
                ammo = weapon.vars[53]
                target = next(item for item in fight._targets() if item.owner is bot)
                self.assertIs(mover.mover_data(bot.body), mover.loaded_mover_table()[bot.body])
                # Aim inside the reconstructed critical band, including the shorter Assassin capsule.
                critical_bottom = target.eye - combat.HEAD_BELOW
                head_y = target.feet[1] + (critical_bottom + min(target.height, target.eye)) / 2
                _, pitch = fixtures.aim(fight._eye(player), (0, head_y, target.feet[2]))
                self.assertEqual(vitals.total, definition.health + definition.shields + definition.armour)
                self.assertEqual(ammo.value(), 12)
                remaining = vitals.total - 80

                self.press(run, player, 1, frames=60, pitch=pitch)
                self.assertEqual(vitals.total, remaining)
                self.assertEqual(ammo.value(), 11)
                hit, = player.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"], {
                    "+0x0": {"+0x0": bot.entity}, "+0x4": 80, "+0x8": combat.CRITICAL,
                })

                def fire_ads():
                    previous_ammo = ammo.value()
                    for _ in range(5):
                        run.step({player: (0, pitch, 3)})
                    self.assertEqual(ammo.value(), previous_ammo - 1)
                    emptied = ammo.value() == 0
                    for _ in range(55):
                        run.step({player: (0, pitch, 0 if emptied else 2)})
                    return emptied

                for _ in range(40):
                    run.step({player: (0, pitch, 2)})
                rounds = math.ceil(remaining / 150)
                reloads = 0
                emptied = False
                with (
                    patch.object(script, "die", wraps=script.die) as die,
                    patch.object(
                        player.client, "queue_bot_death", wraps=player.client.queue_bot_death,
                    ) as death,
                ):
                    for index in range(rounds):
                        # Keep ADS released until the empty magazine refills.
                        if emptied:
                            for _ in range(450):
                                if ammo.value() == 12:
                                    break
                                run.step({})
                            self.assertEqual(ammo.value(), 12)
                            reloads += 1
                            for _ in range(40):
                                run.step({player: (0, pitch, 2)})

                        emptied = fire_ads()
                        dealt = min(150, remaining)
                        remaining -= dealt
                        self.assertEqual(vitals.total, remaining)
                        self.assertEqual(bot.dead, remaining == 0)
                        hits = player.client.messages(combat.HIT)
                        self.assertEqual(len(hits), index + 2)
                        flags = combat.CRITICAL | (combat.KILLING if remaining == 0 else 0)
                        self.assertEqual(hits[-1]["+0x78"], {
                            "+0x0": {"+0x0": bot.entity}, "+0x4": dealt, "+0x8": flags,
                        })

                    # A fresh shot traverses the same space after death.
                    fire_ads()
                    self.assertEqual(len(player.client.messages(combat.HIT)), rounds + 1)
                    self.assertEqual(len(player.client.messages(combat.KILL_NOTICE)), 1)
                    die.assert_called_once()
                    _, death_params, sender = die.call_args.args
                    self.assertEqual(sender, player.body)
                    self.assertEqual(death_params[combat.KILLER], expr.Entity(player.body))
                    self.assertIs(death_params[combat.CRIT], True)
                    death.assert_called_once()
                    self.assertEqual(death.call_args.args[0], bot.entity)

                self.assertEqual(reloads, rounds // 12)
                self.assertEqual(vitals.killer, player.body)
                self.assertIs(bot.body_script, script)
                self.assertIsNone(script.component.fault)
                self.assertIn(bot, run.match.bots)
                self.assertEqual(fight.respawns, [])

    def test_coach_gun_hits_each_authored_enemy_with_real_base_pools(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.spawn(definition, 1)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                _yaw, pitch = fixtures.aim(run.match.combat._eye(player),
                                           (0, bot.position[1] + 0.85, bot.position[2]))
                self.press(run, player, 8, pitch=pitch)
                self.assertEqual(before - vitals.total, 90)
                self.assertFalse(bot.dead)

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", ".")).is_file(),
                         "requires extracted NPC mover extension")
    def test_live_dynamite_shot_and_fuse_apply_blast_and_burn_to_authored_npcs(self):
        from test_ashe_impulses import wall_scene

        self.assertEqual(len(self.catalog), 5)
        for definition in self.catalog.values():
            for mode in ("shot", "fuse", "ally", "wall", "outside"):
                with self.subTest(npc=definition.name, mode=mode):
                    run, player, bot = self.spawn(definition, 22 if mode == "outside" else 16.4,
                                                  ally=mode == "ally")
                    self.assertIs(mover.mover_data(bot.body), mover.loaded_mover_table()[bot.body])
                    if mode == "wall":
                        run.match.collision_world = wall_scene(15.6)
                    fight = run.match.combat
                    vitals = fight.vitals[bot.entity]
                    before = vitals.total
                    engine = fight.dynamite
                    self.press(run, player, 16)
                    grenade, = engine.grenades
                    self.assertEqual(grenade.fuse - grenade.born, math.ceil(ashe.FUSE / combat.TICK))

                    # Track the live grenade with normal ADS input through throw recovery.
                    while run.frame < grenade.born + 41:
                        yaw, pitch = fixtures.aim(fight._eye(player), grenade.position)
                        run.step({player: (yaw, pitch, 2)})
                    self.assertEqual(vitals.total, before)
                    self.assertGreater(grenade.bounces, 0)
                    yaw, pitch = fixtures.aim(fight._eye(player), grenade.position)
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as reports:
                        run.step({player: (16384 if mode == "fuse" else yaw, pitch, 3)})
                    reports.assert_called_once()
                    volley = reports.call_args.args[2]
                    self.assertEqual((volley.instance.graph.index, volley.node.pos), (0x14FC, 14))
                    self.assertLess(run.frame, grenade.fuse)

                    if mode == "fuse":
                        self.assertIn(grenade, engine.grenades)
                        while run.frame < grenade.fuse - 1:
                            run.step({})
                        self.assertIn(grenade, engine.grenades)
                        self.assertEqual(vitals.total, before)
                        self.assertEqual(engine.burns, {})
                        run.step({})

                    self.assertNotIn(grenade, engine.grenades)
                    if mode not in ("shot", "fuse"):
                        self.assertEqual(vitals.total, before)
                        self.assertEqual(engine.burns, {})
                        self.assertEqual(player.client.messages(combat.HIT), [])
                        continue

                    self.assertEqual(vitals.total, before - 50)
                    burn = engine.burns[(player.body, bot.entity)]
                    self.assertEqual(burn.start, run.frame)
                    self.assertEqual(burn.until - burn.start, math.ceil(ashe.BURN_SECONDS / combat.TICK))
                    while run.frame < burn.until - 1:
                        run.step({})
                    self.assertEqual(burn.pulses, 19)
                    self.assertEqual(vitals.total, before - 145)
                    run.step({})
                    self.assertEqual(burn.pulses, 20)
                    self.assertEqual(vitals.total, before - 150)
                    self.assertEqual(vitals.now[world.SHIELDS_POOL], max(0, definition.shields - 150))
                    self.assertFalse(bot.dead)
                    self.assertEqual(engine.burns, {})
                    hits = [notice["+0x78"] for notice in player.client.messages(combat.HIT)]
                    self.assertEqual([hit["+0x4"] for hit in hits], [50] + [5] * 20)
                    self.assertTrue(all(hit["+0x0"]["+0x0"] == bot.entity for hit in hits))

    def test_untouched_dynamite_rebounds_from_each_authored_enemy(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.spawn(definition, 5)
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                _, pitch = fixtures.aim(fight._eye(player), (0, bot.position[1] + 1, bot.position[2]))
                self.press(run, player, 16, pitch=pitch)
                grenade, = fight.dynamite.grenades
                while run.frame < grenade.born + 18:
                    run.step({})
                self.assertEqual(grenade.bounces, 1)
                self.assertLess(grenade.velocity[2], 0)
                self.assertEqual(vitals.total, before)
                self.assertEqual(fight.dynamite.burns, {})
                while run.frame < grenade.fuse:
                    run.step({})
                self.assertNotIn(grenade, fight.dynamite.grenades)
                self.assertLess(vitals.total, before)
                self.assertIn((player.body, bot.entity), fight.dynamite.burns)

    def test_weapons_and_dynamite_exclude_each_allied_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.spawn(definition, 8, ally=True)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                self.press(run, player, 1, frames=60)
                self.press(run, player, 8, frames=60)
                self.press(run, player, 16)
                engine = run.match.combat.dynamite
                grenade, = engine.grenades
                grenade.position = (0, bot.position[1] + 0.8, 8)
                engine.detonate(grenade)
                for _ in range(math.ceil(ashe.BURN_SECONDS / combat.TICK)):
                    run.step({})
                self.assertEqual(vitals.total, before)
                self.assertEqual(engine.burns, {})
                self.assertEqual(player.client.messages(combat.HIT), [])

    def test_controlled_trooper_and_heavy_weapons_damage_ashe(self):
        # Exercise actual NPC weapon graphs by their authored fire button. This
        # is a controlled attack fixture, not a navigation/acquisition AI claim.
        for name, expected in (("Trooper", 60), ("Heavy Assault", 85)):
            with self.subTest(npc=name):
                definition = next(item for item in self.catalog.values() if item.name == name)
                run, player, bot = self.spawn(definition, 8)
                target = next(item for item in run.match.combat._targets() if item.owner is player)
                vitals = run.match.combat.vitals_of(target)
                player.mover.yaw, player.mover.pitch = fixtures.aim(run.match.combat._eye(player),
                                                                  (0, bot.position[1] + 1, 8))
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(100):
                    run.step({})
                self.assertEqual(200 - vitals.total, expected)
                self.assertIsNone(bot.body_script.component.fault)

    def test_controlled_npc_melee_hits_ashe_after_windup(self):
        for name, damage, push in (("Trooper", 30, (0, 0, -1)), ("Heavy Assault", 80, (0, 8, -20))):
            with self.subTest(npc=name):
                run, player, bot, state = self.start_melee(name)
                fight = run.match.combat
                vitals = fight.vitals[player.body]
                self.assertEqual(vitals.total, 200)
                self.assertGreater(state.remaining(), 0.9)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    for _ in range(10):
                        run.step({})
                    self.assertEqual(vitals.total, 200)
                    for _ in range(20):
                        run.step({})
                        if vitals.total < 200:
                            break
                    self.assertEqual(vitals.total, 200 - damage)
                    impulse, = fight.impulses.pending[player.body]
                    self.assertEqual(impulse.vector, push)
                    self.assertEqual(impulse.due_tick, fight.tick + 1)
                    self.assertEqual(npc_control.stop(run.match), 1)
                    self.assertFalse(state.flags & nodes.Ability.RUNNING)
                    for _ in range(60):
                        run.step({})
                    shots.assert_not_called()
                self.assertEqual(vitals.total, 200 - damage)
                self.assertIsNone(bot.body_script.component.fault)

    def test_controlled_npc_melee_interrupts_and_keeps_cooldown(self):
        from test_ashe_impulses import wall_scene

        for name in ("Trooper", "Heavy Assault"):
            for interruption in ("wall", "stop"):
                with self.subTest(npc=name, interruption=interruption):
                    run, player, bot, state = self.start_melee(name)
                    fight = run.match.combat
                    open_world = run.match.collision_world
                    if interruption == "wall":
                        run.match.collision_world = wall_scene(1)
                    else:
                        npc_control.stop(run.match)
                    run.step({})
                    self.assertFalse(state.flags & nodes.Ability.RUNNING)
                    self.assertGreater(state.remaining(), 0.9)
                    for _ in range(30):
                        run.step({})
                    self.assertEqual(fight.vitals[player.body].total, 200)
                    self.assertNotIn(player.body, fight.impulses.pending)

                    run.match.collision_world = open_world
                    self.assertEqual(npc_control.start(run.match, player), bot.entity)
                    for _ in range(10):
                        run.step({})
                    self.assertFalse(state.flags & nodes.Ability.RUNNING)
                    self.assertGreater(state.remaining(), 0)
                    for _ in range(35):
                        run.step({})
                        if state.flags & nodes.Ability.RUNNING:
                            break
                    self.assertTrue(state.flags & nodes.Ability.RUNNING)
                    self.assertEqual(fight.vitals[player.body].total, 200)

    def test_trooper_tracks_outside_its_melee_damage_gate(self):
        run, player, bot, state = self.start_melee("Trooper", distance=2)
        fight = run.match.combat
        observed = []
        for _ in range(30):
            run.step({})
            observed.append(state.instance.vars[375].value())
        self.assertTrue(any(observed))
        self.assertEqual(fight.vitals[player.body].total, 200)
        self.assertNotIn(player.body, fight.impulses.pending)
        self.assertIsNone(bot.body_script.component.fault)

    def test_heavy_charge_graph_routes_declared_player_contact_to_carry_and_impact(self):
        definition = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        run, player, bot = self.spawn(definition, 20)
        fight = run.match.combat
        player.mover.yaw, player.mover.pitch = fixtures.aim(fight._eye(player), (0, bot.position[1] + 1, 20))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        component = bot.body_script.component
        instance = next(item for item in component.instances.values() if item.graph.index == 0x1408)
        victim = expr.Entity(player.body)
        contacts = ()
        impact = []

        def track(state):
            if state.instance is instance:
                return contacts if state.node.pos == 96 else ()
            return NotImplemented

        def health(state, elapsed):
            if state.instance is instance and state.node.pos == 29:
                chosen = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
                amount = state.evaluate(state.node.field("m_modifyHealth")["m_amount"], watch=False)
                impact.append((elapsed, chosen, amount))
            return False

        def state_at(position):
            return instance.states[instance.graph.node(position).state]

        ability = state_at(82)
        start, _, _ = ability.buttons()
        self.assertEqual(start, 35)
        with (patch.object(heavy_charge, "update", return_value=False),
              patch.object(component.world, "track_targets", side_effect=track),
              patch.object(component.world, "modify_health", side_effect=health)):
            component.edges([(start, True)])
            run.step({})
            component.edges([(start, False)])
            self.assertTrue(state_at(137).active)
            self.assertEqual(instance.vars[59].value(), 1)
            self.assertEqual(ability.remaining(), 0)
            for _ in range(49):
                run.step({})
                self.assertFalse(instance.vars[536].value())
            run.step({})
            self.assertTrue(state_at(71).active)
            self.assertTrue(state_at(96).active)
            self.assertEqual(instance.vars[562].value(), ())
            self.assertEqual(impact, [])

            # Declare the position 96 query hit and the later movement stop.
            contacts = (victim,)
            run.step({})
            self.assertEqual(instance.vars[562].value(), contacts)
            self.assertEqual(instance.vars[536].value(), victim)
            self.assertTrue(state_at(158).active)
            effect = state_at(158).node.field("m_script")
            self.assertEqual(expr.guid_of(effect["m_graph"]), 0x0580000000001407)
            self.assertEqual(impact, [])
            instance.write((expr.INSTANCE, 59), 2)
            run.step({})
            self.assertFalse(state_at(158).active)
            self.assertFalse(state_at(71).active)
            self.assertTrue(state_at(50).active)
            self.assertEqual(impact, [(0, victim, 300)])
            self.assertGreater(ability.remaining(), 14.9)
            for _ in range(8):
                run.step({})
            self.assertEqual(impact, [(0, victim, 300)])
            self.assertLess(ability.remaining(), 14.9)

    def test_heavy_charge_carries_ashe_to_wall_and_delivers_impact(self):
        from test_ashe_impulses import wall_scene

        run, player, bot, charge = self.start_heavy_charge()
        fight = run.match.combat
        vitals = fight.vitals[player.body] = combat.Vitals(player.body, 400)
        run.match.collision_world = wall_scene(-4)
        ability = charge.states[charge.graph.node(82).state]
        for _ in range(30):
            run.step({})
        self.assertEqual(bot.position[2], 12)
        self.assertEqual(vitals.total, 400)
        for _ in range(100):
            run.step({})
            if immunity.heavy_carry_active(player, player.body):
                break
        self.assertTrue(immunity.heavy_carry_active(player, player.body))
        self.assertEqual(charge.vars[536].value(), expr.Entity(player.body))
        self.assertEqual(vitals.total, 400)
        self.assertEqual(ability.remaining(), 0)
        for _ in range(40):
            run.step({})
            if vitals.total < 400:
                break
        self.assertEqual(vitals.total, 100)
        self.assertLess(player.mover.position[2], -2)
        self.assertGreater(bot.position[2], -4)
        self.assertFalse(immunity.heavy_carry_active(player, player.body))
        self.assertEqual(charge.vars[59].value(), 2)
        self.assertGreater(ability.remaining(), 14.9)
        npc_control.stop(run.match)
        for _ in range(40):
            run.step({})
        self.assertEqual(charge.vars[59].value(), 0)
        self.assertGreater(ability.remaining(), 14)
        self.assertEqual(vitals.total, 100)
        self.assertIsNone(bot.body_script.component.fault)

    def test_heavy_charge_stop_releases_carried_ashe_without_impact(self):
        run, player, bot, charge = self.start_heavy_charge()
        fight = run.match.combat
        vitals = fight.vitals_of(next(item for item in fight._targets() if item.owner is player))
        for _ in range(120):
            run.step({})
            if immunity.heavy_carry_active(player, player.body):
                break
        self.assertTrue(immunity.heavy_carry_active(player, player.body))
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(immunity.heavy_carry_active(player, player.body))
        stopped = bot.position
        for _ in range(40):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(charge.vars[59].value(), 0)
        self.assertGreater(charge.vars[61].value(), 14)

        bot.position = (0, bot.position[1], 12)
        player.mover.yaw, player.mover.pitch = fixtures.aim(
            fight._eye(player), (0, bot.position[1] + 1, 12))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        run.step({})
        self.assertEqual(charge.vars[59].value(), 0)
        self.assertGreater(charge.vars[61].value(), 14)
        npc_control.stop(run.match)
        run.frame += 950
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(charge.vars[61].value(), 0)
        player.mover.yaw, player.mover.pitch = fixtures.aim(
            fight._eye(player), (0, bot.position[1] + 1, 12))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        run.step({})
        self.assertEqual(charge.vars[59].value(), 1)

    def test_heavy_charge_lethal_impact_retires_the_victim_effect(self):
        from test_ashe_impulses import wall_scene

        run, player, bot, charge = self.start_heavy_charge()
        fight = run.match.combat
        run.match.collision_world = wall_scene(-4)
        carried = False
        for _ in range(170):
            run.step({})
            carried |= immunity.heavy_carry_active(player, player.body)
            if fight.is_dead(player):
                break
        self.assertTrue(carried)
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(fight.vitals[player.body].killer, bot.entity)
        self.assertFalse(immunity.heavy_carry_active(player, player.body))
        self.assertEqual(charge.vars[59].value(), 2)
        self.assertGreater(charge.vars[61].value(), 14.9)

    def test_heavy_charge_carry_retires_on_source_or_victim_loss(self):
        for loss in ("source death", "source removal", "body replacement"):
            with self.subTest(loss=loss):
                run, player, bot, charge = self.start_heavy_charge()
                fight = run.match.combat
                for _ in range(120):
                    run.step({})
                    if immunity.heavy_carry_active(player, player.body):
                        break
                self.assertTrue(immunity.heavy_carry_active(player, player.body))
                previous = player.body_script.component
                if loss == "source death":
                    target = next(item for item in fight._targets() if item.owner is bot)
                    fight._damage(player, target, 10000, False, None, run.frame)
                    self.assertTrue(bot.dead)
                elif loss == "source removal":
                    self.assertTrue(npc.remove_actor(run.match, bot))
                else:
                    position = tuple(player.mover.position)
                    self.assertTrue(run.match.switch_hero(player, player.hero))
                    player.mover.position[:] = position
                    self.assertNotEqual(player.body, previous.entity)
                    run.step({})
                    self.assertEqual(charge.vars[59].value(), 2)
                self.assertFalse(immunity.heavy_carry_active(player, player.body))
                self.assertFalse(any(item.graph.index == 0x1407 for item in previous.instances.values()))
                self.assertFalse(fight.is_dead(player))

    def test_heavy_charge_misses_ashe_after_she_leaves_its_path(self):
        run, player, bot, charge = self.start_heavy_charge()
        fight = run.match.combat
        for _ in range(65):
            run.step({})
            if bot.position[2] < 11.5:
                break
        self.assertLess(bot.position[2], 11.5)
        for _ in range(140):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame, right=127, forward=-80)])
            self.assertFalse(immunity.heavy_carry_active(player, player.body))
            if charge.vars[59].value() == 2:
                break
        self.assertGreater(abs(player.mover.position[0]), 3)
        self.assertAlmostEqual(bot.position[0], 0)
        self.assertLess(bot.position[2], 0)
        self.assertEqual(charge.vars[59].value(), 2)
        self.assertGreater(charge.vars[61].value(), 14.9)
        self.assertEqual(fight.vitals[player.body].total, 200)

    def test_heavy_charge_stops_at_live_cryo_and_carries_after_cancel(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, mei, bot, charge = self.start_heavy_charge(hero=immunity.MEI)
                fight = run.match.combat
                vitals = fight.vitals_of(next(item for item in fight._targets() if item.owner is mei))
                before = vitals.total
                run.step({mei: (0, 0, 8)})
                self.assertTrue(immunity.cryo_active(mei, mei.body))
                for _ in range(30):
                    run.step({})
                if cancel:
                    run.step({mei: (0, 0, 2)})
                self.assertEqual(immunity.cryo_active(mei, mei.body), not cancel)
                for _ in range(105):
                    run.step({})
                    if charge.vars[59].value() == 2 or immunity.heavy_carry_active(mei, mei.body):
                        break
                self.assertEqual(vitals.total, before)
                self.assertNotIn(mei.body, fight.impulses.pending)
                self.assertEqual(immunity.heavy_carry_active(mei, mei.body), cancel)
                switch = charge.states[charge.graph.node(58).state]
                self.assertEqual(switch.current, not cancel)
                if not cancel:
                    self.assertEqual(charge.vars[59].value(), 2)
                    self.assertGreater(charge.vars[61].value(), 14.9)
                    self.assertTrue(immunity.cryo_active(mei, mei.body))
                    self.assertGreater(bot.position[2], 0)
                    stopped = bot.position
                    for _ in range(15):
                        run.step({})
                    self.assertEqual(bot.position, stopped)
                    self.assertEqual(bot.velocity, (0, 0, 0))
                    self.assertEqual(vitals.total, before)

    def test_intervening_cryo_stops_heavy_charge_before_it_reaches_ashe(self):
        run, player, mei = self.fight(distance=6)
        self.assertTrue(run.match.switch_hero(mei, heroes()[immunity.MEI]))
        mei.mover.position[:] = (4, 1, 6)
        definition = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        bot = npc.spawn(run.match, definition, (0, 1, 12), team=1 - player.team)
        fight = run.match.combat
        player.mover.yaw, player.mover.pitch = fixtures.aim(fight._eye(player), (0, 2, 12))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        mei.mover.position[:] = (0, 1, 6)
        run.step({mei: (0, 0, 8)})
        self.assertTrue(immunity.cryo_active(mei, mei.body))
        charge = next(item for item in bot.body_script.component.instances.values()
                      if item.graph.index == heavy_charge.CHARGE)
        targets = {item.entity: fight.vitals_of(item) for item in fight._targets() if item.owner is not bot}
        before = {body: vitals.total for body, vitals in targets.items()}
        for _ in range(100):
            run.step({})
            if charge.vars[59].value() == 2:
                break
        self.assertEqual(charge.vars[59].value(), 2)
        self.assertTrue(charge.states[charge.graph.node(58).state].current)
        self.assertGreater(bot.position[2], 6)
        self.assertEqual(bot.attack_target, player.entity)
        self.assertFalse(immunity.heavy_carry_active(player, player.body))
        self.assertFalse(immunity.heavy_carry_active(mei, mei.body))
        self.assertEqual({body: vitals.total for body, vitals in targets.items()}, before)

    def test_live_projected_barrier_before_heavy_charge_contact_or_impact(self):
        from test_ashe_controls import AsheBarrierTests
        from test_ashe_impulses import wall_scene

        definition = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        for cast_at in (None, "contact", "impact"):
            with self.subTest(cast_at=cast_at):
                run, player, zarya, spare = AsheBarrierTests.projected_arena(self)
                run.match.collision_world = wall_scene(-4) if cast_at == "impact" else flat_world(1)
                zarya.team = spare.team = player.team
                player.mover.position[:] = (0, 1, 0)
                zarya.mover.position[:] = (4, 1, 4)
                spare.mover.position[:] = (25, 1, 6)
                bot = npc.spawn(run.match, definition, (0, 1, 12), team=1 - player.team)
                fight = run.match.combat
                player.mover.yaw, player.mover.pitch = fixtures.aim(fight._eye(player), (0, 2, 12))
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                charge = next(item for item in bot.body_script.component.instances.values()
                              if item.graph.index == heavy_charge.CHARGE)
                movement = charge.graph.node(71)
                for _ in range(90):
                    run.step({})
                    state = charge.states.get(movement.state)
                    if state is not None and state.active:
                        break
                self.assertTrue(charge.states[movement.state].active)
                if cast_at == "contact":
                    aim, barrier = AsheBarrierTests.projected_input(run, zarya, player)
                    run.step({zarya: (*aim, 16)})
                    self.assertTrue(barrier.states[10].active)
                    self.assertEqual(expr.to_entity(barrier.vars[536].value()), player.body)

                for _ in range(75):
                    run.step({})
                    carried = immunity.heavy_carry_active(player, player.body)
                    if cast_at == "contact":
                        self.assertFalse(carried)
                    if carried or bot.position[2] < -2:
                        break
                self.assertEqual(fight.vitals[player.body].total, 200)
                self.assertEqual(carried, cast_at != "contact")
                if cast_at == "contact":
                    self.assertLess(bot.position[2], -2)
                    self.assertTrue(barrier.states[10].active)
                    self.assertNotIn(player.body, fight.personal_barriers)
                    self.assertEqual(charge.vars[59].value(), 1)
                elif cast_at == "impact":
                    aim, barrier = AsheBarrierTests.projected_input(run, zarya, player)
                    run.step({zarya: (*aim, 16)})
                    self.assertTrue(barrier.states[10].active)
                    self.assertEqual(expr.to_entity(barrier.vars[536].value()), player.body)
                    for _ in range(45):
                        run.step({})
                        if charge.vars[59].value() == 2:
                            break
                    self.assertEqual(charge.vars[59].value(), 2)
                    self.assertEqual(fight.vitals[player.body].total, 200)
                    self.assertFalse(immunity.heavy_carry_active(player, player.body))
                    self.assertFalse(barrier.states[10].active)
                    AsheBarrierTests.assert_energy_credit(self, zarya, 200, bot.entity, 40)

    def start_heavy_charge(self, *, hero=None):
        definition = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        run, player, bot = self.spawn(definition, 12)
        if hero is not None:
            position = tuple(player.mover.position)
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.mover.position[:] = position
        fight = run.match.combat
        player.mover.yaw, player.mover.pitch = fixtures.aim(fight._eye(player), (0, bot.position[1] + 1, 12))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        charge = next(item for item in bot.body_script.component.instances.values()
                      if item.graph.index == heavy_charge.CHARGE)
        return run, player, bot, charge

    def start_melee(self, name, *, distance=None):
        definition = next(item for item in self.catalog.values() if item.name == name)
        if distance is None:
            distance = 1.25 if name == "Trooper" else 2
        run, player, bot = self.spawn(definition, distance)
        fight = run.match.combat
        player.mover.yaw, player.mover.pitch = fixtures.aim(
            fight._eye(player), (0, bot.position[1] + 1, distance))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        run.step({})
        state = npc_melee.ability(bot.body_script.component)
        self.assertTrue(state.flags & nodes.Ability.RUNNING)
        self.assertFalse(bot.attack_input)
        return run, player, bot, state

    def test_trooper_heavy_and_sniper_hold_fire_until_line_of_sight_opens(self):
        from test_ashe_impulses import wall_scene

        for name in ("Trooper", "Heavy Assault", "Sniper"):
            with self.subTest(npc=name):
                definition = next(item for item in self.catalog.values() if item.name == name)
                run, player, bot = self.spawn(definition, 8)
                fight = run.match.combat
                player.mover.yaw, player.mover.pitch = fixtures.aim(fight._eye(player),
                                                                  (0, bot.position[1] + 1, 8))
                target = next(item for item in fight._targets() if item.owner is player)
                vitals = fight.vitals_of(target)
                primary = npc_control.button(bot)
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                open_world = run.match.collision_world
                run.match.collision_world = wall_scene(4)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    for _ in range(200):
                        run.step({})
                    self.assertTrue(bot.firing)
                    self.assertEqual(bot.attack_target, player.entity)
                    self.assertFalse(bot.attack_input)
                    self.assertNotIn(primary, bot.body_script.component.held)
                    shots.assert_not_called()
                    self.assertEqual(vitals.total, 200)

                    run.match.collision_world = open_world
                    for _ in range(200):
                        run.step({})
                        if vitals.total < 200:
                            break
                    self.assertTrue(bot.attack_input)
                    self.assertTrue(shots.called)
                    self.assertTrue(all(call.args[0] is bot for call in shots.call_args_list))
                    self.assertLess(vitals.total, 200)
                self.assertIsNone(bot.body_script.component.fault)

    def test_manual_attack_rejects_allied_and_out_of_range_npcs(self):
        for definition in self.catalog.values():
            for ally, distance in ((True, 8), (False, npc_control.AIM_RANGE + 2)):
                with self.subTest(npc=definition.name, ally=ally, distance=distance):
                    run, player, bot = self.spawn(definition, distance, ally=ally)
                    fight = run.match.combat
                    player.mover.yaw, player.mover.pitch = fixtures.aim(
                        fight._eye(player), (0, bot.position[1] + 1, distance))
                    target = next(item for item in fight._targets() if item.owner is player)
                    vitals = fight.vitals_of(target)
                    with self.assertRaisesRegex(ValueError, "No living manual enemy"):
                        npc_control.start(run.match, player)
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                        for _ in range(30):
                            run.step({})
                        shots.assert_not_called()
                    self.assertIsNone(bot.attack_target)
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(vitals.total, 200)

    def test_authored_sniper_sparse_weapon_slots_hit_ashe_and_stop(self):
        definition = next(item for item in self.catalog.values() if item.name == "Sniper")
        run, player, bot = self.spawn(definition, 8)
        player.mover.yaw, player.mover.pitch = fixtures.aim(run.match.combat._eye(player),
                                                          (0, bot.position[1] + 1, 8))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        target = next(item for item in run.match.combat._targets() if item.owner is player)
        vitals = run.match.combat.vitals_of(target)
        for _ in range(200):
            run.step({})
            if vitals.total < 200:
                break
        self.assertEqual(vitals.total, 100)
        self.assertIsNotNone(bot.body_script)
        self.assertIsNone(bot.body_script.component.fault)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(80):
            run.step({})
        self.assertEqual(vitals.total, 100)
        self.assertFalse(bot.attack_input)
        self.assertNotIn(20, bot.body_script.component.held)

    def test_melee_visibility_and_static_wall_against_all_five_actual_npcs(self):
        from test_ashe_impulses import wall_scene

        for definition in self.catalog.values():
            for wall in (False, True):
                with self.subTest(npc=definition.name, wall=wall):
                    run, player, bot = self.spawn(definition, 2)
                    if wall:
                        run.match.collision_world = wall_scene(1)
                    vitals = run.match.combat.vitals[bot.entity]
                    initial = vitals.total
                    _yaw, pitch = fixtures.aim(run.match.combat._eye(player),
                                               (0, bot.position[1] + 1, 2))
                    self.press(run, player, 0x800, frames=40, pitch=pitch)
                    self.assertEqual(initial - vitals.total, 0 if wall else 30)
                    hits = player.client.messages(combat.HIT)
                    self.assertEqual(len(hits), 0 if wall else 1)

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", ".")).is_file(),
                         "requires extracted NPC mover extension")
    def test_authored_capsules_control_viper_upper_side_edges_and_wall_occlusion(self):
        from test_ashe_impulses import wall_scene

        expected = {"Trooper": (1.7, 0.5, 5.75), "Enforcer": (1.9, 0.5, 5.0),
                    "Sniper": (1.75, 0.5, 4.5), "Assassin": (1.5, 0.5, 14.0),
                    "Heavy Assault": (2.3, 0.7, 2.2)}
        for definition in self.catalog.values():
            for case in ("upper_hit", "above", "side_hit", "outside", "wall"):
                with self.subTest(npc=definition.name, case=case):
                    radius = expected[definition.name][1]
                    offset = radius + (0.15 if case == "outside" else -0.15)
                    run, player, bot = self.spawn(definition, 8,
                                                   x=-offset if case in ("side_hit", "outside") else 0)
                    data = mover.mover_data(bot.body)
                    # Missing rows silently select Soldier defaults in mover_data.
                    # This test must prove exact loaded ownership before firing.
                    self.assertIs(data, mover.loaded_mover_table()[bot.body])
                    self.assertEqual((data.stand_height, data.stand_radius, data.run_forward),
                                     expected[definition.name])
                    target = next(item for item in run.match.combat._targets() if item.owner is bot)
                    self.assertEqual((target.height, target.radius), (data.stand_height, data.stand_radius))
                    if case in ("side_hit", "outside"):
                        height = data.stand_height / 2
                    else:
                        height = data.stand_height + (0.2 if case == "above" else -0.3)
                    if case == "wall":
                        run.match.collision_world = wall_scene(4)
                    point = (player.mover.position[0], bot.position[1] + height, bot.position[2])
                    _yaw, pitch = fixtures.aim(run.match.combat._eye(player), point)
                    weapon = next(item for item in player.body_script.component.instances.values()
                                  if item.graph.index == 0x14FC)
                    ammo = weapon.vars[53].value()
                    vitals = run.match.combat.vitals[bot.entity]
                    before = vitals.total
                    self.press(run, player, 1, pitch=pitch)
                    self.assertEqual(weapon.vars[53].value(), ammo - 1)
                    if case in ("upper_hit", "side_hit"):
                        self.assertLess(vitals.total, before)
                        self.assertEqual(len(player.client.messages(combat.HIT)), 1)
                    else:
                        self.assertEqual(vitals.total, before)
                        self.assertEqual(player.client.messages(combat.HIT), [])


if __name__ == "__main__":
    unittest.main()
