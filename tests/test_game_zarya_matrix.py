"""Zarya against actual explicit0407 Matrix commands; attachment/filter are reconstructed."""

import math
import os
import unittest
from dataclasses import replace
from itertools import product
from unittest.mock import patch

from test_game_combat import Run, aim
from test_game_mover import FLOOR, box
from test_game_zarya_absorption import queue_sleep_wake_and_defense

from ow174.game import combat, defenses, dva, interception, phase, stats, zarya
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.mover import FLIGHT_ROLE, defined_mover_data
from ow174.game.physics_snapshot import snapshot_mover
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

MECH = 0x0400000000000407


@unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS")
                    and os.environ.get("ORPHEUS_GRAPH_SUPPLEMENT")
                    and os.environ.get("ORPHEUS_MOVERS_SUPPLEMENT"), "requires actual mech/tag inputs")
class ZaryaMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if graph.entity_body(MECH) is None or defined_mover_data(MECH) is None:
            raise AssertionError("explicit0407 body/mover is required;pilot/fallback is not acceptance")

    @staticmethod
    def matrix(player):
        return next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x02CC)

    @staticmethod
    def health(run, player):
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run.match.combat.vitals_of(target).total

    def scene(self, *, enabled=True, facing=-32768, close=False, ally=False, nearer=False, flash=False):
        run = Run(names=("Zarya", "Mech", "Victim"))
        a, b, c = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(a, heroes()[0x02E0000000000068]))
        self.assertTrue(run.match.switch_hero(b, replace(heroes()[0x02E000000000007A], body=MECH)))
        a.team, b.team, c.team = 0, 0 if ally else 1, 1
        a.mover.position[:] = [0, 1, 0]
        b.mover.position[:] = [0, 1, 1.5 if close else 15]
        c.mover.position[:] = [20, 1, 15]
        if nearer:
            c.mover.position[:] = [0, 1, 3]
        if flash:
            run.match.switch_hero(c, heroes()[0x02E0000000000042])
            c.team = a.team
            c.mover.position[:] = [0, 1, 18]
        a.body_script.ult_seconds = 0
        for _ in range(100):
            run.step({b: (facing, 0, 0)})
        for _ in range(5):
            run.step({b: (facing, 0, 2 if enabled else 0)})
        self.assertEqual(self.health(run, b), 600)
        self.assertEqual(b.hero.body, MECH)
        self.assertEqual(b.mover.data.stand_radius, 0.7)
        self.assertEqual(self.matrix(b).state(3).active, enabled)
        return run, a, b, c

    @staticmethod
    def step(run, b, *, enabled=True, a=None, buttons=0, frames=1):
        for _ in range(frames):
            commands = {p: (p.mover.yaw, p.mover.pitch, 0) for p in run.players}
            commands[b] = (b.mover.yaw, b.mover.pitch, 2 if enabled else 0)
            if a is not None:
                commands[a] = (a.mover.yaw, a.mover.pitch, buttons)
            run.step(commands)

    def shoot(self, run, a, b, *, ultimate=False):
        if ultimate:
            a.body_script.set_ult_charge(2100)
        self.step(run, b, a=a, buttons=32 if ultimate else 2)

    def test_booster_contact_and_knockback_respect_personal_and_projected_barriers(self):
        for barrier in (None, "personal", "projected", "break"):
            with self.subTest(barrier=barrier):
                run, target, mech, caster = self.scene(enabled=False, facing=0)
                match = run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(caster, heroes()[zarya.HERO]))
                caster.team = target.team
                x, y, z = mech.mover.position
                target.mover.position[:] = [x, y, z + 2]
                caster.mover.position[:] = [x + 6, y, z + 2]
                target.mover.body = None
                caster.mover.body = None
                for _ in range(40):
                    run.step({})
                recipient = target if barrier == "personal" else caster
                if barrier == "personal":
                    run.step({target: (0, 0, 8)})
                    self.assertTrue(fight.zarya_barriers.protects(target))
                    pool = fight.zarya_barriers.active[target.body]
                elif barrier:
                    angles = aim(fight._eye(caster), fight._eye(target))
                    run.step({caster: (*angles, 16)})
                    self.assertTrue(fight.zarya_barriers.protects(target))
                    pool = fight.zarya_barriers.projected[caster.body]
                    if barrier == "break":
                        pool.remaining = 5
                before = self.health(run, target)
                graph = next(i for i in mech.body_script.component.instances.values()
                             if i.graph.index == dva.BOOSTERS)
                for _ in range(35):
                    run.step({mech: (0, 0, 8)})
                    contact = fight.booster_contacts.get(mech.body)
                    if contact is not None and target.body in contact[2]:
                        break
                self.assertTrue(graph.state(6).active)
                self.assertTrue(graph.state(7).active)
                self.assertIsNotNone(contact)
                self.assertIn(target.body, contact[2])
                if barrier is None:
                    self.assertEqual(before - self.health(run, target), 10)
                    self.assertGreater(target.mover.velocity[2], 0)
                    self.assertTrue(mech.client.messages(combat.HIT))
                else:
                    self.assertEqual(pool.remaining, 0 if barrier == "break" else 190)
                    self.assertEqual(fight.zarya_barriers.protects(target), barrier != "break")
                    self.assertEqual(self.health(run, target), before)
                    self.assertEqual(target.mover.velocity[2], 0)
                    self.assertFalse(mech.client.messages(combat.HIT))
                    run.step({})
                    energy = recipient.body_script.component.entity_var(87).value()
                    self.assertAlmostEqual(energy, 1 if barrier == "break" else 2)
                if barrier == "break":
                    x, y, z = mech.mover.position
                    target.mover.position[:] = (x, y, z + 5)
                    target.mover.body = None
                    for _ in range(32):
                        run.step({mech: (0, 0, 8)})
                    self.assertIs(fight.booster_contacts.get(mech.body), contact)
                    self.assertGreater(mech.mover.position[2], z + 5)
                    self.assertEqual(self.health(run, target), before)
                    self.assertEqual(target.mover.position[2], z + 5)
                    ability = graph.state(0)
                    for _ in range(700):
                        run.step({})
                        if not ability.flags & ability.RUNNING and ability.remaining() == 0:
                            break
                    self.assertNotIn(mech.body, fight.booster_contacts)
                    self.assertFalse(ability.flags & ability.RUNNING)
                    self.assertEqual(ability.remaining(), 0)
                    x, y, z = mech.mover.position
                    target.mover.position[:] = (x, y, z + 2)
                    target.mover.body = None
                    for _ in range(30):
                        run.step({mech: (0, 0, 8)})
                        if self.health(run, target) < before:
                            break
                    restarted = fight.booster_contacts.get(mech.body)
                    self.assertIsNotNone(restarted, (ability.flags, ability.remaining(), ability.buttons()))
                    self.assertNotEqual(restarted[:2], contact[:2])
                    self.assertEqual(before - self.health(run, target), 10)

    def test_boosters_can_hit_only_the_personal_or_projected_barrier_edge(self):
        cases = ("personal", "projected", "break", "outside", "covered", "allied")
        for case in cases:
            with self.subTest(case=case):
                run, target, mech, caster = self.scene(enabled=False, facing=0)
                match = run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(caster, heroes()[zarya.HERO]))
                if case == "allied":
                    target.team = mech.team
                caster.team = target.team
                x, y, z = mech.mover.position
                target.mover.position[:] = (x + (3.25 if case == "outside" else 2.4), y, z + 2)
                caster.mover.position[:] = (x + 6, y, z + 2)
                target.mover.body = caster.mover.body = None
                if case == "personal":
                    run.step({target: (0, 0, 8)})
                    self.assertTrue(fight.zarya_barriers.protects(target))
                    pool = fight.zarya_barriers.active[target.body]
                else:
                    angles = aim(fight._eye(caster), fight._eye(target))
                    run.step({caster: (*angles, 16)})
                    self.assertTrue(fight.zarya_barriers.protects(target))
                    pool = fight.zarya_barriers.projected[caster.body]
                    if case == "break":
                        pool.remaining = 5
                if case == "covered":
                    wall = z + 0.9
                    match.collision_world = World([
                        (-50, y, -50, -50, y, 100, 50, y, 100),
                        (-50, y, -50, 50, y, 100, 50, y, -50),
                        (-10, 0, wall, 10, 0, wall, 10, 10, wall),
                        (-10, 0, wall, 10, 10, wall, -10, 10, wall),
                    ])
                before = self.health(run, target)
                boost = {mech: (0, 0, 8)}
                run.step(boost)
                track = dva.boosters(mech).state(6)
                offset = track.evaluate(track.node.field("m_05BE62DD"), watch=False)
                radius = track.evaluate(track.node.field("m_radius"), watch=False)
                x, y, z = mech.mover.position
                centre = (x + offset.x, y + offset.y, z + offset.z)
                body = next(t for t in fight._targets() if t.owner is target)
                self.assertGreater(combat.segment_distance(centre, *body.segment()), radius + body.radius)
                bubble = (body.feet[0], body.feet[1] + body.height / 2, body.feet[2])
                self.assertEqual(math.dist(centre, bubble) <= radius + zarya.BUBBLE_RADIUS,
                                 case != "outside")
                activation = fight.booster_contacts[mech.body]
                absorbed = case in ("personal", "projected", "break")
                self.assertEqual(target.body in activation[2], absorbed)
                self.assertEqual(pool.remaining, 0 if case == "break" else 190 if absorbed else 200)
                self.assertEqual(self.health(run, target), before)
                self.assertEqual(target.mover.velocity[2], 0)
                self.assertFalse(mech.client.messages(combat.HIT))
                if case == "break":
                    target.mover.position[:] = (x, y, z + 2)
                    target.mover.body = None
                    run.step(boost)
                    self.assertIs(fight.booster_contacts[mech.body], activation)
                    self.assertEqual(self.health(run, target), before)
                    self.assertFalse(mech.client.messages(combat.HIT))
                run.step({})
                owner = target if case == "personal" else caster
                energy = owner.body_script.component.entity_var(87).value() or 0
                self.assertAlmostEqual(energy, 1 if case == "break" else 2 if absorbed else 0)

    def test_pitched_boosters_reach_elevated_body_or_barrier(self):
        for case in ("level", "body", "barrier", "ceiling"):
            with self.subTest(case=case):
                run, target, mech, _ = self.scene(enabled=False, facing=0)
                fight = run.match.combat
                x, y, z = mech.mover.position
                target.mover.position[:] = (x, y + 5, z + 6)
                target.mover.body = None
                if case == "ceiling":
                    run.match.collision_world = World(FLOOR + box(-20, 20, y + 3, y + 4, -20, 29))
                if case == "barrier":
                    run.step({target: (0, 0, 8)})
                    self.assertTrue(fight.zarya_barriers.protects(target))
                    pool = fight.zarya_barriers.active[target.body]
                before = self.health(run, target)
                pitch = 0 if case == "level" else -8192
                boost = {mech: (0, pitch, 8)}
                run.step(boost)
                role, = (mod for mod in mech.mover.mods.values() if mod.index == FLIGHT_ROLE)
                self.assertEqual((role.value, role.start), (0, run.frame + 1))
                for _ in range(30):
                    run.step(boost)
                    activation = fight.booster_contacts[mech.body]
                    if target.body in activation[2]:
                        break
                self.assertEqual(target.body in activation[2], case in ("body", "barrier"))
                self.assertEqual(before - self.health(run, target), 10 if case == "body" else 0)
                self.assertAlmostEqual(mech.mover.values.accel_air, 35)
                self.assertIsNotNone(mech.mover.override_data)
                if case == "body":
                    frozen = snapshot_mover(mech.mover)
                    self.assertEqual(snapshot_mover(mech.mover), frozen)
                if case == "level":
                    self.assertAlmostEqual(mech.mover.position[1], y, delta=0.05)
                elif case == "ceiling":
                    capsule = mech.mover.body
                    self.assertLessEqual(capsule.position[1] + capsule.top + capsule.radius, y + 3.01)
                    self.assertAlmostEqual(mech.mover.velocity[1], 0, delta=0.002)
                else:
                    self.assertGreater(mech.mover.position[1], y + 1.5)
                    self.assertGreater(mech.mover.velocity[1], 5)
                if case == "barrier":
                    self.assertEqual(pool.remaining, 190)
                    self.assertFalse(mech.client.messages(combat.HIT))

    def test_downward_boosters_cancel_to_regular_gravity(self):
        run, _, mech, _ = self.scene(enabled=False, facing=0)
        mech.mover.position[:] = (0, 12, 0)
        mech.mover.body = None
        run.step({mech: (0, 8192, 8)})
        role, = (mod for mod in mech.mover.mods.values() if mod.index == FLIGHT_ROLE)
        for _ in range(41):
            run.step({mech: (0, 8192, 0)})
        self.assertLess(mech.mover.position[1], 7)
        self.assertGreater(mech.mover.position[2], 5)
        self.assertLess(mech.mover.velocity[1], -8)
        run.step({mech: (0, 8192, 8)})
        self.assertFalse(dva.boosters(mech).state(2).active)
        self.assertEqual(role.end, run.frame + 1)
        before = mech.mover.velocity[1]
        run.step({mech: (0, 8192, 0)})
        self.assertIsNone(mech.mover.override_data)
        self.assertLess(mech.mover.velocity[1], before)

    def test_boosters_recontact_after_phase_but_not_after_cryo_in_same_activation(self):
        for hero, mode in ((0x2, "phase"), (0x1A2, "phase"), (0xDD, "cryo")):
            with self.subTest(hero=hero):
                run, target, mech, _ = self.scene(enabled=False, facing=0)
                match = run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(target, heroes()[0x02E0000000000000 | hero]))
                x, y, z = mech.mover.position
                target.mover.position[:] = (x, y, z + 2)
                target.mover.body = None
                run.step({target: (0, 0, 8)})
                self.assertEqual(phase.protection(target), mode)
                deadline = target.body_script.world.phase_deadline
                while run.frame < deadline - 20:
                    run.step({})
                boost = {mech: (0, 0, 8)}
                before = self.health(run, target)
                run.step(boost)
                self.assertEqual(phase.protection(target), mode)
                activation = fight.booster_contacts.get(mech.body)
                self.assertIsNotNone(activation)
                self.assertEqual(target.body in activation[2], mode == "cryo")
                self.assertEqual(self.health(run, target), before)
                self.assertEqual(target.mover.velocity[2], 0)
                self.assertFalse(mech.client.messages(combat.HIT))

                while run.frame < deadline:
                    run.step(boost)
                self.assertIsNone(phase.protection(target))
                x, y, z = mech.mover.position
                target.mover.position[:] = (x, y, z + 2)
                target.mover.body = None
                run.step(boost)
                self.assertIs(fight.booster_contacts.get(mech.body), activation)
                self.assertIn(target.body, activation[2])
                damage = 10 if mode == "phase" else 0
                self.assertEqual(before - self.health(run, target), damage)
                self.assertEqual(target.mover.velocity[2] > 0, bool(damage))
                self.assertEqual(bool(mech.client.messages(combat.HIT)), bool(damage))

    def test_boosters_contact_requires_reach_and_static_or_cryo_line_of_sight(self):
        for cover in ("lateral", "static", "enemy_cryo", "allied_cryo"):
            with self.subTest(cover=cover):
                run, target, mech, blocker = self.scene(enabled=False, facing=0)
                match = run.match
                fight = match.combat
                x, y, z = mech.mover.position
                target.mover.position[:] = (x + (3 if cover == "lateral" else 0), y, z + 2.75)
                target.mover.body = None
                if cover.endswith("cryo"):
                    self.assertTrue(match.switch_hero(blocker, heroes()[0x02E00000000000DD]))
                    blocker.team = mech.team if cover == "allied_cryo" else target.team
                    blocker.mover.position[:] = (x, y, z + 1.5)
                    blocker.mover.body = None
                    run.step({blocker: (0, 0, 8)})
                    self.assertEqual(phase.protection(blocker), "cryo")
                    for _ in range(24):
                        run.step({})
                elif cover == "static":
                    wall = z + 1.5
                    original_world = match.collision_world
                    match.collision_world = World([
                        (-50, y, -50, -50, y, 100, 50, y, 100),
                        (-50, y, -50, 50, y, 100, 50, y, -50),
                        (-10, 0, wall, 10, 0, wall, 10, 10, wall),
                        (-10, 0, wall, 10, 10, wall, -10, 10, wall),
                    ])
                before = self.health(run, target)
                boost = {mech: (0, 0, 8)}
                run.step(boost)
                activation = fight.booster_contacts.get(mech.body)
                self.assertIsNotNone(activation)
                self.assertNotIn(target.body, activation[2])
                self.assertEqual(self.health(run, target), before)
                self.assertFalse(mech.client.messages(combat.HIT))
                if cover.endswith("cryo"):
                    self.assertEqual(blocker.body in activation[2], cover == "enemy_cryo")
                    run.step({**boost, blocker: (0, 0, 1)})
                    self.assertIsNone(phase.protection(blocker))
                elif cover == "static":
                    match.collision_world = original_world
                x, y, z = mech.mover.position
                target.mover.position[:] = (x, y, z + 2)
                target.mover.body = None
                run.step(boost)
                self.assertIs(fight.booster_contacts.get(mech.body), activation)
                self.assertIn(target.body, activation[2])
                self.assertEqual(before - self.health(run, target), 10)

    def test_boosters_respects_transcendence_and_fortify(self):
        for hero, expected in ((defenses.ZENYATTA, 0), (defenses.ORISA, 3)):
            with self.subTest(hero=hero):
                run, target, mech, _ = self.scene(enabled=False, facing=0)
                fight = run.match.combat
                self.assertTrue(run.match.switch_hero(target, heroes()[hero]))
                x, y, z = mech.mover.position
                target.mover.position[:] = (x, y, z + 2)
                target.mover.body = None
                if hero == defenses.ZENYATTA:
                    script = target.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    run.step({target: (0, 0, 32)})
                    self.assertTrue(defenses.invulnerable(target))
                else:
                    run.step({target: (0, 0, 8)})
                    self.assertAlmostEqual(defenses.fortify_scale(target), 0.6)
                before = self.health(run, target)
                run.step({mech: (0, 0, 8)})
                self.assertIn(target.body, fight.booster_contacts[mech.body][2])
                self.assertAlmostEqual(before - self.health(run, target), expected, places=4)
                self.assertEqual(target.mover.velocity, (0, 0, 0))
                self.assertEqual(bool(mech.client.messages(combat.HIT)), bool(expected))

    def test_matrix_absorbs_hostile_halt_and_keeps_zarya_out_of_the_pull(self):
        definition = 0x040000000000172C
        for ally in (False, True):
            with self.subTest(ally=ally):
                self.assertTrue(interception.eligible(definition))
                run, orisa, mech, victim = self.scene(ally=ally)
                self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                self.assertTrue(run.match.switch_hero(victim, heroes()[0x02E0000000000068]))
                orisa.mover.position[:] = [0, 1, 0]
                mech.mover.position[:] = [0, 1, 10]
                victim.mover.position[:] = [0, 1, 9]
                for player in (orisa, mech, victim):
                    player.mover.body = None
                fight = run.match.combat
                launched = False
                for frame in range(26):
                    run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0), mech: (-32768, 0, 2)})
                    launched |= any(rocket.definition == definition for rocket in fight.rockets)
                self.assertTrue(launched)
                self.assertEqual(bool(fight.rockets), ally)
                instance = next(i for i in orisa.body_script.component.instances.values()
                                if i.graph.index == 0x0CCB)
                if not ally:
                    self.assertGreater(instance.state(2).remaining(), 4)
                for _ in range(45):
                    run.step({mech: (-32768, 0, 2)})
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, mech), 600)
                self.assertEqual(self.health(run, victim), 400)
                if ally:
                    self.assertLess(victim.mover.position[2], 8.5)
                else:
                    self.assertEqual(victim.mover.position[2], 9)
                    self.assertNotIn(victim.body, fight.halt_pulls)

    def test_matrix_absorbs_ana_grenade_and_dart_before_damage_or_status(self):
        for button, definition in ((8, 0x04000000000015E8), (16, combat.ANA_GRENADE)):
            with self.subTest(button=button):
                self.assertTrue(interception.eligible(definition))
                run, ana, mech, zarya = self.scene()
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
                ana.mover.position[:] = [0, 1, 10]
                zarya.mover.position[:] = [0, 1, 18]
                fight = run.match.combat
                angles = aim(fight._eye(ana), fight._eye(mech))
                launched = False
                for frame in range(45):
                    run.step({ana: (*angles, button if frame == 0 else 0), mech: (-32768, 0, 2)})
                    launched |= any(rocket.definition == definition for rocket in fight.rockets)
                self.assertTrue(launched)
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, mech), 600)
                self.assertEqual(self.health(run, zarya), 400)
                for player in (mech, zarya):
                    self.assertNotIn(player.body, fight.sleeps)
                    self.assertEqual(fight.vitals[player.body].healing_block_until, 0)
                self.assertFalse(ana.client.messages(combat.HIT))

    def graviton_boosters_scene(self, offset, *, allied=False):
        run, caster, mech, anchor = self.scene(enabled=False, facing=0, ally=allied)
        fight = run.match.combat
        caster.mover.position[:] = (0, 1, 0)
        mech.mover.position[:] = (offset, 1, 6)
        anchor.mover.position[:] = (0, 1, 6)
        for player in run.players:
            player.mover.body = None
        caster.body_script.set_ult_charge(2100)
        run.step({caster: (0, 0, 32)})
        self.assertEqual(len(fight.rockets), 1)
        host = fight.zarya_gravitons
        for _ in range(80):
            run.step({})
            if host.fields:
                break
        self.assertEqual(len(host.fields), 1)
        instance = dva.boosters(mech)
        self.assertIsNotNone(instance)
        return run, mech, host, instance

    def test_graviton_interrupts_active_boosters_and_preserves_authored_cooldown(self):
        run, mech, host, instance = self.graviton_boosters_scene(8)
        fight = run.match.combat
        field, = host.fields
        self.assertIsNone(host.movement_for(mech))
        ability, flight, cleanup = (instance.state(index) for index in (0, 2, 5))
        boost = {mech: (-16384, -4096, 8)}
        run.step(boost)
        self.assertTrue(flight.active)
        initial_height = mech.mover.position[1]
        initial = flight.counter
        for _ in range(40):
            if host.movement_for(mech) is not None:
                break
            run.step(boost)
        self.assertIsNotNone(host.movement_for(mech))
        self.assertTrue(flight.active)
        self.assertGreater(mech.mover.position[1], initial_height)
        before = instance.component.frame
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        self.assertEqual(instance.component.frame, before)
        self.assertIn(mech.body, host.player_pulls)
        self.assertFalse(flight.active)
        self.assertFalse(instance.state(6).active)
        self.assertFalse(instance.state(7).active)
        self.assertFalse(any(key[:2] == (instance.id, flight.index) and mod.end is None
                             for key, mod in mech.mover.mods.items()))
        self.assertFalse(any(mod.index == FLIGHT_ROLE and mod.end is None
                             for mod in mech.mover.mods.values()))
        self.assertNotIn(mech.body, fight.booster_contacts)
        self.assertTrue(cleanup.active)
        self.assertFalse(fight.is_stunned(mech))
        self.assertEqual(ability.remaining(), 0)
        cooldown_start = cleanup.start + 300
        for _ in range(24):
            run.step(boost)
        self.assertIsNone(mech.mover.override_data)
        self.assertEqual(mech.mover.values.accel_air, mech.mover.data.accel_air)
        self.assertEqual(ability.last, cooldown_start)
        self.assertEqual(ability.cur, 4)
        for _ in range(field.end_tick - run.frame):
            run.step({})
        self.assertFalse(host.fields)
        self.assertGreater(ability.remaining(), 0)
        run.step(boost)
        self.assertFalse(flight.active)
        self.assertEqual(flight.counter, initial)
        for _ in range(100):
            run.step({})
            if ability.remaining() == 0:
                break
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(flight.counter, initial)
        run.step(boost)
        self.assertTrue(flight.active)
        self.assertEqual(flight.counter, initial + 1)

    def test_graviton_blocks_new_enemy_boosters_and_allows_allied_boosters(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, mech, host, instance = self.graviton_boosters_scene(3, allied=allied)
                field, = host.fields
                ability, flight = instance.state(0), instance.state(2)
                initial = flight.counter
                boost = {mech: (16384, 0, 8)}
                run.step(boost)
                self.assertEqual(host.movement_for(mech) is None, allied)
                self.assertEqual(flight.active, allied)
                self.assertEqual(flight.counter, initial + int(allied))
                self.assertEqual(ability.remaining(), 0)
                if allied:
                    continue
                for _ in range(field.end_tick - run.frame - 1):
                    run.step(boost)
                    self.assertEqual(flight.counter, initial)
                    self.assertEqual(ability.remaining(), 0)
                run.step(boost)
                self.assertFalse(host.fields)
                self.assertTrue(flight.active)
                self.assertEqual(flight.counter, initial + 1)

    def test_projected_bubble_and_active_defenses_resolve_in_flight_order(self):
        profiles = ((0x7A, 2, 0x02CC, 3), (0x23B, 8, 0x1D02, 1), (0x29, 16, 0x03A3, 10))
        cases = product(profiles, ("bubble", "defense", "unshielded"), (False, True))
        for (hero, defense, graph_index, active), case, ultimate in cases:
            with self.subTest(hero=hero, case=case, ultimate=ultimate):
                run = Run(names=("Attacker", "Defender", "Protected", "Projector"))
                attacker, defender, target, caster = run.players
                match = run.match
                fight = match.combat
                match.bots.clear()
                match._pending_bot_scripts.clear()
                zarya = heroes()[0x02E0000000000068]
                defender_hero = heroes()[0x02E0000000000000 | hero]
                if hero == 0x7A:
                    defender_hero = replace(defender_hero, body=MECH)
                farther = {0x7A: 18, 0x23B: 12, 0x29: 11}[hero]
                poses = (
                    (attacker, zarya, 0, (0, 1, 0)),
                    (defender, defender_hero, 1, (0, 1, 4 if case == "defense" else farther)),
                    (target, zarya, 1, (1, 1, 8)),
                    (caster, zarya, 1, (6, 1, 8)),
                )
                for actor, definition, team, point in poses:
                    self.assertTrue(match.switch_hero(actor, definition))
                    actor.team = team
                    actor.mover.position[:] = point
                    actor.mover.body = None
                attacker.body_script.ult_seconds = 0
                for _ in range(100):
                    run.step({defender: (-32768, 0, 0)})
                angles = aim(fight._eye(caster), fight._eye(target))
                shielded = case != "unshielded"
                run.step({caster: (*angles, 16 if shielded else 0), defender: (-32768, 0, defense)})
                held = defense if hero == 0x7A else 0
                for _ in range(4):
                    run.step({defender: (-32768, 0, held)})
                instance = next(i for i in defender.body_script.component.instances.values()
                                if i.graph.index == graph_index)
                self.assertTrue(instance.state(active).active)
                barriers = fight.zarya_barriers
                self.assertEqual(barriers.protects(target), shielded)
                pool = barriers.projected.get(caster.body)
                before = self.health(run, defender)
                if ultimate:
                    attacker.body_script.set_ult_charge(2100)
                run.step({attacker: (0, 0, 32 if ultimate else 2), defender: (-32768, 0, held)})
                rocket, = fight.rockets
                for _ in range(80):
                    run.step({defender: (-32768, 0, held)})
                    if rocket not in fight.rockets or rocket.shooter is not attacker:
                        break
                self.assertTrue(instance.state(active).active)
                amount = 5 if ultimate else 47
                absorbed = amount if case == "bubble" else 0
                if pool is not None:
                    self.assertEqual(pool.remaining, 200 - absorbed)
                energy = caster.body_script.component.entity_var(87).value() or 0
                self.assertAlmostEqual(energy, absorbed / 5, places=4)
                notice = instance.vars.get(6584)
                received = (notice.value() or 0) if notice is not None else 0
                self.assertEqual(received, 0 if absorbed else amount)
                self.assertEqual(self.health(run, defender), before)
                self.assertEqual(self.health(run, target), 400)
                self.assertEqual(self.health(run, caster), 400)
                self.assertFalse(attacker.client.messages(combat.HIT))
                fields = fight.zarya_gravitons.fields
                if case != "bubble" and hero == 0x29:
                    self.assertIs(rocket.shooter, defender)
                    self.assertEqual(rocket.source_entity, defender.body)
                    for _ in range(60):
                        if rocket not in fight.rockets:
                            break
                        run.step({defender: (-32768, 0, 0)})
                    self.assertNotIn(rocket, fight.rockets)
                    if ultimate:
                        field, = fields
                        self.assertIs(field.shooter, defender)
                        self.assertEqual(field.source_entity, defender.body)
                else:
                    self.assertNotIn(rocket, fight.rockets)
                    if case == "bubble":
                        self.assertGreater(rocket.distance, 6)
                        self.assertLess(rocket.distance, 8)
                    if ultimate and case == "bubble":
                        field, = fields
                        self.assertIs(field.shooter, attacker)
                    else:
                        self.assertFalse(fields)

    def test_micro_missile_direct_and_splash_resolve_against_each_barrier(self):
        cases = ("full", "edge break", "body break", "splash", "ally")
        for projected, case in product((False, True), cases):
            with self.subTest(projected=projected, case=case):
                run = Run(names=("Mech", "Protected", "Rear", "Projector"))
                mech, victim, rear, projector = run.players
                match = run.match
                fight = match.combat
                match.bots.clear()
                match._pending_bot_scripts.clear()
                mech_hero = replace(heroes()[0x02E000000000007A], body=MECH)
                zarya_hero = heroes()[zarya.HERO]
                victim_x = {"body break": 0, "splash": 2.1}.get(case, 1)
                victim_z = 6.5 if case == "splash" else 3
                poses = (
                    (mech, mech_hero, 0, (0, 1, 0)),
                    (victim, zarya_hero, 0 if case == "ally" else 1, (victim_x, 1, victim_z)),
                    (rear, zarya_hero, 1, (0, 1, 7)),
                    (projector, zarya_hero, 0 if case == "ally" else 1, (6, 1, victim_z)),
                )
                for actor, hero, team, point in poses:
                    self.assertTrue(match.switch_hero(actor, hero))
                    actor.team = team
                    actor.mover.position[:] = point
                    actor.mover.body = None
                for _ in range(100):
                    run.step({})
                caster = projector if projected else victim
                angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                run.step({caster: (*angles, 16 if projected else 8)})
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(victim))
                pool = (barriers.projected if projected else barriers.active)[caster.body]
                capacity = 2 if case.endswith("break") else 200
                pool.remaining = capacity
                target = next(t for t in fight._targets() if t.owner is victim)
                first_vitals = None
                peak_energy = 0
                with patch.object(fight, "_explode", wraps=fight._explode) as explosions:
                    for _ in range(125):
                        run.step({mech: (0, 0, 16)})
                        energy = caster.body_script.component.entity_var(87).value() or 0
                        peak_energy = max(peak_energy, energy)
                        if explosions.call_count == 1 and first_vitals is None:
                            first_vitals = self.health(run, victim), self.health(run, rear)
                        if explosions.call_count == 18 and not fight.rockets:
                            break
                self.assertEqual(explosions.call_count, 18)
                self.assertFalse(fight.rockets)
                rockets = [call.args[0] for call in explosions.call_args_list]
                self.assertTrue(all(r.definition == 0x0400000000001E8A for r in rockets))
                self.assertTrue(all(r.direct.amount == 3 and r.blast.amount == 4 for r in rockets))
                absorbed = {"full": 126, "edge break": 2, "body break": 2}.get(case, 0)
                if case == "splash":
                    a, b = target.segment()
                    x, y, z = target.feet
                    centre = (x, y + target.height / 2, z)
                    for burst in explosions.call_args_list:
                        rocket, point = burst.args
                        body_distance = combat.segment_distance(point, a, b) - target.radius
                        self.assertEqual(rocket.blast.splash(body_distance), 0)
                        edge_distance = math.dist(point, centre) - zarya.BUBBLE_RADIUS
                        damage = rocket.blast.amount * rocket.blast.splash(edge_distance)
                        self.assertGreater(damage, 0)
                        absorbed += damage
                self.assertAlmostEqual(pool.remaining, capacity - absorbed, places=4)
                self.assertAlmostEqual(peak_energy, absorbed / 5, places=4)
                if case.endswith("break"):
                    self.assertEqual(first_vitals, (400, 400))
                self.assertEqual(self.health(run, victim), 281 if case == "body break" else 400)
                rear_damage = {"edge break": 119, "splash": 126, "ally": 126}.get(case, 0)
                self.assertEqual(self.health(run, rear), 400 - rear_damage)

    def test_matrix_consumes_hyperspheres_and_admits_accretion(self):
        for rock in (False, True):
            with self.subTest(rock=rock):
                run, attacker, mech, _ = self.scene()
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E000000000023B]))
                attacker.mover.position[:] = [0, 1, 10]
                fight = run.match.combat
                angles = aim(fight._eye(attacker), fight._eye(mech))
                launched = False
                for frame in range(65):
                    button = (16 if rock else 1) if frame == 0 else 0
                    run.step({attacker: (*angles, button), mech: (-32768, 0, 2)})
                    launched |= bool(fight.rockets)
                    if rock and fight.is_stunned(mech):
                        break
                self.assertTrue(launched)
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, mech), 540 if rock else 600)
                self.assertEqual(fight.is_stunned(mech), rock)
                self.assertEqual(self.matrix(mech).state(3).active, not rock)
                if not rock:
                    self.assertFalse(attacker.client.messages(combat.HIT))

    def test_secondary_is_absorbed_without_damage_and_ultimate_cancels_field_with_charge_spent(self):
        for ultimate in (False, True):
            with self.subTest(ultimate=ultimate):
                run, a, b, _ = self.scene()
                self.shoot(run, a, b, ultimate=ultimate)
                self.step(run, b, frames=70)
                self.assertEqual(self.health(run, b), 600)
                self.assertFalse(a.client.messages(combat.HIT))
                self.assertEqual(a.stats.values.get(stats.HERO_DAMAGE, 0), 0)
                self.assertFalse(run.match.combat.rockets)
                self.assertFalse(run.match.combat.zarya_gravitons.fields)
                if ultimate:
                    self.assertEqual(a.body_script.component.entity_var(342).value(), 0)

    def test_primary_beam_bypasses_matrix_and_uses_beam_armour_policy(self):
        run, a, b, _ = self.scene()
        self.step(run, b, a=a, buttons=1, frames=51)
        self.assertAlmostEqual(self.health(run, b), 558, places=4)
        self.assertAlmostEqual(a.stats.values[stats.HERO_DAMAGE], 42, places=4)

    def test_quick_melee_bypasses_matrix_and_retains_discrete_armour_policy(self):
        run, a, b, _ = self.scene(close=True)
        self.step(run, b, a=a, buttons=0x800)
        self.step(run, b, frames=40)
        self.assertEqual(self.health(run, b), 575)

    def test_release_reopens_secondary_damage(self):
        run, a, b, _ = self.scene()
        self.step(run, b, enabled=False, frames=5)
        self.assertTrue(self.matrix(b).state(3).active)  # authored minimum hold has not ended
        for _ in range(80):
            if not self.matrix(b).state(3).active:
                break
            self.step(run, b, enabled=False)
        self.assertFalse(self.matrix(b).state(3).active)
        self.step(run, b, enabled=False, a=a, buttons=2)
        self.step(run, b, enabled=False, frames=70)
        self.assertEqual(self.health(run, b), 558)

    def test_rear_facing_matrix_does_not_absorb_before_its_body(self):
        run, a, b, _ = self.scene(facing=0)
        self.shoot(run, a, b)
        self.step(run, b, frames=70)
        self.assertEqual(self.health(run, b), 558)

    def test_allied_matrix_does_not_intercept(self):
        run, a, b, _ = self.scene(ally=True)
        self.shoot(run, a, b)
        self.step(run, b, frames=25)
        rocket, = run.match.combat.rockets
        self.assertIs(rocket.shooter, a)
        self.assertEqual(self.health(run, b), 600)

    def test_unknown_or_excluded_metadata_is_not_absorbed(self):
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                run, a, b, _ = self.scene()
                self.shoot(run, a, b)
                rocket, = run.match.combat.rockets
                tags = {} if unknown else {rocket.definition: frozenset({interception.EXCLUDED_TAG})}
                with patch.object(interception, "_TAGS", tags):
                    self.step(run, b, frames=70)
                self.assertEqual(self.health(run, b), 558)

    def test_nearer_static_wall_stops_projectile_before_matrix(self):
        run, a, b, _ = self.scene()
        wall = World([(-10, 0, 3, 10, 0, 3, 10, 10, 3),
                      (-10, 0, 3, 10, 10, 3, -10, 10, 3)])
        with patch.object(run.match.combat, "_collision", return_value=wall):
            self.shoot(run, a, b)
            self.step(run, b, frames=70)
        self.assertEqual(self.health(run, b), 600)
        self.assertFalse(run.match.combat.rockets)

    def test_nearer_body_is_hit_before_matrix(self):
        run, a, b, c = self.scene(nearer=True)
        self.shoot(run, a, b)
        self.step(run, b, frames=70)
        self.assertEqual(self.health(run, c), 153)
        self.assertEqual(self.health(run, b), 600)

    def test_sparse_graviton_respects_actual_projectile_defense_activation_tick(self):
        for hero, graph_index, state_index, button in (
            (0x02E0000000000029, 0x03A3, 10, 16),
            (0x02E000000000023B, 0x1D02, 1, 8),
            (dva.HERO, 0x02CC, 3, 2),
        ):
            for late in (False, True):
                with self.subTest(hero=hero, late=late):
                    run, caster, defender, _ = self.scene(enabled=False)
                    match, fight = run.match, run.match.combat
                    if hero != dva.HERO:
                        self.assertTrue(match.switch_hero(defender, heroes()[hero]))
                    defender.mover.position[:] = [0, 1, 5]
                    defender.mover.body = None
                    before = self.health(run, defender)
                    caster.body_script.set_ult_charge(2100)
                    run.step({caster: (0, 0, 32), defender: (-32768, 0, 0)})
                    run.step({defender: (-32768, 0, 0)})
                    rocket, = fight.rockets
                    self.assertEqual(rocket.last_tick, run.frame)

                    endpoint = run.frame + 45
                    activation = endpoint if late else run.frame + 1
                    match.tick = activation
                    defender.take_commands([Command(activation + 500, yaw=-32768, buttons=button)])
                    instance = next(i for i in defender.body_script.component.instances.values()
                                    if i.graph.index == graph_index)
                    self.assertTrue(instance.state(state_index).active)
                    match.update(endpoint * combat.TICK, endpoint, frames=False)
                    self.assertFalse(fight.rockets)
                    if late:
                        field, = fight.zarya_gravitons.fields
                        self.assertIs(field.shooter, caster)
                        self.assertLess(field.end_tick - 250, activation)
                        self.assertLess(self.health(run, defender), before)
                    elif graph_index == 0x03A3:
                        field, = fight.zarya_gravitons.fields
                        self.assertIs(field.shooter, defender)
                        self.assertEqual(self.health(run, defender), before)
                    else:
                        self.assertFalse(fight.zarya_gravitons.fields)
                        self.assertEqual(self.health(run, defender), before)

    def test_silent_projectile_defenses_expire_between_real_graviton_contacts(self):
        for hero, graph_index, state_index, button, duration_frames in (
            (0x02E0000000000029, 0x03A3, 10, 16, 125),
            (0x02E000000000023B, 0x1D02, 1, 8, 124),
            (dva.HERO, 0x02CC, 3, 2, 31),
        ):
            for early in (True, False):
                with self.subTest(graph=hex(graph_index), early=early):
                    run, caster, defender, _ = self.scene(enabled=False)
                    match, fight = run.match, run.match.combat
                    if hero != dva.HERO:
                        self.assertTrue(match.switch_hero(defender, heroes()[hero]))
                    defender.mover.position[:] = [0, 1, 5]
                    defender.mover.body = None
                    before = self.health(run, defender)
                    run.step({defender: (-32768, 0, button)})
                    run.step({defender: (-32768, 0, 0)})
                    volume = next(i for i in defender.body_script.component.instances.values()
                                  if i.graph.index == graph_index).state(state_index)
                    self.assertTrue(volume.active)
                    start = run.frame
                    launch = start + (1 if early else 155)
                    match.update(launch * combat.TICK, launch, frames=False)
                    caster.body_script.set_ult_charge(2100)
                    caster.take_commands([Command(launch, buttons=32)])
                    self.assertEqual(len(fight.rockets), 1)
                    match.update((launch + 1) * combat.TICK, launch + 1, frames=False)
                    if early:
                        end = start - 1 + duration_frames
                        match.update(end * combat.TICK, end, frames=False)
                        self.assertTrue(volume.active)
                        match.update((end + 1) * combat.TICK, end + 1, frames=False)
                        self.assertFalse(volume.active)
                    endpoint = start + 200
                    match.update(endpoint * combat.TICK, endpoint, frames=False)
                    self.assertFalse(volume.active)
                    self.assertFalse(fight.rockets)
                    if not early:
                        field, = fight.zarya_gravitons.fields
                        self.assertIs(field.shooter, caster)
                        self.assertLess(self.health(run, defender), before)
                    elif graph_index == 0x03A3:
                        field, = fight.zarya_gravitons.fields
                        self.assertIs(field.shooter, defender)
                        self.assertEqual(self.health(run, defender), before)
                    else:
                        self.assertFalse(fight.zarya_gravitons.fields)
                        shields = 3 if graph_index == 0x1D02 else 0
                        self.assertEqual(self.health(run, defender), before + shields)

    def test_matrix_absorption_does_not_bypass_the_cast_charge_lock(self):
        run, a, b, _ = self.scene(close=True)
        script = a.body_script
        script.ult_seconds = 60
        self.shoot(run, a, b, ultimate=True)
        cast_tick = run.frame
        ready = cast_tick + 32
        self.assertEqual(zarya.ultimate_charge_ready_frame(script), ready)
        a.take_commands([Command(cast_tick + 200)])
        self.assertEqual(zarya.ultimate_charge_ready_frame(script), 0)
        self.step(run, b)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(run.match.combat.zarya_gravitons.fields)
        self.assertEqual(self.health(run, b), 600)

        charge = script.component.entity_var(ULT_CHARGE)
        run.match.update((ready - 1) * combat.TICK, ready - 1, frames=False)
        self.assertEqual(charge.value(), 0)
        run.match.update(ready * combat.TICK, ready, frames=False)
        self.assertAlmostEqual(charge.value(), 0.08, places=5)

    def test_no_prediction_lease_still_absorbs_graviton(self):
        run, a, b, _ = self.scene()
        with patch.object(run.match.combat.projectiles, "fired", return_value=None):
            self.shoot(run, a, b, ultimate=True)
        self.step(run, b, frames=70)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(run.match.combat.zarya_gravitons.fields)
        self.assertEqual(self.health(run, b), 600)

    def test_source_team_change_does_not_rewrite_emitted_team(self):
        run, a, b, _ = self.scene()
        self.shoot(run, a, b)
        a.team = b.team
        self.step(run, b, frames=70)
        self.assertEqual(self.health(run, b), 600)
        self.assertFalse(run.match.combat.rockets)

    def test_absorption_does_not_add_per_projectile_resource_cost(self):
        traces = []
        for incoming in (False, True):
            run, a, b, _ = self.scene()
            self.step(run, b, a=a, buttons=2 if incoming else 0)
            trace = []
            for _ in range(25):
                self.step(run, b)
                trace.append(self.matrix(b).vars[87].value())
            traces.append(trace)
        self.assertEqual(len(traces[0]), len(traces[1]))
        for expected, actual in zip(*traces, strict=True):
            # Event-driven ChaseVar evaluation may rebake a binary32 value;
            # this band64..128 has ULP2^-17. No authored per-projectile cost.
            self.assertAlmostEqual(expected, actual, delta=4 * 2**-17)

    def test_dead_matrix_receiver_does_not_absorb_later_flight(self):
        run, a, b, _ = self.scene()
        self.shoot(run, a, b)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is b)
        fight._damage(a, target, 1000, False, None, run.frame)
        self.step(run, b, frames=25)
        self.assertTrue(fight.is_dead(b))
        rocket, = fight.rockets
        self.assertIs(rocket.shooter, a)

    def test_switching_back_to_pilot_retires_matrix(self):
        run, a, b, _ = self.scene()
        self.shoot(run, a, b)
        old = b.body_script
        self.assertTrue(run.match.switch_hero(b, heroes()[0x02E000000000007A]))
        self.assertNotEqual(b.hero.body, MECH)
        self.assertTrue(old.retired)
        self.step(run, b, frames=25)
        rocket, = run.match.combat.rockets
        self.assertIs(rocket.shooter, a)

    def test_exhausted_resource_does_not_absorb(self):
        run, a, b, _ = self.scene()
        self.step(run, b, frames=150)
        self.assertFalse(self.matrix(b).state(3).active)
        self.assertEqual(self.matrix(b).vars[87].value(), 0)
        self.shoot(run, a, b)
        self.step(run, b, frames=70)
        self.assertEqual(self.health(run, b), 558)

    def test_sparse_contacts_respect_matrix_already_ended_on_owner_vm(self):
        for hold_frames in (0, 45, 200):
            paced, _, mech, _ = self.scene(close=True)
            for elapsed in range(200):
                self.step(paced, mech, enabled=elapsed < hold_frames)
                if not self.matrix(mech).state(3).active:
                    break
            self.assertFalse(self.matrix(mech).state(3).active)
            cutoff = paced.frame
            for attack in (2, 32):
                for late in (False, True):
                    with self.subTest(hold_frames=hold_frames, attack=attack, late=late):
                        run, caster, mech, _ = self.scene(close=True)
                        match, fight = run.match, run.match.combat
                        matrix = self.matrix(mech)
                        launch = cutoff + 1 if late else cutoff - 12
                        for at in range(run.frame + 1, cutoff + 5):
                            match.tick = at
                            held = at - run.frame <= hold_frames
                            mech.take_commands([Command(at, yaw=-32768, buttons=2 if held else 0)])
                            if at == launch:
                                yaw, pitch = aim(fight._eye(caster), fight._eye(mech))
                                if attack == 32:
                                    caster.body_script.set_ult_charge(2100)
                                caster.take_commands([Command(run.frame + 1, yaw=yaw, pitch=pitch, buttons=attack)])
                                self.assertEqual(len(fight.rockets), 1)
                        self.assertFalse(matrix.state(3).active)
                        self.assertEqual(fight.tick, run.frame)
                        resource = matrix.vars[87].value()
                        end = cutoff + 40
                        match.update(end * combat.TICK, end, frames=False)
                        self.assertFalse(fight.rockets)
                        self.assertEqual(matrix.vars[87].value(), resource)
                        self.assertNotIn(mech.body, fight.projectile_defense_windows)
                        if attack == 32:
                            self.assertEqual(bool(fight.zarya_gravitons.fields), late)
                        if late:
                            if attack == 2:
                                self.assertEqual(self.health(run, mech), 558)
                            else:
                                self.assertLess(self.health(run, mech), 600)
                        else:
                            self.assertEqual(self.health(run, mech), 600)
                            self.assertEqual(matrix.vars[6584].value(), 47 if attack == 2 else 5)

    def test_held_matrix_depletes_on_world_clock_without_owner_commands(self):
        paced, _, mech, _ = self.scene()
        matrix = self.matrix(mech)
        for _ in range(200):
            if not matrix.state(3).active:
                break
            self.step(paced, mech)
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(matrix.vars[87].value(), 0)
        empty_frame = paced.frame

        for early in (True, False):
            with self.subTest(early=early, empty_frame=empty_frame):
                run, caster, mech, _ = self.scene()
                match, fight = run.match, run.match.combat
                matrix = self.matrix(mech)
                mech.mover.position[:] = [0, 1, 5]
                mech.mover.body = None
                launch = empty_frame - 12 if early else empty_frame + 2
                match.update(launch * combat.TICK, launch, frames=False)
                self.assertEqual(matrix.state(3).active, early)
                caster.body_script.set_ult_charge(2100)
                caster.take_commands([Command(launch, buttons=32)])
                self.assertEqual(len(fight.rockets), 1)
                match.update((launch + 1) * combat.TICK, launch + 1, frames=False)
                if early:
                    match.update(empty_frame * combat.TICK, empty_frame, frames=False)
                    self.assertTrue(matrix.state(3).active)
                    match.update((empty_frame + 1) * combat.TICK, empty_frame + 1, frames=False)
                    self.assertFalse(matrix.state(3).active)
                end = empty_frame + 35
                match.update(end * combat.TICK, end, frames=False)
                self.assertFalse(matrix.state(3).active)
                self.assertEqual(matrix.vars[87].value(), 0)
                self.assertEqual(mech.body_script.buttons, 2)
                self.assertFalse(fight.rockets)
                if early:
                    self.assertFalse(fight.zarya_gravitons.fields)
                    self.assertEqual(self.health(run, mech), 600)
                    for offset, buttons in ((1, 2), (2, 0), (3, 2)):
                        tick = end + offset
                        match.update(tick * combat.TICK, tick, frames=False)
                        mech.take_commands([Command(tick, yaw=-32768, buttons=buttons)])
                        self.assertFalse(matrix.state(3).active)
                        self.assertEqual(matrix.vars[87].value(), 0)
                    recovered = end + 150
                    match.update(recovered * combat.TICK, recovered, frames=False)
                    mech.take_commands([Command(recovered, yaw=-32768)])
                    self.assertGreater(matrix.vars[87].value(), 0)
                    match.update((recovered + 1) * combat.TICK, recovered + 1, frames=False)
                    mech.take_commands([Command(recovered + 1, yaw=-32768, buttons=2)])
                    self.assertTrue(matrix.state(3).active)
                else:
                    field, = fight.zarya_gravitons.fields
                    self.assertIs(field.shooter, caster)
                    self.assertLess(self.health(run, mech), 600)

    def test_authored_minimum_hold_still_protects_immediately_after_release(self):
        run, a, b, _ = self.scene()
        self.step(run, b, enabled=False, a=a, buttons=2)
        self.assertTrue(self.matrix(b).state(3).active)
        self.step(run, b, enabled=False, frames=40)
        self.assertFalse(self.matrix(b).state(3).active)
        self.assertEqual(self.health(run, b), 600)

    def test_actual_enemy_fusion_cannon_uses_eleven_pellets_against_zarya_and_her_barrier(self):
        for barrier in (False, True):
            with self.subTest(barrier=barrier):
                run, a, b, _ = self.scene(enabled=False, close=True)
                if barrier:
                    run.step({a: (0, 0, 8), b: (-32768, 0, 0)})
                run.step({a: (0, 0, 0), b: (-32768, 0, 1)})
                self.assertEqual(self.health(run, a), 400 if barrier else 378)
                if barrier:
                    self.assertEqual(run.match.combat.zarya_barriers.active[a.body].remaining, 178)
                    self.step(run, b, enabled=False)
                    self.assertAlmostEqual(a.body_script.component.entity_var(87).value(), 4.4, places=4)

    def test_sparse_rear_flash_closes_matrix_before_later_secondary_contact(self):
        for late in (False, True):
            with self.subTest(late=late):
                run, caster, mech, flash = self.scene(close=True, flash=True)
                match, fight = run.match, run.match.combat
                flash.mover.position[:] = (0, 1, 4.5)
                flash.mover.body = None
                start = run.frame
                inputs = (
                    (start + (55 if late else 2), caster, Command(start + 1, buttons=2)),
                    (start + 18, flash, Command(start + 1, yaw=-32768, buttons=16)),
                )
                for at, owner, command in sorted(inputs, key=lambda event: event[0]):
                    match.tick = at
                    owner.take_commands([command])
                self.assertEqual(len(fight.rockets), 2)
                end = start + 70
                match.update(end * combat.TICK, end, frames=False)
                self.assertTrue(fight.is_stunned(mech))
                self.assertFalse(self.matrix(mech).state(3).active)
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, mech), 538 if late else 580)

    def test_sparse_flash_admits_held_matrix_only_after_recovery(self):
        for recovered in (False, True):
            with self.subTest(recovered=recovered):
                run, caster, mech, flash = self.scene(enabled=False, close=True, flash=True)
                match, fight = run.match, run.match.combat
                flash.mover.position[:] = (0, 1, 4.5)
                flash.mover.body = None
                start = run.frame
                resource = self.matrix(mech).vars[87].value()
                match.tick = start + 1
                flash.take_commands([Command(match.tick, yaw=-32768, buttons=16)])
                match.tick = start + (90 if recovered else 35)
                mech.take_commands([Command(match.tick, yaw=-32768, buttons=2)])
                self.assertTrue(self.matrix(mech).state(3).active)
                match.tick = start + 95
                caster.take_commands([Command(match.tick, buttons=2)])
                end = start + 113
                match.update(end * combat.TICK, end, frames=False)
                self.assertEqual(self.matrix(mech).state(3).active, recovered)
                self.assertEqual(bool(mech.body_script.component.held), recovered)
                self.assertEqual(self.health(run, mech), 580 if recovered else 538)
                if not recovered:
                    self.assertEqual(self.matrix(mech).vars[87].value(), resource)

    def test_sparse_sleep_admits_held_matrix_only_after_earlier_wake(self):
        for wake_offset in (12, 25, 35):
            with self.subTest(wake_offset=wake_offset):
                run, caster, mech, ana = self.scene(enabled=False, close=True)
                match, fight = run.match, run.match.combat
                resource = self.matrix(mech).vars[87].value()
                end = queue_sleep_wake_and_defense(
                    self, run, caster, mech, ana, wake_offset=wake_offset, defense_button=2,
                )
                self.assertTrue(self.matrix(mech).state(3).active)
                match.update(end * combat.TICK, end, frames=False)
                admitted = wake_offset < 25
                self.assertFalse(fight.is_stunned(mech))
                self.assertEqual(self.matrix(mech).state(3).active, admitted)
                self.assertEqual(bool(mech.body_script.component.held), admitted)
                self.assertEqual(self.health(run, mech), 572.5 if admitted else 530.5)
                if not admitted:
                    self.assertEqual(self.matrix(mech).vars[87].value(), resource)
                    tick = end + 1
                    match.update(tick * combat.TICK, tick, frames=False)
                    mech.take_commands([Command(tick, yaw=-32768, buttons=2)])
                    self.assertTrue(self.matrix(mech).state(3).active)

    def test_actual_rear_flashbang_interrupts_matrix_before_secondary_arrives(self):
        run, a, b, flash = self.scene(flash=True)
        run.step({b: (-32768, 0, 2), flash: (-32768, 0, 16)})
        fight = run.match.combat
        for _ in range(40):
            if fight.is_stunned(b):
                break
            self.step(run, b)
        self.assertTrue(fight.is_stunned(b))
        self.assertFalse(self.matrix(b).state(3).active)
        before = self.health(run, b)
        self.step(run, b, enabled=False, a=a, buttons=2)
        self.step(run, b, enabled=False, frames=70)
        self.assertEqual(before - self.health(run, b), 42)
        self.assertFalse(self.matrix(b).state(3).active)

    def test_actual_reflected_secondary_and_graviton_are_then_absorbed_by_allied_matrix(self):
        for ultimate in (False, True):
            with self.subTest(ultimate=ultimate):
                run = Run(names=("Zarya", "Genji", "Mech"))
                a, genji, mech = run.players
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.switch_hero(a, heroes()[0x02E0000000000068])
                run.match.switch_hero(genji, heroes()[0x02E0000000000029])
                run.match.switch_hero(mech, replace(heroes()[0x02E000000000007A], body=MECH))
                a.team = mech.team = 0
                genji.team = 1
                a.mover.position[:] = [0, 1, -3]
                genji.mover.position[:] = [0, 1, 5]
                mech.mover.position[:] = [1, 1, 0]
                a.body_script.ult_seconds = 0
                for _ in range(100):
                    run.step({genji: (-32768, 0, 0)})
                for frame in range(5):
                    run.step({genji: (-32768, 0, 16 if frame == 0 else 0), mech: (0, 0, 2)})
                if ultimate:
                    a.body_script.set_ult_charge(2100)
                run.step({a: (0, 0, 32 if ultimate else 2), genji: (-32768, 0, 0), mech: (0, 0, 2)})
                for _ in range(70):
                    run.step({genji: (-32768, 0, 0), mech: (0, 0, 2)})
                reflect = next(i for i in genji.body_script.component.instances.values()
                               if i.graph.index == 0x03A3)
                self.assertEqual(reflect.vars[6584].value(), 5 if ultimate else 47)
                self.assertEqual(self.health(run, a), 400)
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(self.health(run, mech), 600)
                self.assertFalse(run.match.combat.rockets)
                self.assertFalse(run.match.combat.zarya_gravitons.fields)
                if ultimate:
                    self.assertEqual(a.body_script.component.entity_var(342).value(), 0)


if __name__ == "__main__":
    unittest.main()
