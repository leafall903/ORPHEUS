"""Mei against explicit resource-backed D.Va mech fixture, not native spawning."""

import copy
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_mei_blizzard as blizzard
import test_mei_combat as fixtures
import test_mei_wall as wall_fixtures
from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat, dva_matrix, genji_deflect, pools
from ow174.game.content import heroes
from ow174.game.script import graph


@unittest.skipUnless(os.environ.get('ORPHEUS_DVA_MECH_DATA'), 'explicit104319 mech supplement required')
class MatrixTests(unittest.TestCase):
    def setUp(self):
        supplement = json.loads(Path(os.environ['ORPHEUS_DVA_MECH_DATA']).read_text(encoding='utf8'))
        self.assertEqual(supplement['build'], 104319)
        self.assertFalse(supplement['missing'])
        data = copy.deepcopy(graph._data())
        data['graphs'] += supplement['graphs']
        data['bodies'].pop('0x040000000000040A')
        data['bodies']['0x0400000000000407'] = supplement['body']
        self.graph_patch = patch.object(graph, '_data', return_value=data)
        self.graph_patch.start()
        self.reset_caches()
        self.addCleanup(self.cleanup_graphs)

    def reset_caches(self):
        graph._records.cache_clear()
        graph.graph.cache_clear()
        pools.hero_pools.cache_clear()

    def cleanup_graphs(self):
        self.graph_patch.stop()
        self.reset_caches()

    def prepare_possessed_mech(self, dva):
        # The runtime does not host native possession. End unpossessed startup
        # while leaving Matrix itself under the authored input graph.
        startup = next(i for i in dva.body_script.component.instances.values() if i.graph.index == 0x276)
        startup.state(30).end(False)

    def scene(self):
        run, mei, dva, _target = fixtures.MeiCombatTests().duel()
        mech = replace(heroes()[dva_matrix.HERO], body=0x0400000000000407)
        run.match.switch_hero(dva, mech)
        x, y, z = mei.mover.position
        run.match.combat.walls.place_player(dva, (x, y, z + 5.))
        angle = fixtures.aim(run.match.combat._eye(dva), run.match.combat._eye(mei))
        for _ in range(70):
            run.step({dva: (*angle, 0)})
        self.prepare_possessed_mech(dva)
        instance = next(i for i in dva.body_script.component.instances.values() if i.graph.index == 0x2CC)
        return run, mei, dva, instance, angle

    def test_native_resource_initialization_actual_rmb_release(self):
        run, _mei, dva, instance, angle = self.scene()
        self.assertEqual(instance.vars[971].value(), 100.)
        self.assertEqual(instance.vars[87].value(), 100.)
        run.step({dva: (*angle, 2)})
        self.assertTrue(instance.state(3).active)
        for _ in range(10):
            run.step({dva: (*angle, 2)})
        self.assertLess(instance.vars[87].value(), 100.)
        run.step({dva: (*angle, 0)})
        for _ in range(30):
            run.step({dva: (*angle, 0)})
        self.assertFalse(instance.state(3).active)

    def target(self, run, actor):
        return next(t for t in run.match.combat._targets() if t.owner is actor)

    def health(self, run, actor):
        return run.match.combat.vitals_of(self.target(run, actor)).total

    def fire(self, run, mei, dva, angle, button, *, height=.9):
        point = dva.mover.position
        attack = fixtures.aim(run.match.combat._eye(mei), (point[0], point[1] + height, point[2]))
        run.step({dva: (*angle, 2)})
        for frame in range(45):
            run.step({mei: (*attack, button if frame < 20 else 0), dva: (*angle, 2)})

    def test_actual_icicle_and_blizzard_use_first_matrix_or_deflect_contact(self):
        for button in (2, 32):
            for matrix_first in (False, True):
                for clear_front in (False, True):
                    with self.subTest(button=button, matrix_first=matrix_first, clear_front=clear_front):
                        self.check_defense_contact_order(button, matrix_first, clear_front)

    def test_matrix_absorbs_when_both_active_defenses_contact_at_launch(self):
        def observe(cast, contacts):
            def query(*args, **kwargs):
                hit = cast(*args, **kwargs)
                if hit is not None:
                    contacts.append((cast.__module__, hit[0]))
                return hit
            return query

        for button in (2, 32):
            with self.subTest(button=button):
                # Observe the real box queries without supplying their results.
                contacts = []
                with (
                    patch.object(dva_matrix, 'cast', side_effect=observe(dva_matrix.cast, contacts)),
                    patch.object(genji_deflect, 'cast', side_effect=observe(genji_deflect.cast, contacts)),
                ):
                    self.check_defense_contact_order(button, True, False, tie=True)
                self.assertCountEqual(contacts, [(dva_matrix.__name__, 0.), (genji_deflect.__name__, 0.)])

    def check_defense_contact_order(self, button, matrix_first, clear_front, *, tie=False):
        run = Run(KANEZAKA_DM, ('Mei', 'Genji', 'D.Va'))
        owner, genji, dva = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.game_map = replace(match.game_map, free_for_all=False)
        owner.team, genji.team, dva.team = 0, 1, 1
        self.assertTrue(match.switch_hero(owner, heroes()[fixtures.MEI]))
        self.assertTrue(match.switch_hero(genji, heroes()[genji_deflect.HERO]))
        mech = replace(heroes()[dva_matrix.HERO], body=dva_matrix.MECH)
        self.assertTrue(match.switch_hero(dva, mech))
        fight = match.combat
        x, y, z = owner.mover.position
        genji_distance = .8 if tie else (12. if matrix_first else 5.)
        fight.walls.place_player(genji, (x, y, z + genji_distance))
        fight.walls.place_player(dva, (x, y, z + (5. if matrix_first else 20.)))
        for _ in range(70):
            run.step({})
        self.prepare_possessed_mech(dva)

        genji_aim = fixtures.aim(fight._eye(genji), fight._eye(owner))
        dva_aim = fixtures.aim(fight._eye(dva), fight._eye(owner))
        front = dva if matrix_first else genji
        point = fight._eye(front)
        if button == 2:
            point = (point[0], front.mover.position[1] + .9, point[2])
        attack = fixtures.aim(fight._eye(owner), point)
        if tie:
            attack = (0, 0)
            genji_aim = dva_aim = (-32768, 0)
        if clear_front:
            # Keep the launch aim fixed and clear the inactive foreground body.
            px, py, pz = front.mover.position
            fight.walls.place_player(front, (px + 8., py, pz))
        genji_enabled = not clear_front or matrix_first
        matrix_enabled = not clear_front or not matrix_first
        owner.body_script.set_ult_charge(1610.)
        launched = reflected = False
        for frame in range(85):
            fire = button if frame == 2 or (button == 2 and 2 <= frame < 22) else 0
            run.step({
                owner: (*attack, fire),
                genji: (*genji_aim, 16 if frame == 0 and genji_enabled else 0),
                dva: (*dva_aim, 2 if matrix_enabled else 0),
            })
            if frame == 1:
                self.assertEqual(bool(genji_deflect.active(fight, genji)), genji_enabled)
                self.assertEqual(bool(dva_matrix.active(fight, dva)), matrix_enabled)
            if button == 2:
                launched |= bool(fight.rockets)
                reflected |= any(rocket.shooter is genji for rocket in fight.rockets)
            else:
                launched |= bool(fight.blizzard.flights)
                reflected |= any(flight.owner is genji for flight in fight.blizzard.flights)
        self.assertTrue(launched)
        expect_reflection = matrix_first == clear_front
        self.assertEqual(reflected, expect_reflection)
        self.assertEqual(self.health(run, genji), 200.)
        self.assertEqual(self.health(run, dva), 600.)
        self.assertFalse(owner.client.messages(combat.HIT))
        if button == 2:
            self.assertFalse(fight.rockets)
            self.assertEqual(self.health(run, owner), 175. if expect_reflection else 250.)
            self.assertEqual(len(genji.client.messages(combat.HIT)), int(expect_reflection))
        else:
            self.assertFalse(fight.blizzard.flights)
            owners = [area.owner for area in fight.blizzard.areas.values()]
            self.assertEqual(owners, [genji] if expect_reflection else [])

    def test_actual_icicle_absorbed_without_damage_hit_credit_or_reflection(self):
        run, mei, dva, instance, angle = self.scene()
        before = self.health(run, dva)
        self.fire(run, mei, dva, angle, 2)
        self.assertEqual(self.health(run, dva), before)
        self.assertEqual(self.health(run, mei), 250.)
        self.assertFalse(mei.client.messages(combat.HIT))
        self.assertFalse(dva.client.messages(combat.HIT))
        self.assertLess(instance.vars[87].value(), 100.)
        self.assertFalse(run.match.combat.rockets)

    def test_actual_spray_excluded_tag_damages_and_slows_mech(self):
        run, mei, dva, _instance, angle = self.scene()
        before = self.health(run, dva)
        self.fire(run, mei, dva, angle, 1)
        self.assertLess(self.health(run, dva), before)
        self.assertIsNotNone(run.match.combat.frost(dva))

    def test_melee_remains_effective_during_matrix(self):
        run, mei, dva, _instance, angle = self.scene()
        x, y, z = mei.mover.position
        run.match.combat.walls.place_player(dva, (x, y, z + 1.5))
        before = self.health(run, dva)
        self.fire(run, mei, dva, angle, 0x800)
        self.assertEqual(self.health(run, dva), before - 25.)  # native mech armour, reconstructed5 block

    def test_airborne_blizzard_is_deleted_without_area_or_charge_refund(self):
        run, mei, dva, instance, angle = self.scene()
        mei.body_script.set_ult_charge(1610.)
        attack = fixtures.aim(run.match.combat._eye(mei), run.match.combat._eye(dva))
        run.step({dva: (*angle, 2)})
        for frame in range(95):
            run.step({mei: (*attack, 32 if frame == 0 else 0), dva: (*angle, 2)})
        self.assertFalse(run.match.combat.blizzard.flights)
        self.assertFalse(run.match.combat.blizzard.areas)
        self.assertLess(mei.body_script.component.entity_var(342).value(), 100.)
        self.assertGreater(instance.vars[87].value(), 0.)
        self.assertFalse(mei.client.messages(combat.HIT))

    def test_landed_blizzard_is_not_removed_by_matrix(self):
        run, mei, dva, _instance, angle = self.scene()
        mei.body_script.set_ult_charge(1610.)
        x, y, z = mei.mover.position
        attack = fixtures.aim(run.match.combat._eye(mei), (x, y, z + 3.))
        for frame in range(32):
            run.step({mei: (*attack, 32 if frame == 0 else 0), dva: (*angle, 0)})
        area = blizzard.BlizzardTests().landed(run)
        before = self.health(run, dva)
        for _ in range(75):
            run.step({dva: (*angle, 2)})
        self.assertIn(area.entity, run.match.combat.blizzard.areas)
        self.assertLess(self.health(run, dva), before)
        self.assertIsNotNone(run.match.combat.frost(dva))

    def test_ice_wall_anchors_blizzard_before_matrix_and_blocks_area_until_cancel(self):
        run, mei, dva, instance, angle = self.scene()
        fight = run.match.combat
        walls = wall_fixtures.WallTests()
        pieces, wall_aim = walls.place(run, mei)
        self.assertEqual(len(pieces), 5)
        mei.body_script.set_ult_charge(1610.)
        attack = fixtures.aim(fight._eye(mei), fight._eye(dva))
        before = self.health(run, dva)
        for frame in range(100):
            run.step({mei: (*attack, 32 if frame == 0 else 0), dva: (*angle, 2)})
            if fight.blizzard.areas:
                break
        self.assertTrue(instance.state(3).active)
        self.assertEqual(len(fight.blizzard.areas), 1)
        area = next(iter(fight.blizzard.areas.values()))
        self.assertIs(area.owner, mei)
        self.assertLess(area.position[2], pieces[2].feet[2])
        self.assertFalse(fight.blizzard.flights)
        self.assertLess(mei.body_script.component.entity_var(342).value(), 100.)
        for _ in range(30):
            run.step({dva: (*angle, 2)})
        self.assertGreater(run.frame * 16, area.starts)
        self.assertEqual(self.health(run, dva), before)
        self.assertIsNone(fight.frost(dva))
        self.assertTrue(all(piece.health == 400. for piece in pieces))

        run.step({mei: (*wall_aim, 16), dva: (*angle, 2)})
        for _ in range(20):
            run.step({dva: (*angle, 2)})
        self.assertFalse(fight.walls.pieces)
        self.assertTrue(instance.state(3).active)
        self.assertIn(area.entity, fight.blizzard.areas)
        self.assertLess(self.health(run, dva), before)
        self.assertIsNotNone(fight.frost(dva))

    def test_empty_resource_stops_absorption_and_repeated_press_cannot_refill(self):
        run, mei, dva, instance, angle = self.scene()
        for _ in range(140):
            run.step({dva: (*angle, 2)})
        self.assertFalse(instance.state(3).active)
        self.assertLess(instance.vars[87].value(), 5.)
        capacity = instance.vars[971].value()
        for frame in range(12):
            run.step({dva: (*angle, 2 if frame % 2 else 0)})
        self.assertEqual(instance.vars[971].value(), capacity)
        self.assertLess(instance.vars[87].value(), 10.)
        before = self.health(run, dva)
        self.fire(run, mei, dva, angle, 2)
        self.assertLess(self.health(run, dva), before)

    def test_allied_icicle_passes_matrix_without_absorption(self):
        run, mei, dva, _instance, angle = self.scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        dva.team = mei.team
        point = dva.mover.position
        attack = fixtures.aim(run.match.combat._eye(mei), (point[0], point[1] + .9, point[2]))
        run.step({dva: (*angle, 2)})
        for frame in range(28):
            run.step({mei: (*attack, 2 if frame < 20 else 0), dva: (*angle, 2)})
        self.assertTrue(run.match.combat.rockets)  # passed ally body and matrix, still in flight
        self.assertTrue(all(r.shooter is mei for r in run.match.combat.rockets))
        self.assertFalse(mei.client.messages(combat.HIT))

    def test_static_los_prevents_absorption_through_wall(self):
        run, mei, dva, _instance, angle = self.scene()
        fixtures.static_wall(run, mei, 2.)
        self.fire(run, mei, dva, angle, 2)
        self.assertFalse(run.match.combat.rockets)
        # Health alone cannot distinguish wall collision from absorption:
        # query actual active matrix against the emitter-side segment too.
        self.assertIsNotNone(dva_matrix.active(run.match.combat, dva))
        self.assertIsNone(dva_matrix.cast(run.match.combat, mei, 0x040000000000108B,
                                         run.match.combat._eye(mei), (0., 0., 1.), .5))
        self.assertFalse(mei.client.messages(combat.HIT))

    def test_actual_spray_freeze_ends_matrix_channel(self):
        run, mei, dva, instance, angle = self.scene()
        point = dva.mover.position
        attack = fixtures.aim(run.match.combat._eye(mei), (point[0], point[1] + .9, point[2]))
        run.step({dva: (*angle, 2)})
        for _ in range(105):
            run.step({mei: (*attack, 1), dva: (*angle, 2)})
            if run.match.combat.frozen(dva):
                break
        self.assertTrue(run.match.combat.frozen(dva))
        self.assertFalse(instance.state(3).active)
        self.assertFalse(instance.state(0).flags & instance.state(0).RUNNING)
        for _ in range(100):
            run.step({dva: (*angle, 0)})
        self.assertFalse(instance.state(3).active)

    def test_ruleset_disable_refuses_actual_rmb_without_spending_fuel(self):
        run, _mei, dva, instance, angle = self.scene()
        dva.body_script.component.set(9743, False)
        for _ in range(40):
            run.step({dva: (*angle, 2)})
        self.assertFalse(instance.state(3).active)
        self.assertEqual(instance.vars[87].value(), 100.)

    def test_death_immediately_refuses_matrix_interception(self):
        run, mei, dva, instance, angle = self.scene()
        run.step({dva: (*angle, 2)})
        self.assertTrue(instance.state(3).active)
        run.match.combat._damage(mei, self.target(run, dva), 2000., False, None, run.frame)
        self.assertTrue(run.match.combat.is_dead(dva))
        self.assertIsNone(dva_matrix.active(run.match.combat, dva))
        run.step({dva: (*angle, 2)})
        self.assertFalse(instance.state(3).active)
        self.assertFalse(instance.state(0).flags & instance.state(0).RUNNING)

    def test_native_recovery_restores_capacity_and_later_actual_rmb(self):
        run, _mei, dva, instance, angle = self.scene()
        for _ in range(40):
            run.step({dva: (*angle, 2)})
        depleted = instance.vars[87].value()
        self.assertGreater(depleted, 0.)
        self.assertLess(depleted, 100.)
        for _ in range(450):
            run.step({dva: (*angle, 0)})
        self.assertEqual(instance.vars[87].value(), 100.)
        self.assertEqual(instance.vars[61].value(), 0.)
        run.step({dva: (*angle, 2)})
        self.assertTrue(instance.state(3).active)


if __name__ == '__main__':
    unittest.main()
