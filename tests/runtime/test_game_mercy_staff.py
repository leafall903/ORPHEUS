"""Actual friendly staff healing through input, authored selection and real pools.

These cover reconstructed host geometry/bindings, not native settings, external
effect script mounting, healing modifiers, Valkyrie or client acceptance.
"""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import Run, aim
from test_game_mover import FLOOR, wall

from ow174.game import bodytrace, combat, movelog, stats
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import PRACTICE_RANGE, Spawn, heroes
from ow174.game.script import expr
from ow174.game.script.regeneration import MERCY

SCENE = replace(PRACTICE_RANGE, spawns=(
    Spawn(0, (0.0, 1.0, 0.0), 0.0),
    Spawn(1, (0.0, 1.0, 5.0), 0.0),
    Spawn(2, (5.0, 1.0, 5.0), 0.0),
))


class MercyStaffTests(unittest.TestCase):
    def setUp(self):
        for diagnostic in (bodytrace, movelog):
            self.enterContext(patch.object(diagnostic, "ENABLED", False))
        self.run = Run(SCENE, ("Mercy", "Ally", "Enemy"))
        self.mercy, self.ally, self.enemy = self.run.players
        self.ally.team = self.mercy.team
        self.enemy.team = 1
        self.match = self.run.match
        self.match.bots.clear()
        self.match._pending_bot_scripts.clear()
        self.match.switch_hero(self.mercy, heroes()[MERCY])
        self.run.step({})
        self.fight = self.match.combat
        self.target = next(t for t in self.fight._targets() if t.owner is self.ally)
        self.vitals = self.fight.vitals_of(self.target)
        self.fight._damage(self.enemy, self.target, 100, False, None, self.run.frame)

    def step(self, count, *, target=None, buttons=FIRE, backwards=False):
        target = target or self.ally
        for _ in range(count):
            x, y, z = target.mover.position
            yaw, pitch = aim(self.fight._eye(self.mercy), (x, y + 1.0, z))
            if backwards:
                yaw = (yaw + 65536) % 65536 - 32768
            self.run.step({self.mercy: (yaw, pitch, buttons)})

    def test_primary_restores_fifty_five_per_second_and_counts_only_actual_healing(self):
        self.step(50)
        self.assertAlmostEqual(self.vitals.total, 144, places=4)
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING), 44, places=4)
        self.assertEqual(self.ally.body_script.component.entity_var(253).value(), self.vitals.total)
        self.step(200)
        self.assertEqual(self.vitals.total, 200)
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING), 100, places=4)

    def test_release_stops_healing_and_repress_resumes(self):
        self.step(10)
        before = self.vitals.total
        self.step(10, buttons=0)
        self.assertEqual(self.vitals.total, before)
        self.step(10)
        self.assertGreater(self.vitals.total, before)

    def test_enemy_and_missed_aim_do_not_receive_healing(self):
        enemy = next(t for t in self.fight._targets() if t.owner is self.enemy)
        self.fight._damage(self.mercy, enemy, 100, False, None, self.run.frame)
        self.step(10, target=self.enemy)
        self.assertEqual(self.vitals.total, 100)
        self.assertEqual(self.fight.vitals_of(enemy).total, 100)
        self.step(10, backwards=True)
        self.assertEqual(self.vitals.total, 100)
        self.assertEqual(self.mercy.stats.value(stats.HEALING), 0)

    def test_dead_friendly_body_does_not_receive_healing(self):
        self.fight._damage(self.enemy, self.target, 500, False, None, self.run.frame)
        self.step(30)
        self.assertTrue(self.vitals.dead)
        self.assertEqual(self.vitals.total, 0)
        self.assertEqual(self.mercy.stats.value(stats.HEALING), 0)

    def test_duplicate_input_and_outgoing_body_replacement_do_not_duplicate_healing(self):
        self.step(10)
        before = self.vitals.total
        self.mercy.take_commands([Command(self.run.frame, buttons=FIRE)])
        self.assertEqual(self.vitals.total, before)
        old = self.ally.body
        frame = self.run.frame
        self.match.switch_hero(self.ally, self.ally.hero)
        self.assertNotEqual(self.ally.body, old)
        self.assertEqual(self.fight.heal_target(self.mercy, old, 50, frame), 0)

    def test_staff_search_hands_a_real_entity_to_its_authored_hold_state(self):
        self.step(5)
        instance = next(i for i in self.mercy.body_script.component.instances.values()
                        if i.graph.index == 0x6B)
        self.assertEqual(instance.find_var(0, 375).value(), (expr.Entity(self.ally.body),))
        self.assertEqual(instance.find_var(0, 468).value(), (expr.Entity(self.ally.body),))
        self.assertTrue(instance.state(5).active)
        self.assertEqual(instance.state(5).payload()["targets"][0][0], self.ally.body)

    def test_free_for_all_keeps_every_other_body_hostile_to_staff(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=True)
        self.step(20)
        self.assertEqual(self.vitals.total, 100)

    def test_self_target_is_rejected_by_the_authoritative_heal_consumer(self):
        self.assertEqual(self.fight.heal_target(self.mercy, self.mercy.body, 50, self.run.frame), 0)

    def test_switch_to_blaster_ends_staff_healing(self):
        self.step(10)
        before = self.vitals.total
        self.run.frame += 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.mercy.take_commands([Command(self.run.frame, action=2)])
        self.step(40)
        self.assertEqual(self.vitals.total, before)

    def test_range_limit_blocks_acquisition_and_breaks_an_existing_beam(self):
        self.step(10)
        before = self.vitals.total
        self.ally.spawn = ((0.0, 1.0, 20.0), 0.0)
        self.ally.mover = self.ally.new_mover()
        self.step(10)
        self.assertEqual(self.vitals.total, before)

    def test_static_occlusion_breaks_an_existing_beam(self):
        self.step(10)
        before = self.vitals.total
        self.match.collision_world = World(FLOOR + wall(-5, 2.5, 5, 2.5))
        self.step(10)
        self.assertEqual(self.vitals.total, before)

    def test_caster_death_and_target_team_change_stop_healing(self):
        self.step(10)
        before = self.vitals.total
        self.ally.team = self.enemy.team
        self.step(10)
        self.assertEqual(self.vitals.total, before)
        self.ally.team = self.mercy.team
        caster = next(t for t in self.fight._targets() if t.owner is self.mercy)
        self.fight._damage(self.enemy, caster, 500, False, None, self.run.frame)
        self.step(10)
        self.assertEqual(self.vitals.total, before)

    def test_held_primary_reacquires_replacement_friendly_body(self):
        self.step(10)
        old = self.ally.body
        self.match.switch_hero(self.ally, self.ally.hero)
        target = next(t for t in self.fight._targets() if t.owner is self.ally)
        vitals = self.fight.vitals_of(target)
        self.fight._damage(self.enemy, target, 100, False, None, self.run.frame)
        self.step(30)
        self.assertNotEqual(self.ally.body, old)
        self.assertGreater(vitals.total, 100)

    def test_live_target_bindings_and_pending_healing_have_passive_storage(self):
        self.step(5)
        script = self.mercy.body_script
        before = snapshot_digest(export_body_vm(script))
        script.support_healing.append((self.run.frame, self.ally.body, 0.88, None))
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), before)
        script.support_healing.clear()
        self.assertEqual(snapshot_digest(export_body_vm(script)), before)
