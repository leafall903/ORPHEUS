"""Actual Symmetra primary versus Mei and her defenses in a static match."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_mei_combat as fixtures
import test_mei_shields as shields
import test_mei_wall as walls
from test_game_combat import aim, in_world

from ow174.game import combat, mei
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import FIRE
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, runtime

SYMMETRA = 0x02E0000000000016


class MeiSymmetraBeamTests(unittest.TestCase):
    def scene(self):
        run, owner, symmetra, _ = fixtures.MeiCombatTests().duel()
        self.assertTrue(run.match.switch_hero(symmetra, heroes()[SYMMETRA]))
        x, y, z = owner.mover.position
        run.match.combat.walls.place_player(symmetra, (x, y, z + 5.))
        for _ in range(12):
            run.step({})
        return run, owner, symmetra, run.match.combat

    def health(self, fight, player):
        target = next(t for t in fight._targets() if t.owner is player)
        return fight.vitals_of(target).total

    def inputs(self, run, symmetra, target, count=1, buttons=FIRE, mei_input=None):
        fight = run.match.combat
        x, y, z = target.mover.position
        angle = aim(fight._eye(symmetra), (x, y + 1.2, z))
        for _ in range(count):
            frame = dict(mei_input or {})
            frame[symmetra] = (*angle, buttons)
            run.step(frame)

    def test_beam_deals_authored_base_damage_once_per_tick_and_releases(self):
        run, owner, symmetra, fight = self.scene()
        self.inputs(run, symmetra, owner, 10)
        self.assertAlmostEqual(self.health(fight, owner), 240.4, places=5)
        self.assertEqual(len(symmetra.client.messages(combat.HIT)), 10)
        weapon = next(i for i in symmetra.body_script.component.instances.values() if i.graph.index == 0x157C)
        self.assertEqual(weapon.find_var(0, 16627).value(), expr.Entity(owner.body))
        self.assertTrue(weapon.state(86).active)
        self.inputs(run, symmetra, owner, 12, buttons=0)
        self.assertAlmostEqual(self.health(fight, owner), 240.4, places=5)
        self.assertFalse(weapon.state(86).active)

    def test_beam_rejects_ally_range_miss_and_static_cover(self):
        for reason in ('ally', 'range', 'miss', 'static'):
            with self.subTest(reason=reason):
                run, owner, symmetra, fight = self.scene()
                if reason == 'ally':
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = symmetra.team
                if reason == 'range':
                    x, y, z = owner.mover.position
                    fight.walls.place_player(symmetra, (x, y, z + 30.))
                if reason == 'static':
                    fixtures.static_wall(run, owner, distance=2.)
                if reason == 'miss':
                    for _ in range(10):
                        run.step({symmetra: (0, 0, FIRE)})
                else:
                    self.inputs(run, symmetra, owner, 10)
                self.assertEqual(self.health(fight, owner), 250.)
                self.assertFalse(symmetra.client.messages(combat.HIT))

    def test_cryo_blocks_rear_body_and_cancel_restores_beam_damage(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, owner, symmetra, fight = self.scene()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team = symmetra.team if allied else 1 - symmetra.team
                rear = run.match.add_player(3, 'Rear', SOLDIER, 1 - symmetra.team, False)
                in_world(run.match, rear)
                run.players.append(rear)
                self.assertTrue(run.match.switch_hero(rear, heroes()[SOLDIER]))
                x, y, z = owner.mover.position
                fight.walls.place_player(rear, (x, y, z - 1.8))
                for _ in range(10):
                    run.step({})
                self.inputs(run, symmetra, rear, mei_input={owner: (0, 0, 8)})
                self.assertTrue(mei.protected(owner))
                self.inputs(run, symmetra, rear, 35)
                self.assertEqual((self.health(fight, owner), self.health(fight, rear)), (250., 200.))
                self.assertFalse(symmetra.client.messages(combat.HIT))
                self.inputs(run, symmetra, rear, mei_input={owner: (0, 0, FIRE)})
                self.assertFalse(mei.protected(owner))
                self.inputs(run, symmetra, rear, 5)
                damaged = rear if allied else owner
                self.assertLess(self.health(fight, damaged), 200. if allied else 250.)

    def test_ice_wall_takes_beam_without_body_damage_then_cancel_exposes_mei(self):
        run, owner, symmetra, fight = self.scene()
        fixture = walls.WallTests()
        pieces, wall_aim = fixture.place(run, owner, distance=2.)
        self.assertEqual(len(pieces), 5)
        self.inputs(run, symmetra, owner, 10)
        self.assertEqual(self.health(fight, owner), 250.)
        self.assertAlmostEqual(pieces[2].health, 390.4, places=5)
        self.assertTrue(all(piece.health == 400. for piece in pieces if piece is not pieces[2]))
        fixture.tap(run, owner, 16, wall_aim)
        self.assertFalse(fight.walls.pieces)
        self.inputs(run, symmetra, owner, 5)
        self.assertLess(self.health(fight, owner), 250.)


    def test_sustained_beam_uses_authored_charge_transitions_against_mei(self):
        run, owner, symmetra, fight = self.scene()
        weapon = next(i for i in symmetra.body_script.component.instances.values() if i.graph.index == 0x157C)
        transitions = []
        prior_rate = None
        for frame in range(1, 170):
            before = self.health(fight, owner)
            self.inputs(run, symmetra, owner)
            rate = weapon.find_var(0, 499).value()
            if rate != prior_rate:
                transitions.append((frame, rate))
                prior_rate = rate
            self.assertAlmostEqual(before - self.health(fight, owner), rate * .016, places=5)
        self.assertEqual(transitions, [(1, 60), (85, 120), (169, 180)])
        before = self.health(fight, owner)
        self.inputs(run, symmetra, owner, 390, buttons=0)
        self.assertEqual(weapon.find_var(0, 499).value(), 60)
        self.assertEqual(self.health(fight, owner), before)

    def test_projected_barrier_protects_mei_and_refills_beam_ammo(self):
        fixture = shields.ShieldTests()
        run, symmetra, zarya, owner = fixture.scene()
        self.assertTrue(run.match.switch_hero(symmetra, heroes()[SYMMETRA]))
        self.assertTrue(run.match.switch_hero(owner, heroes()[fixtures.MEI]))
        fight = run.match.combat
        x, y, z = symmetra.mover.position
        fight.walls.place_player(owner, (x, y, z + 5.))
        fight.walls.place_player(zarya, (x + 3., y, z + 5.))
        for _ in range(16):
            run.step({})
        weapon = next(i for i in symmetra.body_script.component.instances.values() if i.graph.index == 0x157C)
        ammo = weapon.find_var(0, 53)
        stock = ammo.value()
        for _ in range(32):
            run.step({symmetra: (-32768, 0, FIRE)})
        self.assertLess(ammo.value(), stock)
        run.step({})
        bubble = fixture.cast(run, zarya, owner)
        self.inputs(run, symmetra, owner, 6)
        self.assertTrue(weapon.state(95).active)
        self.assertEqual(weapon.find_var(0, 536).value(), expr.Entity(bubble.entity))
        self.assertEqual(symmetra.body_script.world.beam_barrier, bubble.entity)
        component = symmetra.body_script.component
        with patch.object(runtime.Component, 'run_now', side_effect=AssertionError('snapshot ran the VM')):
            before = snapshot_digest(export_component_vm(component))
            component.world.beam_barrier = None
            try:
                self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
            finally:
                component.world.beam_barrier = bubble.entity
            self.assertEqual(snapshot_digest(export_component_vm(component)), before)
        before_ammo, before_shield = ammo.value(), bubble.health
        self.inputs(run, symmetra, owner, 5)
        self.assertAlmostEqual(ammo.value() - before_ammo, 5 * .016 * 7, places=3)
        self.assertAlmostEqual(before_shield - bubble.health, 5 * .016 * 60, places=5)
        self.assertEqual(self.health(fight, owner), 250.)
        self.inputs(run, symmetra, owner, 3, buttons=0)
        self.assertFalse(weapon.state(95).active)
        self.assertIsNone(symmetra.body_script.world.beam_barrier)


    def test_actual_mei_freeze_stops_beam_until_thaw_and_fresh_input(self):
        run, owner, symmetra, fight = self.scene()
        mei_aim = aim(fight._eye(owner), fight._eye(symmetra))
        for _ in range(150):
            self.inputs(run, symmetra, owner, mei_input={owner: (*mei_aim, FIRE)})
            if fight.frozen(symmetra):
                break
        self.assertTrue(fight.frozen(symmetra))
        before = self.health(fight, owner)
        self.assertLess(before, 250.)
        self.assertGreater(before, 0.)
        self.inputs(run, symmetra, owner, 12)
        self.assertEqual(self.health(fight, owner), before)
        for _ in range(150):
            if not fight.frozen(symmetra):
                break
            run.step({})
        self.assertFalse(fight.frozen(symmetra))
        self.assertEqual(self.health(fight, owner), before)
        self.inputs(run, symmetra, owner, 5)
        self.assertLess(self.health(fight, owner), before)


if __name__ == '__main__':
    unittest.main(argv=[__file__], verbosity=2)

