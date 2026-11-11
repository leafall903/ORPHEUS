"""Actual Sigma and Genji input over the prepared 104319 body graphs."""

import gzip
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_sigma_combat as fixtures
from test_game_combat import KANEZAKA_DM, Run, aim
from test_sigma_flux_placement import SigmaFluxPlacementTests

from ow174.game import combat, genji, heavy_assault, npc, npc_control, sigma
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph, nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

GENJI = 0x02E0000000000029


@unittest.skipUnless(os.environ.get("ORPHEUS_GENJI_GRAPHS"), "requires the external Genji graph extract")
class SigmaDeflectTests(unittest.TestCase):
    def setUp(self):
        data = graph._data()
        extract = json.loads(gzip.decompress(Path(os.environ["ORPHEUS_GENJI_GRAPHS"]).read_bytes()))
        key = "0x0400000000000225"
        body = extract["bodies"].get(key) or extract["entities"][key]
        body = {**body, "hero": f"0x{GENJI:016X}", "name": "Genji"}
        existing = {record["index"] for record in data["graphs"]}
        additional = [record for record in extract["graphs"] if record["index"] not in existing]
        overlay = {
            **data, "bodies": {**data["bodies"], key: body},
            "graphs": data["graphs"] + additional,
        }
        self.addCleanup(self.clear_graphs)
        self.enterContext(patch.object(graph, "_data", return_value=overlay))
        self.clear_graphs()

    @staticmethod
    def clear_graphs():
        graph.graph.cache_clear()
        graph._records.cache_clear()

    def arena(self, *, front=True, distance=6, deflect=True):
        run, shooter, genji, _ = fixtures.SigmaCombatTests().arena(offset=0, distance=distance)
        position = list(genji.mover.position)
        run.match.switch_hero(genji, heroes()[GENJI])
        genji.mover.position = position
        fight = run.match.combat
        targets = {target.owner: fight.vitals_of(target) for target in fight._targets()}
        yaw, pitch = aim(fight._eye(shooter), fight._eye(genji))
        reply = aim(fight._eye(genji), fight._eye(shooter)) if front else (0, 0)
        if deflect:
            run.step({genji: (*reply, 0x10)})
            effect = next(i for i in genji.body_script.component.instances.values()
                          if i.graph.index == 0x03A3)
            self.assertTrue(effect.states[10].active)
        return run, shooter, genji, targets, (yaw, pitch), reply

    def test_genji_shuriken_primary_and_fan_damage_and_range(self):
        for button, distance, expected in ((1, 6, 87), (2, 2.15, 87), (2, 6, 29)):
            with self.subTest(button=button, distance=distance):
                run, owner, genji, health, _, reply = self.arena(distance=distance, deflect=False)
                for frame in range(30):
                    run.step({genji: (*reply, button if frame == 0 else 0)})
                self.assertEqual(400 - health[owner].total, expected)

    def test_sigma_quick_melee_obeys_live_directional_deflect(self):
        for mode in ("front", "rear", "expired"):
            with self.subTest(mode=mode):
                run, owner, defender, health, facing, reply = self.arena(front=mode != "rear", distance=2.15)
                guard = next(i for i in defender.body_script.component.instances.values()
                             if i.graph.index == genji.DEFLECT)
                if mode == "expired":
                    for _ in range(145):
                        run.step({defender: (*reply, 0)})
                    self.assertFalse(guard.states[10].active)
                start = health[defender].total
                run.step({owner: (*facing, 0x800), defender: (*reply, 0)})
                self.assertEqual(start - health[defender].total, 0 if mode == "front" else 30)
                self.assertEqual(health[owner].total, 400)
                self.assertFalse(run.match.combat.rockets)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG"), "requires the external NPC catalog")
    def test_npc_melee_deflect_applies_only_to_the_protected_body(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"]))
        for hero, amount in ((heavy_assault.TROOPER, 30), (heavy_assault.HERO, 80)):
            definition = next(item for item in catalog.values() if item.hero == hero)
            for front in (True, False):
                with self.subTest(npc=definition.name, front=front):
                    run = Run(KANEZAKA_DM, ("Sigma", "Genji"))
                    match = run.match
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    match.game_map = replace(match.game_map, free_for_all=False)
                    owner, defender = run.players
                    defender.team = owner.team
                    match.switch_hero(owner, heroes()[sigma.HERO])
                    match.switch_hero(defender, heroes()[GENJI])
                    x, y, z = owner.mover.position
                    defender.mover.position[:] = (x + .95, y, z + .55)
                    defender.mover.body = None
                    bot = npc.spawn(match, definition, (x, y, z + 2.15), team=1 - owner.team, yaw=-32768)
                    fight = match.combat
                    facing = aim(fight._eye(owner), (x, y + 1.25, z + 2.15))
                    reply = aim(fight._eye(defender), (x, y + 1.25, z + 2.15))
                    if not front:
                        reply = (reply[0] % 65536 - 32768, 0)
                    for _ in range(12):
                        run.step({})
                    run.step({owner: (*facing, 0)})
                    npc_control.start(match, owner)
                    attack = heavy_assault.ability(bot.body_script.component)
                    health = {t.owner: fight.vitals_of(t) for t in fight._targets()}
                    for _ in range(25):
                        run.step({})
                        if attack.flags & nodes.Ability.RUNNING:
                            break
                    self.assertTrue(attack.flags & nodes.Ability.RUNNING)
                    run.step({defender: (*reply, 0x10)})
                    guard = next(i for i in defender.body_script.component.instances.values()
                                 if i.graph.index == genji.DEFLECT)
                    modifier = guard.states[guard.graph.node(107).state]
                    self.assertTrue(modifier.active)
                    with patch.object(heavy_assault, "_push", wraps=heavy_assault._push) as push:
                        for _ in range(30):
                            run.step({defender: (*reply, 0)})
                            if health[owner].total < 400:
                                break
                    self.assertTrue(modifier.active)
                    self.assertEqual(health[owner].total, 400 - amount)
                    self.assertEqual(health[defender].maximum - health[defender].total, 0 if front else amount)
                    pushed = [call.args[1].owner for call in push.call_args_list]
                    self.assertCountEqual(pushed, [owner] if front else [owner, defender])
                    self.assertEqual(health[bot].total, health[bot].maximum)
                    self.assertFalse(fight.rockets)

    def test_genji_primary_and_fan_feed_grasp_conversion(self):
        for button in (1, 2):
            with self.subTest(button=button):
                run, owner, genji, health, forward, reply = self.arena(deflect=False)
                run.step({owner: (*forward, 0x08)})
                for frame in range(140):
                    run.step({owner: (*forward, 0), genji: (*reply, button if frame == 0 else 0)})
                grasp = next(i for i in owner.body_script.component.instances.values()
                             if i.graph.index == sigma.GRASP)
                self.assertEqual(grasp.vars[21312].value(), 87)
                self.assertAlmostEqual(health[owner].temporary_shields[sigma.SHIELD_TAG].now, 52.2, places=4)
                self.assertEqual(sum(health[owner].now.values()), 400)

    def test_genji_primary_and_fan_damage_experimental_barrier(self):
        for button in (1, 2):
            with self.subTest(button=button):
                run, owner, genji, health, forward, reply = self.arena(deflect=False)
                fight = run.match.combat
                for frame in range(40):
                    run.step({owner: (forward[0], 0, 0x02 if frame < 4 else 0)})
                barrier, = fight.barriers.values()
                self.assertTrue(barrier.active)
                for frame in range(30):
                    run.step({genji: (*reply, button if frame == 0 else 0)})
                self.assertEqual(barrier.vitals.total, 613)
                self.assertEqual(health[owner].total, 400)

    def test_static_cover_blocks_both_genji_shuriken_patterns(self):
        for button in (1, 2):
            with self.subTest(button=button):
                run, owner, genji, health, _, reply = self.arena(deflect=False)
                fixtures.SigmaCombatTests().wall(run, sideways=False, offset=3)
                for frame in range(30):
                    run.step({genji: (*reply, button if frame == 0 else 0)})
                self.assertEqual(health[owner].total, 400)

    def test_swift_strike_hits_sigma_through_barrier_and_grasp(self):
        for defense in ("open", "barrier", "grasp"):
            with self.subTest(defense=defense):
                run, owner, genji, health, forward, reply = self.arena(deflect=False)
                fight = run.match.combat
                if defense == "barrier":
                    for frame in range(40):
                        run.step({owner: (forward[0], 0, 0x02 if frame < 4 else 0)})
                    barrier, = fight.barriers.values()
                    target = next(target for target in fight._targets() if target.owner is owner)
                    self.assertFalse(fight._blast_passes_barriers(genji, fight._eye(genji), target, None))
                elif defense == "grasp":
                    run.step({owner: (*forward, 0x08)})
                    grasp = next(i for i in owner.body_script.component.instances.values()
                                 if i.graph.index == sigma.GRASP)
                    self.assertTrue(grasp.states[1].active)
                start = genji.mover.position[2]
                for frame in range(45):
                    run.step({genji: (reply[0], 0, 8 if frame in (0, 32) else 0)})
                self.assertEqual(start - genji.mover.position[2], 15)
                self.assertEqual(health[owner].total, 350)
                self.assertNotIn(genji.body, fight.genji_strikes)
                if defense == "barrier":
                    self.assertTrue(barrier.active)
                    self.assertEqual(barrier.vitals.total, 700)
                elif defense == "grasp":
                    self.assertEqual(grasp.vars[21312].value(), 0)

    def test_swift_strike_wall_and_ally_do_not_take_damage(self):
        for mode in ("wall", "ally"):
            with self.subTest(mode=mode):
                run, owner, genji, health, _, reply = self.arena(deflect=False)
                if mode == "wall":
                    fixtures.SigmaCombatTests().wall(run, sideways=False, offset=3)
                else:
                    from dataclasses import replace

                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    genji.team = owner.team
                start = genji.mover.position[2]
                for frame in range(40):
                    run.step({genji: (reply[0], 0, 8 if frame == 0 else 0)})
                displacement = start - genji.mover.position[2]
                if mode == "wall":
                    self.assertGreater(displacement, 0)
                    self.assertLess(displacement, 3)
                else:
                    self.assertEqual(displacement, 15)
                self.assertEqual(health[owner].total, 400)

    def test_lethal_swift_strike_resets_cooldown_for_immediate_second_dash(self):
        run, owner, genji, health, _, reply = self.arena(deflect=False)
        health[owner].now[combat.HEALTH_POOL] = 45
        health[owner].now[combat.SHIELDS_POOL] = 0
        start = genji.mover.position[2]
        for frame in range(40):
            run.step({genji: (reply[0], 0, 8 if frame == 0 else 0)})
        self.assertTrue(run.match.combat.is_dead(owner))
        self.assertEqual(health[owner].killer, genji.body)
        self.assertEqual(start - genji.mover.position[2], 15)
        strike = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x01DB)
        self.assertEqual(strike.state(0).var.value(), 0)
        for frame in range(45):
            run.step({genji: (reply[0], 0, 8 if frame in (0, 36) else 0)})
        self.assertEqual(start - genji.mover.position[2], 30)
        self.assertEqual(strike.state(1).counter, 2)
        self.assertGreater(strike.state(0).var.value(), 7)

    def test_accretion_cancels_swift_strike_before_contact(self):
        run, owner, genji, health, _, reply = self.arena(deflect=False, distance=12)
        fight = run.match.combat
        x, y, start = genji.mover.position
        forward = aim(fight._eye(owner), (x, y + 1.2, start))
        run.step({owner: (*forward, 0x10), genji: (reply[0], 0, 0)})
        for _ in range(42):
            run.step({owner: (*forward, 0), genji: (reply[0], 0, 0)})
        strike = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x01DB)
        for frame in range(30):
            run.step({owner: (*forward, 0), genji: (reply[0], 0, 8 if frame == 0 else 0)})
            if fight.is_stunned(genji):
                break
        self.assertTrue(fight.is_stunned(genji))
        self.assertEqual(health[genji].total, 130)
        self.assertGreater(start - genji.mover.position[2], 0)
        self.assertLess(start - genji.mover.position[2], 12)
        self.assertFalse(strike.state(1).active)
        self.assertNotIn(genji.body, fight.genji_strikes)
        for _ in range(140):
            run.step({})
        self.assertFalse(strike.state(1).active)
        self.assertEqual(strike.state(1).counter, 1)
        self.assertEqual(health[owner].total, 400)

    def test_dragonblade_swing_damage_barrier_grasp_and_static_cover(self):
        for mode in ("open", "barrier", "grasp", "wall", "rear", "range"):
            with self.subTest(mode=mode):
                run, owner, genji, health, forward, reply = self.arena(
                    deflect=False, distance=7 if mode == "range" else 3,
                )
                fight = run.match.combat
                if mode == "barrier":
                    for frame in range(12):
                        run.step({owner: (forward[0], 0, 0x02 if frame < 5 else 0)})
                    barrier, = fight.barriers.values()
                    target = next(target for target in fight._targets() if target.owner is owner)
                    self.assertFalse(fight._blast_passes_barriers(genji, fight._eye(genji), target, None))
                elif mode == "wall":
                    fixtures.SigmaCombatTests().wall(run, sideways=False, offset=1.5)
                elif mode == "rear":
                    reply = forward
                script = genji.body_script
                cost = script.component.entity_var(ULT_COST).value()
                script.set_ult_charge(cost)
                for frame in range(112):
                    run.step({owner: (*forward, 0x08 if frame == 80 and mode == "grasp" else 0),
                              genji: (*reply, 0x20 if frame == 0 else 1 if frame == 81 else 0)})
                self.assertTrue(script.ultimates[0].flags & 1)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(health[owner].total, 400 if mode in ("wall", "rear", "range") else 290)
                if mode == "barrier":
                    self.assertEqual(barrier.vitals.total, 700)
                if mode == "grasp":
                    grasp = next(i for i in owner.body_script.component.instances.values()
                                 if i.graph.index == sigma.GRASP)
                    self.assertTrue(grasp.states[1].active)
                    self.assertEqual(grasp.vars[21312].value(), 0)

    def test_accretion_suppresses_dragonblade_swing_then_allows_fresh_swing(self):
        run, owner, genji, health, _, reply = self.arena(deflect=False, distance=2.2)
        fight = run.match.combat
        script = genji.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for frame in range(75):
            run.step({genji: (*reply, 0x20 if frame == 0 else 0)})
        x, y, z = genji.mover.position
        forward = aim(fight._eye(owner), (x, y + 1.2, z))
        run.step({owner: (*forward, 0x10), genji: (*reply, 0)})
        for _ in range(60):
            run.step({owner: (*forward, 0), genji: (*reply, 0)})
            if fight.is_stunned(genji):
                break
        self.assertTrue(fight.is_stunned(genji))
        self.assertEqual(health[genji].total, 130)
        after_rock = health[owner].total
        self.assertLess(after_rock, 400)
        while fight.is_stunned(genji):
            run.step({genji: (*reply, 1)})
            self.assertEqual(health[owner].total, after_rock)
        self.assertTrue(script.ultimates[0].flags & 1)
        run.step({genji: (*reply, 0)})
        for frame in range(40):
            run.step({genji: (*reply, 1 if frame == 0 else 0)})
        self.assertEqual(health[owner].total, after_rock - 110)

    def test_dragonblade_charge_gate_and_natural_expiry_restore_shuriken(self):
        run, _, genji, _, _, reply = self.arena(deflect=False, distance=3)
        script = genji.body_script
        cost = script.component.entity_var(ULT_COST).value()
        ultimate, = script.ultimates
        script.set_ult_charge(cost - 1)
        run.step({genji: (*reply, 0x20)})
        self.assertFalse(ultimate.flags & 1)
        unspent = script.component.entity_var(ULT_CHARGE).value()
        self.assertGreaterEqual(unspent, cost - 1)
        self.assertLess(unspent, cost)
        run.step({genji: (*reply, 0)})
        script.set_ult_charge(cost)
        run.step({genji: (*reply, 0x20)})
        self.assertTrue(ultimate.flags & 1)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        for frame in range(530):
            run.step({genji: (*reply, 1 if frame == 80 else 0)})
            if not ultimate.flags & 1:
                break
        self.assertFalse(ultimate.flags & 1)
        self.assertGreaterEqual(frame, 490)
        self.assertLessEqual(frame, 510)
        self.assertNotIn(genji.body, run.match.combat.genji_blades)
        hits_before = len(genji.client.messages(combat.HIT))
        for frame in range(55):
            run.step({genji: (*reply, 1 if frame == 20 else 0)})
        hits = genji.client.messages(combat.HIT)[hits_before:]
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29, 29, 29])

    def dragonblade_flux(self):
        run, owner, genji, health, _, reply = self.arena(deflect=False, distance=2.2)
        for player in (genji, owner):
            script = player.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({genji: (*reply, 0x20)})
        drawn_at = run.frame
        SigmaFluxPlacementTests().select(run, owner, tuple(genji.mover.position))
        return run, owner, genji, health, drawn_at

    def test_dragonblade_hits_while_lifted_and_flux_keeps_its_slam(self):
        run, owner, genji, health, drawn_at = self.dragonblade_flux()
        fight = run.match.combat
        ultimate, = genji.body_script.ultimates
        hit_while_lifted = False
        for frame in range(440):
            x, y, z = owner.mover.position
            reply = aim(fight._eye(genji), (x, y + 1, z))
            before = health[owner].total
            run.step({genji: (*reply, 1 if frame == 52 else 0)})
            if before - health[owner].total == 110:
                hit_while_lifted = genji.mover.airborne
                self.assertTrue(ultimate.flags & 1)
            if not ultimate.flags & 1:
                break
        self.assertTrue(hit_while_lifted)
        self.assertEqual(health[genji].total, 50)
        self.assertFalse(genji.mover.airborne)
        self.assertFalse(ultimate.flags & 1)
        self.assertIn(run.frame - drawn_at, range(490, 511))
        self.assertEqual(len(genji.client.messages(combat.HIT)), 1)

    def test_lethal_dragonblade_cancels_flux_only_before_descent(self):
        for swing_frame, early, expected in ((52, True, 150), (186, False, 50)):
            with self.subTest(early=early):
                run, owner, genji, health, _ = self.dragonblade_flux()
                fight = run.match.combat
                health[owner].now[combat.HEALTH_POOL] = 90
                health[owner].now[combat.SHIELDS_POOL] = 0
                component = genji.body_script.component
                observed = False
                for frame in range(270):
                    effect = next((i for i in component.instances.values()
                                   if i.graph.index == sigma.FLUX_EFFECT), None)
                    airborne = genji.mover.airborne
                    pre_descent = bool(effect and effect.states.get(16) and effect.states[16].active)
                    x, y, z = owner.mover.position
                    reply = aim(fight._eye(genji), (x, y + 1, z))
                    run.step({genji: (*reply, 1 if frame == swing_frame else 0)})
                    if not observed and fight.is_dead(owner):
                        self.assertEqual(health[owner].killer, genji.body)
                        self.assertTrue(airborne)
                        self.assertIsNotNone(effect)
                        self.assertEqual(pre_descent, early)
                        self.assertEqual(effect.id in component.instances, not early)
                        observed = True
                self.assertTrue(observed)
                self.assertFalse(genji.mover.airborne)
                self.assertEqual(health[genji].total, expected)

    def test_flux_denies_new_swift_strike_and_ends_an_existing_dash(self):
        for already_moving in (False, True):
            with self.subTest(already_moving=already_moving):
                run, owner, genji, health, _, reply = self.arena(deflect=False)
                fight = run.match.combat
                owner.body_script.set_ult_charge(owner.body_script.component.entity_var(ULT_COST).value())
                SigmaFluxPlacementTests().select(run, owner, tuple(genji.mover.position))
                component = genji.body_script.component
                strike = next(i for i in component.instances.values() if i.graph.index == 0x01DB)
                start = genji.mover.position[2]
                caught = None
                saw_dash = False
                for frame in range(270):
                    pressed = frame in ((56, 80, 190) if already_moving else (80, 160, 190))
                    if pressed and frame != 56:
                        self.assertTrue(component.entity_var(5995).value())
                    run.step({genji: (reply[0], 0, 8 if pressed else 0)})
                    saw_dash |= strike.state(1).active
                    if component.entity_var(5995).value() and caught is None:
                        caught = genji.mover.position[2]
                        self.assertFalse(strike.state(1).active)
                        self.assertNotIn(genji.body, fight.genji_strikes)
                    if caught is not None:
                        self.assertEqual(genji.mover.position[2], caught)
                self.assertIsNotNone(caught)
                self.assertEqual(saw_dash, already_moving)
                self.assertEqual(strike.state(1).counter, int(already_moving))
                self.assertEqual(health[genji].total, 50)
                self.assertEqual(health[owner].total, 400)
                self.assertFalse(genji.mover.airborne)
                self.assertFalse(component.entity_var(5995).value())
                if already_moving:
                    self.assertGreater(start - caught, 3)
                    self.assertLess(start - caught, 5)
                else:
                    self.assertEqual(caught, start)
                run.step({genji: (reply[0], 0, 8)})
                self.assertEqual(strike.state(1).active, not already_moving)

    def test_flux_entry_and_slam_pass_deflect(self):
        for press, expected_active in ((50, (True, False)), (70, (False, False)), (190, (False, True))):
            with self.subTest(press=press):
                run, owner, genji, health, _, _ = self.arena(deflect=False)
                fight = run.match.combat
                script = owner.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                SigmaFluxPlacementTests().select(run, owner, tuple(genji.mover.position))
                component = genji.body_script.component
                deflect = next(i for i in component.instances.values() if i.graph.index == 0x03A3)
                losses = []
                activated_airborne = False
                saw_lift = False
                for frame in range(270):
                    x, y, z = owner.mover.position
                    reply = aim(fight._eye(genji), (x, y + 1, z))
                    state = deflect.states.get(10)
                    was_active = state is not None and state.active
                    was_airborne = genji.mover.airborne
                    before = health[genji].total
                    run.step({genji: (*reply, 0x10 if frame == press else 0)})
                    active = deflect.states.get(10) is not None and deflect.states[10].active
                    if frame == press:
                        self.assertTrue(active)
                        activated_airborne = was_airborne
                    if health[genji].total < before:
                        losses.append((before - health[genji].total, was_active))
                        self.assertEqual(active, was_active)
                    saw_lift |= genji.mover.airborne
                self.assertEqual(losses, [(50, expected_active[0]), (100, expected_active[1])])
                self.assertEqual(activated_airborne, press != 50)
                self.assertTrue(saw_lift)
                self.assertFalse(genji.mover.airborne)
                self.assertFalse(
                    any(i.graph.index == sigma.FLUX_EFFECT for i in component.instances.values()),
                )
                self.assertEqual(health[owner].total, 400)
                self.assertFalse(genji.client.messages(combat.HIT))

    def test_lifted_genji_can_hit_with_both_shuriken_patterns(self):
        for button in (1, 2):
            with self.subTest(button=button):
                run, owner, genji, health, _, _ = self.arena(deflect=False, distance=2.2)
                fight = run.match.combat
                script = owner.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                SigmaFluxPlacementTests().select(run, owner, tuple(genji.mover.position))
                hit_frames = []
                for frame in range(270):
                    x, y, z = owner.mover.position
                    reply = aim(fight._eye(genji), (x, y + 1, z))
                    before = health[owner].total
                    run.step({genji: (*reply, button if frame == 80 else 0)})
                    if health[owner].total < before:
                        self.assertTrue(genji.mover.airborne)
                        hit_frames.append(frame)
                self.assertEqual(len(hit_frames), 3 if button == 1 else 1)
                self.assertEqual(health[owner].total, 313)
                hits = genji.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29, 29, 29])
                self.assertEqual(health[genji].total, 50)
                self.assertFalse(genji.mover.airborne)

    def test_lethal_flux_slam_retires_dragonblade(self):
        run, owner, genji, health, _ = self.dragonblade_flux()
        fight = run.match.combat
        health[genji].now[combat.HEALTH_POOL] = 125
        script = genji.body_script
        ultimate, = script.ultimates
        for _ in range(260):
            run.step({})
            if fight.is_dead(genji):
                break
            self.assertTrue(ultimate.flags & 1)
        self.assertTrue(fight.is_dead(genji))
        self.assertEqual(health[genji].killer, owner.body)
        self.assertFalse(ultimate.flags & 1)
        self.assertTrue(script.component.owner_dead)
        for _ in range(30):
            run.step({genji: (0, 0, 1)})
        self.assertFalse(ultimate.flags & 1)
        self.assertEqual(health[owner].total, 400)

    def test_front_deflect_returns_both_hyperspheres_with_genji_hit_credit(self):
        run, shooter, genji, health, forward, reply = self.arena()
        for actor in (shooter, genji):
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        with patch.object(
            shooter.mover, "add_external_impulse", wraps=shooter.mover.add_external_impulse,
        ) as impact:
            for frame in range(90):
                run.step({shooter: (*forward, 1 if frame == 0 else 0), genji: (*reply, 0)})
        self.assertEqual(impact.call_count, 2)
        self.assertEqual(health[shooter].total, 290)
        self.assertEqual(health[genji].total, 200)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 4)
        self.assertFalse(shooter.client.messages(combat.HIT))
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), 110)
        self.assertEqual(shooter.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_deflected_damage_cannot_charge_the_next_dragonblade_until_expiry(self):
        run, shooter, genji, health, forward, reply = self.arena(distance=8)
        script = genji.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        ultimate, = script.ultimates
        fight = run.match.combat
        for frame in range(40):
            run.step({shooter: (*forward, 1 if frame == 0 else 0), genji: (*reply, 0)})
            if any(rocket.shooter is genji for rocket in fight.rockets):
                break
        self.assertTrue(any(rocket.shooter is genji for rocket in fight.rockets))
        self.assertEqual(health[shooter].total, 400)
        run.step({genji: (*reply, 0x20)})
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        for _ in range(90):
            run.step({shooter: (*forward, 0), genji: (*reply, 0)})
            self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
            self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(health[shooter].total, 345)
        for _ in range(550):
            run.step({})
            self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        run.step({genji: (*reply, 0x10)})
        before = health[shooter].total
        genji_before = health[genji].total
        for frame in range(90):
            run.step({shooter: (*forward, 1 if frame == 0 else 0), genji: (*reply, 0)})
        self.assertEqual(before - health[shooter].total, 110)
        self.assertEqual(health[genji].total, genji_before)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 110)

    def test_rear_hyperspheres_hit_genji_during_deflect(self):
        run, shooter, genji, health, forward, reply = self.arena(front=False)
        for frame in range(90):
            run.step({shooter: (*forward, 1 if frame == 0 else 0), genji: (*reply, 0)})
        self.assertEqual(health[shooter].total, 400)
        self.assertEqual(health[genji].total, 90)

    def test_reflected_lethal_hit_credits_the_current_genji_body(self):
        run, shooter, genji, health, forward, reply = self.arena()
        genji.body_script.ult_seconds = 0
        genji.body_script.set_ult_charge(0)
        health[shooter].now[combat.HEALTH_POOL] = 35
        health[shooter].now[combat.SHIELDS_POOL] = 0
        for frame in range(90):
            run.step({shooter: (*forward, 1 if frame == 0 else 0), genji: (*reply, 0)})
        self.assertTrue(health[shooter].dead)
        self.assertEqual(health[shooter].killer, genji.body)
        self.assertEqual(health[genji].total, 200)
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), 35)

    def test_accretion_passes_front_deflect_and_interrupts_genji(self):
        run, shooter, genji, health, forward, reply = self.arena()
        for frame in range(90):
            run.step({shooter: (*forward, 0x10 if frame == 0 else 0), genji: (*reply, 0)})
            if health[genji].total < 200:
                break
        self.assertEqual(health[shooter].total, 400)
        self.assertEqual(health[genji].total, 130)
        self.assertTrue(run.match.combat.is_stunned(genji))
        effect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertFalse(effect.states[10].active)

    def test_wall_before_active_deflect_prevents_reflection_and_damage(self):
        run, shooter, genji, health, forward, reply = self.arena(distance=10)
        fixtures.SigmaCombatTests().wall(run, sideways=False, offset=5)
        for frame in range(90):
            run.step({shooter: (*forward, 1 if frame == 0 else 0), genji: (*reply, 0)})
            self.assertFalse(any(rocket.shooter is genji for rocket in run.match.combat.rockets))
        self.assertEqual(health[shooter].total, 400)
        self.assertEqual(health[genji].total, 200)
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_grasp_absorbs_returning_hyperspheres_using_reflected_hostility(self):
        run, shooter, genji, health, forward, reply = self.arena(distance=8)
        for actor in (shooter, genji):
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        saw_reflection = False
        for frame in range(85):
            buttons = 1 if frame == 0 else 0x08 if 13 <= frame < 19 else 0
            run.step({shooter: (*forward, buttons), genji: (*reply, 0)})
            saw_reflection |= any(rocket.shooter is genji for rocket in run.match.combat.rockets)
        grasp = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(saw_reflection)
        self.assertEqual(grasp.vars[21312].value(), 110)
        self.assertEqual(health[shooter].total, 400)
        self.assertEqual(health[genji].total, 200)
        for _ in range(75):
            run.step({})
        bonus = health[shooter].temporary_shields[sigma.SHIELD_TAG]
        self.assertAlmostEqual(bonus.now, 66)
        self.assertEqual(shooter.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires the external C015 model")
    def test_reflected_pair_breaks_barrier_then_feeds_grasp(self):
        run, shooter, genji, health, forward, reply = self.arena(distance=8, deflect=False)
        for actor in (shooter, genji):
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        fight = run.match.combat
        for frame in range(50):
            run.step({shooter: (0, 0, 2 if frame < 16 else 0)})
        barrier, = fight.barriers.values()
        self.assertTrue(barrier.active)
        barrier.vitals.damage(660)
        run.step({genji: (*reply, 0x10)})
        grasp = fixtures.SigmaCombatTests().ability(shooter, sigma.GRASP).instance
        reflected = {}
        saw_break = False
        for frame in range(80):
            buttons = 1 if frame == 0 else 0x08 if frame == 18 else 0
            run.step({shooter: (*forward, buttons), genji: (*reply, 0)})
            if frame == 18:
                self.assertTrue(grasp.states[1].active)
            reflected.update((id(rocket), rocket) for rocket in fight.rockets if rocket.shooter is genji)
            if not barrier.active and not saw_break:
                saw_break = True
                absorbed = grasp.vars.get(21312)
                self.assertEqual(absorbed.value() if absorbed is not None else 0, 0)
                self.assertEqual(barrier.vitals.total, 0)
            self.assertEqual(health[shooter].total, 400)
            self.assertEqual(health[genji].total, 200)
        self.assertEqual(len(reflected), 2, (barrier.vitals.total, grasp.vars[21312].value()))
        self.assertTrue(saw_break, (barrier.vitals.total, grasp.vars[21312].value()))
        self.assertEqual(grasp.vars[21312].value(), 55)
        for _ in range(75):
            run.step({})
        self.assertAlmostEqual(health[shooter].temporary_shields[sigma.SHIELD_TAG].now, 33)
        for actor in (shooter, genji):
            self.assertEqual(actor.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_genji_reflects_sphere_after_sigma_dies_and_earns_return_damage_charge(self):
        run = Run(KANEZAKA_DM, ("Sigma", "Genji", "Soldier"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        shooter, genji, soldier = run.players
        run.match.switch_hero(shooter, heroes()[sigma.HERO])
        run.match.switch_hero(genji, heroes()[GENJI])
        x, y, z = shooter.mover.position
        genji.mover.position = [x, y, z + 6]
        soldier.mover.position = [x, y, z - 3]
        fight = run.match.combat
        health = {target.owner: fight.vitals_of(target) for target in fight._targets()}
        health[shooter].now[combat.HEALTH_POOL] = 20
        health[shooter].now[combat.SHIELDS_POOL] = 0
        for actor in (shooter, genji):
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        forward = aim(fight._eye(shooter), fight._eye(genji))
        reply = aim(fight._eye(genji), fight._eye(soldier))
        lethal = aim(fight._eye(soldier), (x, y + 0.8, z))
        run.step({genji: (*reply, 0x10)})
        run.step({shooter: (*forward, 1), genji: (*reply, 0)})
        for _ in range(40):
            if fight.rockets:
                break
            run.step({shooter: (*forward, 0), genji: (*reply, 0)})
        sphere, = fight.rockets
        self.assertIs(sphere.shooter, shooter)
        run.step({genji: (*reply, 0), soldier: (*lethal, 1)})
        self.assertTrue(health[shooter].dead)
        self.assertEqual(health[shooter].killer, soldier.body)
        reflected = False
        for _ in range(60):
            run.step({genji: (*reply, 0)})
            self.assertTrue(all(rocket is sphere for rocket in fight.rockets))
            if sphere.shooter is genji:
                reflected = True
                self.assertEqual(sphere.owner_body, genji.body)
        self.assertTrue(reflected)
        self.assertFalse(fight.rockets)
        self.assertEqual(health[genji].total, 200)
        self.assertEqual(health[soldier].total, 145)
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), 55)
        self.assertEqual(shooter.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_reflected_hypersphere_hits_after_genji_death_without_awarding_charge(self):
        self.assert_reflected_hit_after_genji_death(lethal_return=False)

    def test_reflected_kill_after_genji_death_credits_his_original_body(self):
        self.assert_reflected_hit_after_genji_death(lethal_return=True)

    def assert_reflected_hit_after_genji_death(self, *, lethal_return):
        run = Run(KANEZAKA_DM, ("Sigma", "Genji", "Soldier"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        shooter, genji, soldier = run.players
        run.match.switch_hero(shooter, heroes()[sigma.HERO])
        run.match.switch_hero(genji, heroes()[GENJI])
        x, y, z = shooter.mover.position
        genji.mover.position = [x, y, z + 8]
        soldier.mover.position = [x + 5, y, z + 8]
        fight = run.match.combat
        health = {target.owner: fight.vitals_of(target) for target in fight._targets()}
        health[genji].now[combat.HEALTH_POOL] = 20
        if lethal_return:
            health[shooter].now[combat.HEALTH_POOL] = 55
            health[shooter].now[combat.SHIELDS_POOL] = 0
        for actor in (shooter, genji):
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        forward = aim(fight._eye(shooter), fight._eye(genji))
        reply = aim(fight._eye(genji), fight._eye(shooter))
        lethal = aim(fight._eye(soldier), (x, y + 0.8, z + 8))
        run.step({genji: (*reply, 0x10)})
        run.step({shooter: (*forward, 1), genji: (*reply, 0)})
        for _ in range(40):
            run.step({shooter: (*forward, 0), genji: (*reply, 0)})
            if any(rocket.shooter is genji for rocket in fight.rockets):
                break
        reflected, = (rocket for rocket in fight.rockets if rocket.shooter is genji)
        self.assertEqual(reflected.owner_body, genji.body)
        self.assertEqual(health[shooter].total, 55 if lethal_return else 400)
        run.step({genji: (*reply, 0), soldier: (*lethal, 1)})
        self.assertTrue(health[genji].dead)
        self.assertEqual(health[genji].killer, soldier.body)
        self.assertIn(reflected, fight.rockets)
        for _ in range(65):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(health[shooter].total, 0 if lethal_return else 345)
        if lethal_return:
            self.assertEqual(health[shooter].killer, genji.body)
            self.assertGreater(health[shooter].death_tick, health[genji].death_tick)
        self.assertEqual(health[soldier].total, 200)
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(shooter.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_reflected_flight_is_removed_on_deflector_body_replacement(self):
        run, shooter, genji, health, forward, reply = self.arena()
        fight = run.match.combat
        for frame in range(30):
            run.step({shooter: (*forward, 1 if frame == 0 else 0), genji: (*reply, 0)})
            reflected = next((r for r in fight.rockets if r.owner_body == genji.body), None)
            if reflected is not None:
                break
        self.assertIsNotNone(reflected)
        self.assertIs(reflected.shooter, genji)
        self.assertLess(reflected.left, 0.44)
        self.assertIsNone(reflected.shot)
        run.match.switch_hero(shooter, heroes()[SOLDIER])
        self.assertIn(reflected, fight.rockets)
        run.match.switch_hero(genji, heroes()[SOLDIER])
        self.assertNotIn(reflected, fight.rockets)
        self.assertEqual(health[shooter].total, 400)
