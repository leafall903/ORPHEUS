"""Actual Deadeye casts targeting Hanzo with the prepared 104319 resources."""

import unittest
from unittest.mock import patch

from test_game_combat import aim, in_world
from test_game_hanzo_mccree_fan import add_genji, hanzo_arena

from ow174.game import combat, replay_values, zarya_barrier
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.collision import flat_world
from ow174.game.content import heroes
from ow174.game.script import expr, graph, nodes


class HanzoDeadeyeTests(unittest.TestCase):
    def arena(self, *, distance=8, lateral=3, **options):
        fixture, run, shooter, hanzo = hanzo_arena(self, distance=distance, **options)
        hanzo.mover.position[0] = lateral
        hanzo.mover.body = None
        for _ in range(20):
            run.step({})
        script = shooter.body_script
        script.ult_seconds = 0
        self.assertEqual(script.component.vars[338].value(), 1680)
        self.assertIsNotNone(graph.graph(0x01EE), "prepared runtime must include Deadeye target effect")
        root = next(i for i in script.component.instances.values() if i.graph.index == 0x022F)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x01D2)
        return fixture, run, shooter, hanzo, root, weapon

    @staticmethod
    def advance(run, frames):
        for _ in range(frames):
            run.step({})

    def charge(self, run, shooter, frames=180):
        script = shooter.body_script
        script.set_ult_charge(1680)
        run.step({shooter: (0, 0, 32)})
        self.advance(run, frames)

    @staticmethod
    def effects(shooter):
        return [i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x01EE]

    def release(self, run, shooter, button=1):
        run.step({shooter: (0, 0, button)})
        self.advance(run, 35)

    def test_off_axis_hanzo_is_locked_and_killed_by_actual_release(self):
        fixture, run, shooter, hanzo, root, weapon = self.arena()
        self.charge(run, shooter)
        script = shooter.body_script
        self.assertTrue(root.state(0).flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.vars[342].value(), 0)
        self.assertEqual(fixture.health(run, hanzo), 200)
        effects = self.effects(shooter)
        self.assertEqual([i.vars[536].value() for i in effects], [expr.Entity(hanzo.body)])
        self.assertEqual(root.state(6).children, {hanzo.body: effects[0].id})
        snapshot = replay_values.decode(export_body_vm(script)["value"])
        self.assertTrue(any(row["ref"] == ["state", root.id, 6]
                            and "children" in dict(row["fields"]) for row in snapshot["objects"]))
        self.release(run, shooter)
        self.assertEqual(fixture.health(run, hanzo), 0)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.assertEqual(hanzo.client.messages(combat.DEATH_NOTICE),
                         [{"+0x78": combat.KILLED_BY, "+0x7C": {"+0x0": shooter.body}}])
        self.assertFalse(root.state(0).flags & nodes.Ability.RUNNING)
        self.assertFalse(self.effects(shooter))

    def test_below_cost_gate_then_early_second_q_only_deals_partial_damage(self):
        fixture, run, shooter, hanzo, root, weapon = self.arena()
        script = shooter.body_script
        script.set_ult_charge(1679)
        run.step({shooter: (0, 0, 32)})
        self.advance(run, 20)
        self.assertFalse(root.state(0).flags & nodes.Ability.RUNNING)
        self.assertFalse(self.effects(shooter))
        self.assertEqual(script.component.vars[342].value(), 1679)
        self.charge(run, shooter, frames=20)
        fight = run.match.combat
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as ray:
            self.release(run, shooter, button=32)
        ray.assert_called_once()
        self.assertAlmostEqual(ray.call_args.args[4].amount, 12.800095558166504, delta=1e-5)
        self.assertAlmostEqual(fixture.health(run, hanzo), 187.1999044418335, delta=1e-5)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.assertEqual(script.component.vars[342].value(), 0)
        self.assertFalse(self.effects(shooter))

    def test_cancel_and_expiry_retire_effect_and_leave_ammo(self):
        for cancel in (True, False):
            with self.subTest(cancel=cancel):
                fixture, run, shooter, hanzo, root, weapon = self.arena()
                self.charge(run, shooter, frames=65)
                self.assertEqual(len(self.effects(shooter)), 1)
                if cancel:
                    self.release(run, shooter, button=2)
                else:
                    self.advance(run, 330)
                self.assertFalse(root.state(0).flags & nodes.Ability.RUNNING)
                self.assertFalse(root.state(6).children)
                self.assertFalse(self.effects(shooter))
                self.assertEqual(fixture.health(run, hanzo), 200)
                self.assertEqual(weapon.vars[53].value(), 6)

    def test_search_membership_and_physical_range_are_independent(self):
        for options, locked, visible, hp in (
            ({"distance": 99, "lateral": 0}, True, True, 0),
            ({"distance": 101, "lateral": 0}, True, True, 200),
            ({"distance": 201, "lateral": 0}, False, False, 200),
            ({"distance": -8, "lateral": 0}, False, False, 200),
            ({"ally": True}, False, False, 200),
            ({"wall": True}, True, False, 200),
        ):
            with self.subTest(**options):
                fixture, run, shooter, hanzo, root, weapon = self.arena(**options)
                self.charge(run, shooter)
                self.assertEqual(bool(self.effects(shooter)), locked)
                self.assertEqual(expr.Entity(hanzo.body) in root.component.entity_var(5874).value(), visible)
                self.release(run, shooter)
                self.assertEqual(fixture.health(run, hanzo), hp)
                self.assertEqual(weapon.vars[53].value(), 5 if visible else 6)
                if locked and not visible:
                    self.assertTrue(root.state(0).flags & nodes.Ability.RUNNING)
                    self.assertEqual(len(self.effects(shooter)), 1)
                    self.release(run, shooter, button=2)
                self.assertFalse(self.effects(shooter))

    def test_wall_pauses_charge_and_visibility_restores_same_target_effect(self):
        fixture, run, shooter, hanzo, root, weapon = self.arena(wall=True)
        self.charge(run, shooter, frames=150)
        effect, = self.effects(shooter)
        visible = root.component.entity_var(5874)
        self.assertEqual(visible.value(), ())
        self.assertEqual(effect.vars[476].value(), 0)
        run.match.collision_world = flat_world(hanzo.mover.position[1])
        self.advance(run, 20)
        self.assertEqual(visible.value(), (expr.Entity(hanzo.body),))
        self.assertEqual(self.effects(shooter), [effect])
        self.release(run, shooter)
        damage = 200 - fixture.health(run, hanzo)
        self.assertAlmostEqual(damage, 83.60022735595703, delta=1e-5)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.assertFalse(self.effects(shooter))

    def test_allied_zarya_projected_shield_takes_deadeye_without_spill(self):
        fixture, run, shooter, hanzo, root, weapon = self.arena()
        match = run.match
        fight = match.combat
        zarya = match.add_player(3, "Zarya", zarya_barrier.HERO, hanzo.team, False)
        in_world(match, zarya)
        run.players.append(zarya)
        self.assertTrue(match.switch_hero(zarya, heroes()[zarya_barrier.HERO]))
        zarya.mover.position[:] = (20, hanzo.mover.position[1], 8)
        zarya.mover.body = None
        self.charge(run, shooter)
        angles = aim(fight._eye(zarya), fight._eye(hanzo))
        run.step({zarya: (*angles, 16)})
        shields = fight.zarya_barriers
        self.assertEqual(shields.projected[zarya.body][1:], (200, hanzo.body))
        self.release(run, shooter)
        self.assertNotIn(zarya.body, shields.projected)
        self.assertEqual(fixture.health(run, hanzo), 200)
        self.assertEqual(fixture.health(run, zarya), 400)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.assertFalse(shooter.client.messages(combat.HIT))

    def test_hanzo_full_bows_kill_caster_before_release_and_clear_lock(self):
        fixture, run, shooter, hanzo, root, weapon = self.arena()
        self.charge(run, shooter, frames=30)
        self.assertEqual(len(self.effects(shooter)), 1)
        fight = run.match.combat
        target = (shooter.mover.position[0], shooter.mover.position[1] + 1, shooter.mover.position[2])
        angles = aim(fight._eye(hanzo), target)
        for expected in (100, 0):
            for _ in range(70):
                run.step({hanzo: (*angles, 1)})
            run.step({hanzo: (*angles, 0)})
            self.advance(run, 25)
            self.assertEqual(fixture.health(run, shooter), expected)
        self.assertTrue(fight.is_dead(shooter))
        self.assertFalse(root.state(6).children)
        self.assertFalse(self.effects(shooter))
        self.release(run, shooter, button=32)
        self.assertEqual(fixture.health(run, hanzo), 200)
        self.assertEqual(weapon.vars[53].value(), 6)
        self.assertFalse(shooter.client.messages(combat.HIT))
        self.assertEqual(shooter.client.messages(combat.DEATH_NOTICE),
                         [{"+0x78": combat.KILLED_BY, "+0x7C": {"+0x0": hanzo.body}}])

    def test_allied_genji_reflects_deadeye_away_from_hanzo(self):
        fixture, run, shooter, hanzo, root, weapon = self.arena()
        fight = run.match.combat
        genji = add_genji(self, run, hanzo.team, (1.5, hanzo.mover.position[1], 4))
        self.advance(run, 20)
        self.charge(run, shooter)
        self.assertEqual({effect.vars[536].value() for effect in self.effects(shooter)},
                         {expr.Entity(genji.body), expr.Entity(hanzo.body)})
        facing = aim(fight._eye(genji), fight._eye(shooter))
        run.step({genji: (*facing, 16)})
        run.step({genji: (*facing, 0), shooter: (0, 0, 1)})
        for _ in range(35):
            run.step({genji: (*facing, 0)})
        self.assertEqual(fixture.health(run, hanzo), 200)
        self.assertEqual(fixture.health(run, genji), 200)
        self.assertTrue(fight.is_dead(shooter))
        self.assertEqual(shooter.client.messages(combat.DEATH_NOTICE),
                         [{"+0x78": combat.KILLED_BY, "+0x7C": {"+0x0": genji.body}}])
        self.assertFalse(shooter.client.messages(combat.HIT))
        self.assertFalse(root.state(6).children)
