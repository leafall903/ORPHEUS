"""D.Va's two bodies: she leaves a wrecked or self-destructing mech as the pilot, calls a new one and
boards it, and a dead pilot returns in her mech (docs/research/dva-forms.md).

The graphs decide when: the mech's eject message 016C, Self-Destruct's 3 s fuse and its damage by
distance, Call Mech's 0.2 s and 1.75 s, its 250 around the landing mech.
"""

import math
import unittest
from unittest.mock import patch

from test_game_mover import wall
from test_track_targets import GROUND, Arena

from ow174.game import combat, dva, stats
from ow174.game.content import heroes, spawn_form
from ow174.game.script import driver
from ow174.game.world import OP_DESTROY

FIRE, ULTIMATE = 0x01, 0x20
ORIGIN = (0.0, GROUND, 0.0)
SPAWN_LOCK = 70  # the mech's graph holds its abilities for its first second
FUSE, AFTER_BLAST = 188, 20  # 3 s, then 0.3 s to the node that destroys the mech
CALL, BOARD = 14, 110  # 0.2 s to the new mech, 1.75 s more to her being in it


class Field(Arena):
    """D.Va at `at` facing +Z, in her mech unless `pilot`, and one more player per (ally, position)."""

    def __init__(self, *others, walls=(), pilot: bool = False, at: tuple = ORIGIN) -> None:
        super().__init__(*others, walls=walls, hero=dva.HERO)
        hero = heroes()[dva.HERO]
        assert self.match.switch_hero(self.owner, hero if pilot else spawn_form(hero), at=(at, 0))
        self.steps(SPAWN_LOCK)

    @property
    def body(self) -> int:
        return self.owner.hero.body

    def charge(self) -> float:
        script = self.owner.body_script
        cost = script.component.entity_var(driver.ULT_COST, create=False).value()
        script.set_ult_charge(cost)
        return cost

    def ult(self) -> float:
        return self.owner.body_script.component.entity_var(driver.ULT_CHARGE, create=False).value()

    def health(self, player) -> float:
        fight = self.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.entity == player.body)).total

    def hit(self, amount: float) -> None:
        fight = self.match.combat
        target = next(t for t in fight._targets() if t.entity == self.owner.body)
        fight._damage(self.others[0], target, amount, False, None, self.run.frame)

    def destroyed(self) -> list[int]:
        return [item.entity for item in self.owner.client.entities if item.op == OP_DESTROY]


class PilotTests(unittest.TestCase):
    def test_a_pilot_on_foot_is_not_in_a_mech_and_fires_her_gun(self):
        field = Field(pilot=True)
        self.assertEqual(field.body, dva.PILOT)
        self.assertEqual(field.match.forms.loose, {})
        with patch.object(combat.Combat, "_shoot", autospec=True) as shoot:
            field.steps(60, FIRE)
        self.assertGreaterEqual(shoot.call_count, 3)
        self.assertEqual(field.body, dva.PILOT)
        self.assertIsNone(field.owner.body_script.component.fault)

    def test_her_screen_is_not_blacked_out(self):
        # 026A's Entry turns the boarding blackout on, and only her being stowed in a mech turns it off.
        field = Field(pilot=True)
        instances = field.owner.body_script.component.instances.values()
        graph = next(instance for instance in instances if instance.graph.index == dva.PILOT_GRAPH)
        self.assertIs(graph.vars[dva.BLACKOUT].value(), False)
        self.assertFalse(graph.state(graph.graph.node(193).state).active)  # its presenter


class CannonTests(unittest.TestCase):
    def test_a_shot_hits_with_every_pellet_and_kills_once(self):
        # 050A node 6: 11 pellets of 2 a shot, rings of 1, 4 and 6 in a 4 degree spread (var 243).
        field = Field((False, (0.0, GROUND, 6.0)))
        enemy = field.others[0]
        real = combat.Combat._damage
        with patch.object(combat.Combat, "_damage", autospec=True, side_effect=real) as damage:
            field.steps(9, FIRE)  # the first shot and only it
        self.assertEqual([call.args[3] for call in damage.call_args_list], [2.0] * 11)
        field.steps(120, FIRE)
        self.assertTrue(field.match.combat.is_dead(enemy))
        self.assertEqual(field.owner.stats.values.get(stats.ELIMINATIONS, 0), 1)


class EjectTests(unittest.TestCase):
    def test_a_wrecked_mech_leaves_the_pilot_alive_where_it_stood(self):
        field = Field((False, (0.0, GROUND, 30.0)), at=(3.0, GROUND, 4.0))
        mech = field.owner.body
        field.hit(5000.0)
        self.assertFalse(field.match.combat.is_dead(field.owner))
        field.steps(2)
        self.assertEqual(field.body, dva.PILOT)
        self.assertNotEqual(field.owner.body, mech)
        self.assertEqual(tuple(field.owner.mover.position), (3.0, GROUND, 4.0))
        self.assertFalse(field.match.combat.is_dead(field.owner))
        self.assertEqual(field.health(field.owner), 150.0)
        self.assertEqual(field.owner.stats.values.get(stats.DEATHS, 0), 0)
        self.assertIn(mech, field.destroyed())  # a wreck goes at once
        self.assertEqual(field.match.forms.loose, {})

    def test_a_dead_pilot_returns_in_her_mech(self):
        field = Field((False, (0.0, GROUND, 30.0)), pilot=True)
        field.hit(5000.0)
        self.assertTrue(field.match.combat.is_dead(field.owner))
        field.steps(int(combat.RESPAWN / combat.TICK) + 20)
        self.assertFalse(field.match.combat.is_dead(field.owner))
        self.assertEqual(field.body, dva.MECH)
        self.assertEqual(field.health(field.owner), 600.0)


class SelfDestructTests(unittest.TestCase):
    NEAR, FAR = (0.0, GROUND, 3.0), (0.0, GROUND, -12.0)

    def field(self, *others, walls=()) -> Field:
        field = Field(*others, walls=walls)
        field.charge()
        field.steps(1, ULTIMATE)
        field.steps(2)
        return field

    def test_the_ultimate_ejects_her_and_the_mech_stays_for_its_fuse(self):
        field = Field()
        mech = field.owner.body
        cost = field.charge()
        self.assertEqual(cost, 1540.0)  # Self-Destruct's; Call Mech's is the pilot's own
        field.steps(1, ULTIMATE)
        field.steps(2)
        self.assertEqual(field.body, dva.PILOT)
        self.assertEqual(tuple(field.owner.mover.position), ORIGIN)
        self.assertEqual(list(field.match.forms.loose), [mech])
        self.assertNotIn(mech, field.destroyed())
        field.steps(FUSE - 10)
        self.assertEqual(list(field.match.forms.loose), [mech])
        self.assertLess(field.ult(), 100.0)
        field.steps(10 + AFTER_BLAST)
        self.assertEqual(field.match.forms.loose, {})
        self.assertIn(mech, field.destroyed())
        self.assertEqual(field.ult(), field.charge())  # the blast leaves Call Mech ready

    def test_the_blast_hits_by_distance(self):
        field = self.field((False, self.NEAR), (False, self.FAR))
        near, far = field.others
        field.steps(FUSE - 10)
        self.assertEqual((field.health(near), field.health(far)), (200.0, 200.0))
        with patch.object(combat.Combat, "_damage", autospec=True) as damage:
            field.steps(10 + AFTER_BLAST)
        dealt = {call.args[2].entity: call.args[3] for call in damage.call_args_list}
        point = (0.0, GROUND + 1.3, 0.0)
        gap = math.dist(point, self.FAR)
        self.assertAlmostEqual(dealt[near.body], 1000.0, places=3)  # inside 4 m
        self.assertAlmostEqual(dealt[far.body], 50.0 + 850.0 * (20.0 - gap) / 16.0, places=3)
        self.assertTrue(all(call.args[1] is field.owner for call in damage.call_args_list))

    def test_the_blast_spares_allies_cover_and_what_is_out_of_reach(self):
        cover = wall(-2.0, 5.0, 2.0, 5.0)
        for others in (((True, self.NEAR), (False, (0.0, GROUND, 8.0))), ((False, (0.0, GROUND, -25.0)),)):
            field = self.field(*others, walls=cover)
            with patch.object(combat.Combat, "_damage", autospec=True) as damage:
                field.steps(FUSE + AFTER_BLAST)
            self.assertEqual(damage.call_args_list, [])

    def test_a_kill_by_the_blast_is_hers(self):
        field = self.field((False, self.NEAR))
        field.steps(FUSE + AFTER_BLAST)
        self.assertTrue(field.match.combat.is_dead(field.others[0]))
        self.assertEqual(field.owner.stats.values.get(stats.ELIMINATIONS, 0), 1)


class CallMechTests(unittest.TestCase):
    def test_the_ultimate_calls_a_mech_and_she_boards_it(self):
        spot = (1.0, GROUND, 0.5)
        field = Field((False, (0.0, GROUND, 2.0)), (False, (0.0, GROUND, 6.0)), pilot=True, at=spot)
        beside, away = field.others
        pilot = field.owner.body
        field.charge()
        field.steps(1, ULTIMATE)
        field.steps(CALL)
        (called,) = field.match.forms.loose.values()
        self.assertEqual(called.position, spot)
        self.assertIsNone(called.script)
        self.assertEqual(field.body, dva.PILOT)
        # Her client turns her aim to the mech's facing as she boards: it reads that from the mech's
        # movement state, so the standing mech gets records once its create went out.
        def records() -> list:
            sent = field.match.remote_movements(field.owner)
            return [item for item in sent if item.entity == called.entity]

        self.assertEqual(records(), [])
        field.match.sent_updates(field.owner, list(field.owner.client.entities))
        (record,) = records()
        self.assertEqual((tuple(record.movement.position), record.movement.yaw), (spot, called.yaw))
        with patch.object(combat.Combat, "_damage", autospec=True, side_effect=combat.Combat._damage) as hit:
            field.steps(60)  # 0.7 s after it is made the mech hits what stands within 2.5 m: 250
        self.assertEqual([(call.args[2].entity, call.args[3]) for call in hit.call_args_list],
                         [(beside.body, 250.0)])  # once, though the search runs on
        self.assertTrue(field.match.combat.is_dead(beside))
        self.assertEqual(field.health(away), 200.0)
        field.steps(BOARD - 60)
        self.assertEqual(field.body, dva.MECH)
        self.assertEqual(tuple(field.owner.mover.position), spot)
        self.assertEqual(field.match.forms.loose, {})
        self.assertIn(pilot, field.destroyed())
        self.assertIn(called.entity, field.destroyed())  # she plays a new entity on its spot
        self.assertNotEqual(field.owner.body, called.entity)
        self.assertEqual(field.health(field.owner), 600.0)
        self.assertIsNone(field.owner.body_script.component.fault)

    def test_a_pilot_killed_while_calling_loses_the_called_mech(self):
        field = Field((False, (0.0, GROUND, 30.0)), pilot=True)
        field.charge()
        field.steps(1, ULTIMATE)
        field.steps(CALL)
        (called,) = field.match.forms.loose
        field.hit(5000.0)
        field.steps(5)
        self.assertEqual(field.match.forms.loose, {})
        self.assertIn(called, field.destroyed())


if __name__ == "__main__":
    unittest.main()
