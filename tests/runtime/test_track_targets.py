"""Target searches on the server: Guardian Angel flies Mercy to the ally she aims at, her staff locks and
heals one, Sombra's Hack takes the enemy she aims at.

The search is the client's own (docs/research/track-targets.md): from the eye along the aim, the filter's
masks, the cone, clear sight, and the best score where one target is asked for.
"""

import math
import unittest
from unittest.mock import patch

from test_game_combat import Run
from test_game_mover import FLOOR, wall

from ow174.game import combat, match, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.expr import Entity

MERCY, SOMBRA = 0x02E0000000000004, 0x02E000000000012E
GUARDIAN_ANGEL, STAFF, HACK = 0x006E, 0x006B, 0x0B1B
TARGET = 375  # the three graphs keep their target in #375
FIRE, SECONDARY, ABILITY_1 = 0x01, 0x02, 0x08
GROUND = 1.0  # FLOOR's top
# The filters as authored: any, all and excluded entity bits, then any, required and excluded relations.
GUARDIAN_ANGEL_FILTER = (0, 1, 0x200000, 1, 0, 4)
STAFF_FILTER = (1, 0, 0x200010, 0, 1, 4)
HOSTILE_FILTER = (0x40, 0, 0x14, 0, 0, 1)
LIVE, DEAD = combat.HERO_BIT, combat.HERO_BIT | combat.DEAD_BIT


def degrees(angle: float) -> int:
    """A command's yaw: 65536 to the turn, 0 facing +Z, positive toward +X."""
    return round(angle * 65536 / 360)


class Arena:
    """The hero (Mercy unless named) at the origin facing +Z, and one more player per (ally, position)."""

    def __init__(self, *others, walls=(), hero=MERCY) -> None:
        self.run = Run(names=tuple("ABC"[: 1 + len(others)]))
        self.match = self.run.match
        self.owner, *self.others = self.run.players
        assert self.match.switch_hero(self.owner, heroes()[hero])
        self.match.bots.clear()
        world = World(FLOOR + list(walls))
        self.match.collision_world = world
        places = ((True, (0.0, GROUND, 0.0)), *others)
        for player, (ally, position) in zip(self.run.players, places, strict=True):
            player.team = self.owner.team if ally else 1 - self.owner.team
            player.mover.use(world=world)
            player.mover.position[:] = position
        self.steps(30)

    def steps(self, count: int, buttons: int = 0, yaw: int = 0) -> None:
        for _ in range(count):
            self.run.frame += 1
            self.match.update(self.run.frame * combat.TICK, self.run.frame)
            self.owner.take_commands([Command(self.run.frame, yaw=yaw, buttons=buttons)])
            for other in self.others:
                other.take_commands([Command(self.run.frame)])

    def value(self, graph: int, var: int):
        instances = self.owner.body_script.component.instances.values()
        found = next(i for i in instances if i.graph.index == graph).vars.get(var)
        return found.value() if found is not None else None

    def target(self, graph: int) -> tuple:
        return tuple(self.value(graph, TARGET) or ())

    def fly(self, yaw: int = 0) -> float:
        """Hold Shift for a second: how far Mercy went."""
        start = tuple(self.owner.mover.position)
        self.steps(60, ABILITY_1, yaw)
        assert self.owner.body_script.component.fault is None
        return math.dist(start, self.owner.mover.position)


class FilterTests(unittest.TestCase):
    def test_the_authored_masks(self):
        cases = (
            # filter, bits, hostile, own, admitted
            (GUARDIAN_ANGEL_FILTER, LIVE, False, False, True),
            (GUARDIAN_ANGEL_FILTER, DEAD, False, False, True),  # its condition decides about the dead
            (GUARDIAN_ANGEL_FILTER, LIVE, True, False, False),
            (GUARDIAN_ANGEL_FILTER, LIVE, False, True, False),
            (GUARDIAN_ANGEL_FILTER, LIVE | 0x200000, False, False, False),
            (GUARDIAN_ANGEL_FILTER, 0, False, False, False),
            (STAFF_FILTER, LIVE, False, False, True),
            (STAFF_FILTER, DEAD, False, False, False),
            (STAFF_FILTER, LIVE, True, False, False),
            (STAFF_FILTER, LIVE, False, True, False),
            (HOSTILE_FILTER, 0x40, True, False, True),
            (HOSTILE_FILTER, 0x40, False, False, False),
            (HOSTILE_FILTER, LIVE, True, False, False),
            ((combat.ANY_ENTITY, 0, 0x94, 0, 0, 4), 0, True, False, True),  # Hack's: bits or none
            ((combat.ANY_ENTITY, 0, 0x94, 0, 0, 4), 0x10, True, False, False),
        )
        for masks, bits, hostile, own, admitted in cases:
            with self.subTest(masks=masks, bits=bits, hostile=hostile, own=own):
                self.assertEqual(combat.filter_admits(masks, bits, hostile, own), admitted)


class VolumeTests(unittest.TestCase):
    EYE = (0.0, GROUND + 1.55, 0.0)
    AHEAD = (0.0, 0.0, 1.0)

    def inside(self, feet, radius=30.0, aperture=30.0) -> bool:
        target = combat.Target(1, 0, feet, 1.8, 0.4, 1.55, None)
        limit = math.cos(math.radians(aperture / 2))
        return combat._in_search_volume(self.EYE, self.AHEAD, radius, limit, target)

    def test_range_counts_the_body_radius(self):
        self.assertTrue(self.inside((0.0, GROUND, 30.3)))
        self.assertFalse(self.inside((0.0, GROUND, 30.5)))

    def test_cone_is_half_the_aperture_to_the_body_surface(self):
        def at(angle, distance=10.0):
            turn = math.radians(angle)
            return (distance * math.sin(turn), GROUND, distance * math.cos(turn))

        self.assertTrue(self.inside(at(14.0)))
        self.assertTrue(self.inside(at(17.0)))  # the capsule's near side is inside 15 degrees
        self.assertFalse(self.inside(at(18.0)))
        self.assertFalse(self.inside(at(180.0)))
        self.assertTrue(self.inside(at(180.0), aperture=360.0))

    def test_a_body_the_aim_passes_through_is_inside_any_cone(self):
        self.assertTrue(self.inside((0.3, GROUND, 1.0), aperture=0.0))
        self.assertFalse(self.inside((0.3, GROUND, -1.0), aperture=0.0))


class GuardianAngelTests(unittest.TestCase):
    def test_shift_at_an_ally_flies_mercy_to_him(self):
        arena = Arena((True, (0.0, GROUND, 10.0)))
        self.assertGreater(arena.fly(), 5.0)
        ally = arena.others[0]
        self.assertEqual(arena.target(GUARDIAN_ANGEL), (Entity(ally.body),))
        gap = math.dist(arena.owner.mover.position, ally.mover.position)
        self.assertTrue(2.0 < gap < 4.0, gap)  # the graph stops her short of him

    def test_no_flight_without_a_target(self):
        cases = {
            "nobody": (),
            "an enemy": ((False, (0.0, GROUND, 10.0)),),
            "an ally outside the cone": ((True, (7.0, GROUND, 7.0)),),
            "an ally past the range": ((True, (0.0, GROUND, 31.0)),),
            "an ally behind her": ((True, (0.0, GROUND, -10.0)),),
        }
        for name, others in cases.items():
            with self.subTest(name):
                arena = Arena(*others)
                self.assertLess(arena.fly(), 0.01)
                self.assertEqual(arena.target(GUARDIAN_ANGEL), ())

    def test_an_ally_behind_a_wall_is_no_target(self):
        blocked = wall(-5.0, 5.0, 5.0, 5.0, y0=GROUND, y1=GROUND + 6.0)
        arena = Arena((True, (0.0, GROUND, 10.0)), walls=blocked)
        self.assertLess(arena.fly(), 0.01)
        self.assertEqual(arena.target(GUARDIAN_ANGEL), ())

    def test_turning_to_the_ally_finds_him(self):
        arena = Arena((True, (7.0, GROUND, 7.0)))
        self.assertGreater(arena.fly(degrees(45)), 4.0)

    def test_the_ally_nearest_the_crosshair_wins_over_the_nearer_one(self):
        aimed, close = (0.0, GROUND, 20.0), (1.2, GROUND, 6.0)
        arena = Arena((True, aimed), (True, close))
        arena.steps(3, ABILITY_1)
        self.assertEqual(arena.target(GUARDIAN_ANGEL), (Entity(arena.others[0].body),))


class StaffTests(unittest.TestCase):
    def locked(self, *others, walls=()) -> tuple:
        arena = Arena(*others, walls=walls)
        arena.steps(40, FIRE)
        self.assertIsNone(arena.owner.body_script.component.fault)
        return arena, arena.target(STAFF)

    def test_fire_at_an_ally_locks_him(self):
        arena, target = self.locked((True, (0.0, GROUND, 6.0)))
        self.assertEqual(target, (Entity(arena.others[0].body),))

    def test_no_lock_without_a_target(self):
        cases = {
            "an enemy": ((False, (0.0, GROUND, 6.0)),),
            "an ally past the range": ((True, (0.0, GROUND, 16.0)),),
            "an ally outside the cone": ((True, (6.0, GROUND, 6.0)),),
        }
        for name, others in cases.items():
            with self.subTest(name):
                self.assertEqual(self.locked(*others)[1], ())

    def test_no_lock_through_a_wall(self):
        blocked = wall(-5.0, 3.0, 5.0, 3.0, y0=GROUND, y1=GROUND + 6.0)
        self.assertEqual(self.locked((True, (0.0, GROUND, 6.0)), walls=blocked)[1], ())

    def test_the_beam_heals_the_ally_at_the_staffs_own_rate(self):
        arena = Arena((True, (0.0, GROUND, 6.0)))
        ally = arena.others[0]
        vitals = arena.match.combat.body_vitals(ally.body, ally)
        vitals.damage(150.0)
        arena.steps(40, FIRE)  # the lock, then the beam
        before = vitals.total
        arena.steps(125, FIRE)  # two seconds
        self.assertAlmostEqual(vitals.total - before, 2 * 55.0, delta=2.0)  # 006B #6578
        self.assertGreater(arena.owner.stats.value(stats.HEALING), 100.0)

    def test_the_beam_does_not_heal_an_enemy_or_past_full_health(self):
        arena = Arena((False, (0.0, GROUND, 6.0)))
        vitals = arena.match.combat.body_vitals(arena.others[0].body, arena.others[0])
        vitals.damage(150.0)
        arena.steps(120, FIRE)
        self.assertAlmostEqual(vitals.total, 50.0)
        arena = Arena((True, (0.0, GROUND, 6.0)))
        vitals = arena.match.combat.body_vitals(arena.others[0].body, arena.others[0])
        arena.steps(120, FIRE)
        self.assertAlmostEqual(vitals.total, 200.0)


class HackTests(unittest.TestCase):
    """Graph 0B1B: the search (node 50), the hold on its target (node 52), the cast and the cooldown."""

    CAST, COOLDOWN = 670, 61  # the cast's length, and the cooldown's seconds left

    def hack(self, *others, walls=(), frames=10):
        arena = Arena(*others, walls=walls, hero=SOMBRA)
        arena.steps(frames, SECONDARY)
        self.assertIsNone(arena.owner.body_script.component.fault)
        return arena

    def test_secondary_fire_at_an_enemy_takes_him(self):
        arena = self.hack((False, (0.0, GROUND, 8.0)))
        self.assertEqual(arena.target(HACK), (Entity(arena.others[0].body),))

    def test_no_target(self):
        cases = {
            "an ally": ((True, (0.0, GROUND, 8.0)),),
            "an enemy past the range": ((False, (0.0, GROUND, 16.0)),),
            "an enemy outside the cone": ((False, (8.0, GROUND, 8.0)),),
        }
        for name, others in cases.items():
            with self.subTest(name):
                self.assertEqual(self.hack(*others).target(HACK), ())

    def test_no_target_through_a_wall(self):
        blocked = wall(-5.0, 4.0, 5.0, 4.0, y0=GROUND, y1=GROUND + 6.0)
        self.assertEqual(self.hack((False, (0.0, GROUND, 8.0)), walls=blocked).target(HACK), ())

    def test_the_cast_takes_the_graphs_time_and_then_the_cooldown_runs(self):
        arena = self.hack((False, (0.0, GROUND, 8.0)))
        cast = arena.value(HACK, self.CAST)
        self.assertGreater(cast, 0.3)
        self.assertFalse(arena.value(HACK, self.COOLDOWN))
        frames = 10
        while not arena.value(HACK, self.COOLDOWN) and frames < 200:
            arena.steps(1, SECONDARY)
            frames += 1
        # From the press: the search, the hold on the target, then the cast.
        self.assertAlmostEqual(frames * combat.TICK, cast, delta=0.25)
        self.assertGreater(arena.value(HACK, self.COOLDOWN), 1.0)

    def test_letting_go_before_the_cast_ends_starts_no_cooldown(self):
        arena = self.hack((False, (0.0, GROUND, 8.0)))
        arena.steps(120)
        self.assertFalse(arena.value(HACK, self.COOLDOWN))


class BodyFilterBitsTests(unittest.TestCase):
    """The client's own searches gather bodies from its list of entities with filter bit 0: without it
    the staff's beam target and Guardian Angel's own search find nobody (seen in the client)."""

    def components(self, build) -> dict:
        with patch.object(match.world, "create") as create:
            build(0)
        return create.call_args.args[2]

    def test_player_and_bot_bodies_carry_bit_0_and_their_team(self):
        run = Run()
        player = run.players[0]
        bits = self.components(run.match._body_builder(player, None))[26]
        self.assertEqual(bits, [combat.HERO_BIT | match.TEAM_BIT << player.team])
        bot = run.match.bots[0]
        bits = self.components(run.match._bot_create_update(bot).build)[26]
        self.assertEqual(bits, [combat.HERO_BIT | match.TEAM_BIT << bot.team])


if __name__ == "__main__":
    unittest.main()
