"""Actual selected0407 commands and Zen volleys; reconstructed finite-projectile Matrix host."""

import hashlib
import json
import os
import tempfile
import unittest
from itertools import product
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_dva_body_forms as forms
from test_game_combat import Run, aim, in_world

from ow174.game import combat, dva, matrix, npc, projectiles
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World, flat_world
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.world import OP_DESTROY
from tools.extract_projectile_tags import entity_tags

ORB, FROST = 0x0400000000000236, 0x04000000000008BE
CHARGED_ORB = 0x0400000000000261


class MatrixInputTests(unittest.TestCase):
    def test_bad_catalog_and_unknown_definition_cannot_be_assumed_absorbable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'tags.json'
            for data in ({'version': 1, 'build': 'other', 'entities': {}},
                         {'version': 1, 'build': matrix.BUILD, 'entities': {'0236': []}},
                         {'version': 1, 'build': matrix.BUILD,
                          'entities': {'0x0400000000000236': ['0x0400000000000145']}}):
                path.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaises(ValueError):
                    matrix.read_tags(path)
        with patch.object(matrix, 'tags', return_value={ORB: frozenset(),
                                                       FROST: frozenset({matrix.EXCLUDED_TAG})}):
            self.assertTrue(matrix.absorbable(ORB))
            self.assertFalse(matrix.absorbable(FROST))
            self.assertFalse(matrix.absorbable(None))
            self.assertFalse(matrix.absorbable(0x040000000000FFFF))

    @unittest.skipUnless(os.environ.get('ORPHEUS_MATRIX_INPUTS'), 'requires manifest-verified matrix inputs')
    def test_exported_projectile_tags_match_verified_xml_and_profile_type_is097(self):
        root = Path(os.environ['ORPHEUS_MATRIX_INPUTS'])
        proof = json.loads((root.parent/'zen-matrix-input-provenance.json').read_text(encoding='utf-8'))
        for row in proof:
            data = (root/row['guid'].split('.')[1]/f"{row['guid']}.xml").read_bytes()
            self.assertTrue(row['verified'])
            self.assertEqual(row['build'], 104319)
            self.assertEqual(hashlib.sha256(data).hexdigest(), row['xml_sha256'])
        self.assertEqual(entity_tags(root/'003'/'000000000236.003.xml'), ['0x0D600000000002F9'])
        self.assertIn('0x0D60000000000145', entity_tags(root/'003'/'0000000008BE.003.xml'))
        self.assertTrue((root/'097'/'000000000002.097.xml').is_file())
        self.assertTrue(matrix.absorbable(ORB))
        self.assertFalse(matrix.absorbable(FROST))


@unittest.skipUnless(os.environ.get('ORPHEUS_DVA_RUNTIME') and os.environ.get('ORPHEUS_PROJECTILE_TAGS'),
                     'requires actual0407 metadata/mover and verified projectile tag catalog')
class ZenyattaMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        forms.DvaBodyFormTests.setUpClass()

    def arena(self, *, active=True, yaw=-32768, distance=8):
        run, mech, zen = forms.DvaBodyFormTests().arena()
        floor = mech.mover.position[1]
        mech.mover.position[:] = (0, floor, distance)
        zen.mover.position[:] = (0, floor, 0)
        collision = flat_world(floor)
        run.match.collision_world = collision
        for player in run.players:
            player.mover.use(world=collision)
        for _ in range(100):
            self.step(run, mech, zen, mb=0, yaw=yaw)
        if active:
            for _ in range(10):
                self.step(run, mech, zen, yaw=yaw)
            self.assertTrue(self.instance(mech).state(3).active)
        return run, mech, zen

    def instance(self, mech):
        return next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x2CC)

    def step(self, run, mech, zen, *, mb=SECONDARY, zb=0, yaw=-32768):
        run.step({mech: (yaw, 0, mb), zen: (0, 600, zb)})

    def health(self, run, actor):
        return run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is actor))

    def shoot(self, run, mech, zen, *, mb=SECONDARY, yaw=-32768):
        self.step(run, mech, zen, zb=FIRE, mb=mb, yaw=yaw)
        for _ in range(30):
            self.step(run, mech, zen, mb=mb, yaw=yaw)

    def test_matrix_intercepts_before_projected_bubble_on_mech(self):
        for active in (False, True):
            with self.subTest(matrix=active):
                run, mech, zen = self.arena(active=False)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                zen.team, mech.team = 0, 1
                hero = heroes()[ZARYA]
                zarya = match.add_player(3, "Zarya", hero.guid, 1, False)
                in_world(match, zarya)
                self.assertTrue(match.switch_hero(zarya, hero))
                zarya.team = mech.team
                run.players.append(zarya)
                x, y, z = mech.mover.position
                zarya.mover.position = [x+4, y, z]
                zarya.mover.body = None
                direction = aim(fight._eye(zarya), fight._eye(mech))
                guard = SECONDARY if active else 0
                for _ in range(10):
                    self.step(run, mech, zen, mb=guard)
                run.step({zarya: (*direction, 16), mech: (-32768, 0, guard)})
                bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                self.assertEqual((bubble.target, bubble.hp), (mech.body, 200))

                charge = zen.body_script.component.entity_var(342)
                before_charge, before_frame = charge.value(), run.frame
                self.shoot(run, mech, zen, mb=guard)
                self.assertEqual(bubble.hp, 200 if active else 152)
                energy_graph = next(i for i in zarya.body_script.component.instances.values()
                                    if i.graph.index == 0x0242)
                energy = energy_graph.find_var(1, 87, create=False)
                self.assertAlmostEqual((energy.value() if energy is not None else 0) or 0,
                                       0 if active else 9.6, places=4)
                self.assertEqual(self.health(run, mech).total, 600)
                self.assertFalse(zen.client.messages(combat.HIT))
                self.assertFalse(fight.rockets)
                self.assertAlmostEqual(charge.value()-before_charge,
                                       (run.frame-before_frame)*combat.TICK*5, places=4)

    def test_friendly_matrix_only_consumes_the_orb_after_enemy_deflect(self):
        genji_hero = 0x02E0000000000029
        for active in (False, True):
            with self.subTest(matrix=active):
                run, mech, zen = self.arena(active=False, yaw=0, distance=3)
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                zen.team = mech.team = 0
                genji = match.add_player(3, "Genji", genji_hero, 1, False)
                in_world(match, genji)
                self.assertTrue(match.switch_hero(genji, heroes()[genji_hero]))
                run.players.append(genji)
                floor = zen.mover.position[1]
                genji.mover.position = [0, floor, 13]
                genji.mover.body = None
                fight = match.combat
                guard = {
                    genji: (*aim(fight._eye(genji), (0, floor+1, 3)), 16),
                    mech: (0, 0, SECONDARY if active else 0),
                }
                for _ in range(20):
                    run.step(guard)
                self.assertEqual(self.instance(mech).state(3).active, active)
                direction = aim(fight._eye(zen), (0, floor+1, 13))
                run.step({**guard, zen: (*direction, FIRE)})
                orb, = fight.rockets
                for _ in range(35):
                    run.step(guard)
                    if orb not in fight.rockets:
                        break
                self.assertIs(orb.shooter, genji)
                self.assertNotIn(orb, fight.rockets)
                self.assertEqual(self.health(run, mech).total, 600 if active else 557)
                self.assertEqual(self.health(run, zen).total, 200)
                self.assertEqual(self.health(run, genji).total, 200)
                self.assertFalse(zen.client.messages(combat.HIT))
                self.assertEqual(len(genji.client.messages(combat.HIT)), 0 if active else 1)


    def test_actual_hitscan_rewinds_bodies_but_samples_live_matrix_pose(self):
        for guard, delayed in product(("inactive", "front", "rear"), (False, True)):
            with self.subTest(guard=guard, delayed=delayed):
                run, mech, zen = self.arena(active=False, distance=3)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                mech.team = zen.team = 0
                soldier = match.add_player(3, "Soldier", SOLDIER, 1, False)
                in_world(match, soldier)
                self.assertTrue(match.switch_hero(soldier, heroes()[SOLDIER]))
                soldier.team = 1
                run.players.append(soldier)
                floor = zen.mover.position[1]
                soldier.mover.position = [0, floor, 12]
                mech.mover.position = [6, floor, 3]
                soldier.mover.body = mech.mover.body = None
                for _ in range(20):
                    run.step({})
                old_tick = run.frame
                self.assertFalse(any(matrix.active_matrix_states(mech)))
                zen.mover.position = [6, floor, 0]
                mech.mover.position = [0, floor, 3]
                zen.mover.body = mech.mover.body = None
                yaw = 0 if guard == "front" else -32768
                buttons = 0 if guard == "inactive" else SECONDARY
                for _ in range(10):
                    run.step({mech: (yaw, 0, buttons)})
                self.assertEqual(any(matrix.active_matrix_states(mech)), guard != "inactive")
                current_tick = run.frame + 1
                match.update(current_tick * combat.TICK, current_tick, frames=False)
                for actor, historical_x, live_x in ((zen, 0, 6), (mech, 6, 0)):
                    target = next(t for t in fight._targets() if t.owner is actor)
                    self.assertAlmostEqual(fight.seen(target, old_tick).feet[0], historical_x)
                    self.assertAlmostEqual(target.feet[0], live_x)
                gun = next(i for i in soldier.body_script.component.instances.values() if i.graph.index == 0x0254)
                ammo = gun.vars[53].value()
                direction = aim(fight._eye(soldier), (0, floor + 1, 0))
                delay_ms = (current_tick - old_tick) * 16 if delayed else 0
                soldier.take_commands([Command(current_tick, yaw=direction[0], pitch=direction[1],
                                               buttons=FIRE, extra=delay_ms)])
                self.assertEqual(gun.vars[53].value(), ammo - 1)
                self.assertFalse(fight.rockets)
                hits = soldier.client.messages(combat.HIT)
                # With the old capsule off the line, even the rear box can be
                # nearer than Zen's historical body. A current shot meets armour first.
                absorbed = guard == "front" or (guard == "rear" and delayed)
                if absorbed:
                    self.assertFalse(hits)
                    self.assertEqual(self.health(run, zen).total, 200)
                    self.assertEqual(self.health(run, mech).total, 600)
                else:
                    hit, = hits
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], zen.body if delayed else mech.body)
                    self.assertEqual(hit["+0x78"]["+0x4"], 20 if delayed else 15)
                    self.assertEqual(self.health(run, zen).total, 180 if delayed else 200)
                    self.assertEqual(self.health(run, mech).total, 600 if delayed else 585)

    def test_front_consumes_real_primary_without_damage_hit_or_damage_ultimate_charge(self):
        run, mech, zen = self.arena()
        charge = zen.body_script.component.entity_var(342).value()
        frame = run.frame
        self.shoot(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, 600)
        self.assertEqual(self.health(run, zen).total, 200)
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertFalse(mech.client.messages(combat.HIT))
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(run.match.combat.projectiles.shots)
        self.assertAlmostEqual(zen.body_script.component.entity_var(342).value()-charge,
                               (run.frame-frame)*.016*5, places=4)
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x164)
        self.assertEqual(weapon.vars[53].value(), 19)

    def test_inactive_matrix_query_does_not_materialize_a_vm_state(self):
        run, mech, zen = self.arena(active=False)
        instance = self.instance(mech)
        before = tuple(instance.states)
        self.assertNotIn(3, before)
        self.assertIsNone(run.match.combat._matrix_hit(
            zen, run.match.combat._eye(zen), (0, 0, 1), 30, ORB))
        self.assertEqual(tuple(instance.states), before)

    def test_actual_full_burst_consumes_five_rounds_without_hitting_active_matrix(self):
        run, mech, zen = self.arena(active=False)
        for _ in range(130):
            self.step(run, mech, zen, mb=0, zb=SECONDARY)
        for _ in range(60):
            self.step(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, 600)
        self.assertFalse(zen.client.messages(combat.HIT))
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x164)
        self.assertEqual(weapon.vars[53].value(), 15)
        self.assertFalse(run.match.combat.rockets)

    def test_released_critical_volley_samples_matrix_between_impacts(self):
        for initially_active in (False, True):
            with self.subTest(initially_active=initially_active):
                run, mech, zen = self.arena(active=False, distance=24)
                fight = run.match.combat
                vitals = self.health(run, mech)
                self.assertEqual(vitals.total, 600)
                angles = aim(fight._eye(zen), fight._eye(mech))
                matrix_instance = self.instance(mech)
                charge = zen.body_script.component.entity_var(342)
                weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x164)

                def step(*, matrix_held, buttons=0):
                    run.step({mech: (-32768, 0, SECONDARY if matrix_held else 0),
                              zen: (*angles, buttons)})

                step(matrix_held=False, buttons=8)
                owner = fight.zenyatta.owner(zen)
                orb = owner.discord
                self.assertIsNotNone(orb)
                self.assertEqual(orb.target, mech.body)
                for frame in range(130):
                    step(matrix_held=initially_active and frame >= 120, buttons=SECONDARY)
                self.assertEqual(bool(matrix_instance.states.get(3) and matrix_instance.states[3].active),
                                 initially_active)
                before_charge, before_frame = charge.value(), run.frame
                shots = {}
                switched = False
                for _ in range(90):
                    step(matrix_held=initially_active != switched)
                    live = {id(rocket): rocket for rocket in fight.rockets if rocket.shooter is zen}
                    shots.update(live)
                    resolved = len(shots.keys() - live.keys())
                    if not switched and resolved == 2:
                        hits = zen.client.messages(combat.HIT)
                        self.assertEqual(len(hits), 0 if initially_active else 2)
                        self.assertTrue(live, "Matrix transition must happen with another orb in flight")
                        switched = True
                    if switched and resolved == 5:
                        break

                self.assertTrue(switched)
                self.assertEqual(len(shots), 5)
                self.assertTrue(all(rocket.definition == CHARGED_ORB for rocket in shots.values()))
                self.assertFalse(fight.rockets)
                self.assertIs(owner.discord, orb)
                self.assertEqual(matrix_instance.states[3].active, not initially_active)
                expected_hits = 3 if initially_active else 2
                hits = [notice["+0x78"] for notice in zen.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"], hit["+0x8"]) for hit in hits],
                                 [(mech.body, 115, combat.CRITICAL)] * expected_hits)
                self.assertEqual(vitals.total, 600 - 115 * expected_hits)
                self.assertEqual(weapon.vars[53].value(), 15)
                self.assertAlmostEqual(charge.value() - before_charge,
                                       115 * expected_hits + (run.frame - before_frame) * combat.TICK * 5,
                                       places=3)

    def test_real_orb_absorption_retires_confirmed_owner_copy_without_explosion_release(self):
        run, mech, zen = self.arena()
        self.step(run, mech, zen, zb=FIRE)
        [rocket] = run.match.combat.rockets
        shot = rocket.shot
        self.assertIsNotNone(shot)
        # Explicit already-acknowledged transport fixture; the actual Volley and flight are unchanged.
        shot.created[0xA0001234] = projectiles._ProjectileArrival(received=True)
        zen.client.entities.clear()
        self.step(run, mech, zen)
        self.assertNotIn(shot, run.match.combat.projectiles.shots)
        [destroy] = [row for row in zen.client.entities if row.entity == 0xA0001234]
        self.assertEqual(destroy.op, OP_DESTROY)
        self.assertIsNone(destroy.ch2)
        self.assertEqual(self.health(run, mech).total, 600)

    def test_real_depletion_rejects_new_matrix_until_resource_recovers_then_orbs_are_consumed(self):
        run, mech, zen = self.arena()
        instance = self.instance(mech)
        for _ in range(160):
            self.step(run, mech, zen)
        self.assertEqual(instance.vars[87].value(), 0)
        self.assertFalse(instance.state(3).active)
        self.shoot(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, 557)
        for _ in range(150):
            self.step(run, mech, zen, mb=0)
        self.assertGreater(instance.vars[87].value(), 10)
        for _ in range(10):
            self.step(run, mech, zen)
        self.assertTrue(instance.state(3).active)
        hits = len(zen.client.messages(combat.HIT))
        self.shoot(run, mech, zen)
        self.assertEqual(len(zen.client.messages(combat.HIT)), hits)
        self.assertEqual(self.health(run, mech).total, 557)

    def test_matrix_depletion_keeps_the_inflight_impact_boundary_across_a_gap(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                self.check_matrix_depletion_boundary(partial)

    def check_matrix_depletion_boundary(self, partial):
        def arena():
            run, mech, zen = self.arena(active=False, distance=24)
            if partial:
                for _ in range(55):
                    self.step(run, mech, zen)
                for _ in range(90):
                    self.step(run, mech, zen, mb=0)
                self.assertGreater(self.instance(mech).vars[87].value(), 10)
                self.assertLess(self.instance(mech).vars[87].value(), 100)
            return run, mech, zen

        run, mech, zen = arena()
        self.step(run, mech, zen, zb=FIRE)
        self.assertTrue(self.instance(mech).states[3].active)
        origin = run.frame
        fight = run.match.combat
        orb, = fight.rockets
        for _ in range(35):
            self.step(run, mech, zen)
            if orb not in fight.rockets:
                break
        self.assertNotIn(orb, fight.rockets)
        self.assertEqual(self.health(run, mech).total, 600)
        travel = run.frame-origin
        self.assertGreater(travel, 5)
        for _ in range(150):
            if not self.instance(mech).states[3].active:
                break
            self.step(run, mech, zen)
        expiry = run.frame-origin
        if partial:
            self.assertLess(expiry, 125)
        else:
            self.assertEqual(expiry, 125)
        self.assertEqual(self.instance(mech).vars[87].value(), 0)

        for crossing in (expiry, expiry+1):
            for skipped in (False, True):
                with self.subTest(crossing=crossing, skipped=skipped):
                    run, mech, zen = arena()
                    fight = run.match.combat
                    self.step(run, mech, zen)
                    self.assertTrue(self.instance(mech).states[3].active)
                    start = run.frame
                    for relative in range(1, expiry-2):
                        self.step(run, mech, zen, zb=FIRE if relative == crossing-travel else 0)
                    self.assertEqual(run.frame, start+expiry-3)
                    self.assertTrue(self.instance(mech).states[3].active)
                    self.assertTrue(any(rocket.shooter is zen for rocket in fight.rockets))
                    if skipped:
                        run.frame += 4
                        self.step(run, mech, zen)
                    else:
                        for _ in range(5):
                            self.step(run, mech, zen)
                    self.assertEqual(run.frame, start+expiry+2)
                    for _ in range(25):
                        self.step(run, mech, zen)
                    self.assertFalse(fight.rockets)
                    self.assertFalse(self.instance(mech).states[3].active)
                    expected = 600 if crossing == expiry else 557
                    self.assertEqual(self.health(run, mech).total, expected)
                    hits = zen.client.messages(combat.HIT)
                    self.assertEqual(len(hits), 0 if crossing == expiry else 1)
                    if hits:
                        self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], mech.body)

    def test_absorbed_orb_does_not_add_resource_cost_beyond_authored_elapsed_drain(self):
        absorbed, defender, zen = self.arena()
        control, other, idle = self.arena()
        self.shoot(absorbed, defender, zen)
        for _ in range(31):
            self.step(control, other, idle)
        self.assertFalse(absorbed.match.combat.rockets)
        self.assertEqual(self.health(absorbed, defender).total, 600)
        self.assertEqual(self.instance(defender).vars[87].value(), self.instance(other).vars[87].value())

    def test_nearer_live_body_receives_real_orb_before_the_matrix_volume(self):
        run, mech, zen = self.arena(distance=15)
        # A real Soldier definition is a collision/health fixture, not one of the five Talon NPCs.
        soldier = heroes()[SOLDIER]
        body = npc.spawn(run.match, npc.Definition(soldier.guid, soldier.body, 'Near body', 200),
                         (0, zen.mover.position[1], 2.5), team=mech.team)
        self.shoot(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, 600)
        self.assertEqual(self.health(run, body).total, 104)
        self.assertEqual(zen.client.messages(combat.HIT)[0]['+0x78']['+0x4'], 96)

    def test_actual_harmony_and_transcendence_heal_friendly_mech_during_active_matrix(self):
        for button, rate in ((0x10, 30), (0x20, 300)):
            with self.subTest(button=button):
                run, mech, zen = self.arena()
                zen.team = mech.team
                vitals = self.health(run, mech)
                vitals.damage(100)
                before = vitals.total
                if button == 0x20:
                    script = zen.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                self.step(run, mech, zen, zb=button)
                for _ in range(20):
                    self.step(run, mech, zen)
                self.assertTrue(self.instance(mech).state(3).active)
                self.assertAlmostEqual(vitals.total, min(600, before+rate*.016*20), delta=rate*.016)

    def test_flashbang_and_projection_order_controls_matrix_handoff(self):
        for projection_first in (True, False):
            with self.subTest(projection_first=projection_first):
                run, mech, zen = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                zen.team, mech.team = 0, 1
                x, y, z = mech.mover.position
                actors = []
                for number, name, guid, team, position in (
                    (3, "Zarya", ZARYA, 1, [x+4, y, z]),
                    (4, "McCree", 0x02E0000000000042, 0, [x, y, z+2]),
                ):
                    hero = heroes()[guid]
                    player = match.add_player(number, name, guid, team, False)
                    in_world(match, player)
                    self.assertTrue(match.switch_hero(player, hero))
                    player.team = team
                    player.mover.position = position
                    player.mover.body = None
                    run.players.append(player)
                    actors.append(player)
                zarya, caster = actors
                projection = aim(fight._eye(zarya), fight._eye(mech))
                flash = aim(fight._eye(caster), (x, y+1, z))
                matrix_input = {mech: (-32768, 0, SECONDARY)}
                key = (zarya.body, PROJECTED)

                if projection_first:
                    run.step({**matrix_input, zarya: (*projection, 16)})
                    self.assertEqual(fight.barriers.bubbles[key].target, mech.body)
                run.step({**matrix_input, caster: (*flash, 16)})
                for _ in range(24):
                    run.step(matrix_input)
                self.assertEqual(fight.is_stunned(mech), not projection_first)
                self.assertEqual(self.instance(mech).state(3).active, projection_first)
                before = self.health(run, mech).total
                self.assertEqual(before, 600 if projection_first else 580)
                if not projection_first:
                    run.step({zarya: (*projection, 16)})
                bubble = fight.barriers.bubbles[key]
                self.assertEqual((bubble.target, bubble.hp), (mech.body, 175 if projection_first else 200))

                self.assertEqual(fight.is_stunned(mech), not projection_first)
                self.assertEqual(self.instance(mech).state(3).active, projection_first)
                self.shoot(run, mech, zen, mb=SECONDARY if projection_first else 0)
                self.assertEqual(bubble.hp, 175 if projection_first else 152)
                energy_graph = next(i for i in zarya.body_script.component.instances.values()
                                    if i.graph.index == 0x0242)
                energy = energy_graph.find_var(1, 87, create=False).value()
                self.assertAlmostEqual(energy, 5 if projection_first else 9.6, places=4)
                self.assertEqual(self.health(run, mech).total, before)
                self.assertFalse(zen.client.messages(combat.HIT))
                self.assertFalse(fight.rockets)

    def test_actual_back_flashbang_interrupts_matrix_then_real_zen_orb_hits(self):
        run = Run(names=('Mech', 'Zenyatta', 'McCree'))
        mech, zen, caster = run.players
        run.match.bots.clear()
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[dva.HERO], body=dva.MECH)))
        self.assertTrue(run.match.switch_hero(zen, heroes()[0x02E0000000000020]))
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        mech.team, zen.team, caster.team = 1, 0, 0
        floor = mech.mover.position[1]
        collision = flat_world(floor)
        run.match.collision_world = collision
        for player, z in ((mech, 8), (zen, 0), (caster, 10)):
            player.mover.position[:] = (0, floor, z)
            player.mover.use(world=collision)
        for _ in range(100):
            self.step(run, mech, zen, mb=0)
        for _ in range(10):
            self.step(run, mech, zen)
        self.assertTrue(self.instance(mech).state(3).active)
        fight = run.match.combat
        angles = aim(fight._eye(caster), (0, floor+1, 8))
        run.step({mech: (-32768, 0, SECONDARY), caster: (*angles, 16)})
        for _ in range(50):
            if fight.is_stunned(mech):
                break
            self.step(run, mech, zen)
        self.assertTrue(fight.is_stunned(mech))
        self.assertFalse(self.instance(mech).state(3).active)
        before = self.health(run, mech).total
        self.shoot(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, before-43)

    def test_off_back_and_released_matrix_allow_real_orb_damage(self):
        for mode in ('off', 'back', 'released'):
            with self.subTest(mode=mode):
                yaw = 0 if mode == 'back' else -32768
                run, mech, zen = self.arena(active=mode != 'off', yaw=yaw)
                if mode == 'released':
                    for _ in range(30):
                        self.step(run, mech, zen, mb=0)
                    self.assertFalse(self.instance(mech).state(3).active)
                self.shoot(run, mech, zen, mb=SECONDARY if mode == 'back' else 0, yaw=yaw)
                self.assertEqual(self.health(run, mech).total, 557)
                self.assertEqual(len(zen.client.messages(combat.HIT)), 1)

    def test_actual_melee_bypasses_active_matrix(self):
        run, mech, zen = self.arena(distance=2)
        self.step(run, mech, zen, zb=0x800)
        for _ in range(35):
            self.step(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, 575)
        self.assertEqual(zen.client.messages(combat.HIT)[0]['+0x78']['+0x4'], 25)

    def test_discord_targeting_bypasses_matrix_but_consumed_orb_grants_no_damage(self):
        run, mech, zen = self.arena()
        self.step(run, mech, zen, zb=8)
        self.assertIsNotNone(run.match.combat.zenyatta.owners[zen.body].discord)
        self.shoot(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, 600)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_authored_bounds_rotate_with_aim_and_do_not_expand_to_unknown_geometry(self):
        run, mech, _ = self.arena()
        eye = run.match.combat._eye(mech)
        forward = (0, 0, 1)
        self.assertAlmostEqual(matrix.matrix_distance(
            mech, (0, eye[1], 23), (0, 0, -1), 30, eye, forward), 4.75)
        for x, y in ((1.26, eye[1]), (0, eye[1]+1.01)):
            self.assertIsNone(matrix.matrix_distance(mech, (x, y, 23), (0, 0, -1), 30, eye, forward))
        self.assertAlmostEqual(matrix.matrix_distance(
            mech, (15, eye[1], 8), (-1, 0, 0), 30, eye, (1, 0, 0)), 4.75)

    def test_world_wall_precedes_matrix_and_no_damage_or_charge_is_reported(self):
        run, mech, zen = self.arena(distance=15)
        floor = zen.mover.position[1]
        run.match.collision_world = World([
            (-5, floor, 2, 5, floor, 2, 5, floor+5, 2),
            (-5, floor, 2, 5, floor+5, 2, -5, floor+5, 2),
        ])
        with patch.object(run.match.combat, '_matrix_hit', wraps=run.match.combat._matrix_hit) as query:
            self.shoot(run, mech, zen)
        self.assertTrue(query.called)
        self.assertEqual(self.health(run, mech).total, 600)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_dead_and_replaced_mech_cannot_leave_a_live_matrix(self):
        for death in (False, True):
            with self.subTest(death=death):
                run, mech, zen = self.arena()
                fight = run.match.combat
                if death:
                    fight._damage(zen, next(t for t in fight._targets() if t.owner is mech),
                                  10000, False, None, run.frame)
                else:
                    self.assertTrue(run.match.switch_hero(mech, heroes()[dva.HERO]))
                self.assertIsNone(fight._matrix_hit(zen, fight._eye(zen), (0, 0, 1), 30, ORB))

    def test_team_filter_and_ffa_use_actual_match_relationships(self):
        run, mech, zen = self.arena()
        fight = run.match.combat
        zen.team = mech.team
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        self.assertIsNone(fight._matrix_hit(zen, fight._eye(zen), (0, 0, 1), 30, ORB))
        run.match.game_map = replace(run.match.game_map, free_for_all=True)
        self.assertIsNotNone(fight._matrix_hit(zen, fight._eye(zen), (0, 0, 1), 30, ORB))
