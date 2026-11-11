"""Actual Reinhardt 0095 input and authored shield mesh against Torbjorn."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_torbjorn_defenses as defenses
from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat, hero_torbjorn, reinhardt_barrier, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION")
                     and os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"), "needs authored barrier and graphs")
class TorbjornReinhardtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        defenses.TorbjornDefenseTests.setUpClass()
        cls.addClassCleanup(defenses.TorbjornDefenseTests.tearDownClass)

    def arena(self, distance=4):
        fixture = defenses.TorbjornDefenseTests()
        run, owner, rein, behind = fixture.arena("Reinhardt", distance)
        x, y, z = owner.mover.position
        fixture.place(behind, (x + 100, y, z + 100))
        return fixture, run, owner, rein

    def test_raised_shield_takes_rivets_and_pellets_then_release_restores_body_hit(self):
        for button, amount in ((1, 70), (2, 125)):
            with self.subTest(button=button):
                fixture, run, owner, rein = self.arena()
                fight = run.match.combat
                pose = {"defender_aim": (-32768, 0)}
                fixture.advance(run, owner, rein, 5, defense=2, **pose)
                shield = fight.barriers.states[rein.body]
                self.assertTrue(shield.active)
                self.assertEqual(shield.current, 1600)
                body = fixture.health(run, rein)
                before = body.total
                fixture.step(run, owner, rein, fire=button, defense=2, **pose)
                fixture.advance(run, owner, rein, 20, defense=2, **pose)
                self.assertAlmostEqual(shield.current, 1600 - amount)
                self.assertEqual(body.total, before)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                fixture.advance(run, owner, rein, 55, **pose)
                self.assertFalse(shield.active)
                fixture.step(run, owner, rein, fire=button, **pose)
                fixture.advance(run, owner, rein, 20, **pose)
                self.assertLess(body.total, before)

    def test_barrier_field_uses_live_raise_and_release_during_projectile_flight(self):
        cases = (
            ("rivet", 1, 1, 8, 70, 65),
            ("secondary", 2, 10, 12, 120.5636, 75),
            ("globule", 1, 1, 8, 25, 20),
        )
        for weapon, button, count, raise_distance, shield_damage, armored_damage in cases:
            for raise_after_launch in (False, True):
                with self.subTest(weapon=weapon, raise_after_launch=raise_after_launch):
                    distance = raise_distance if raise_after_launch else 4
                    fixture, run, owner, rein = self.arena(distance)
                    fight = run.match.combat
                    pose = {"defender_aim": (-32768, 0)}
                    hp = fixture.health(run, rein)
                    before = hp.total
                    charge = owner.body_script.component.entity_var(342)
                    if weapon == "globule":
                        owner.body_script.set_ult_charge(10000)
                        fixture.step(run, owner, rein, fire=32, **pose)
                        fixture.advance(run, owner, rein, 45, **pose)
                    initial_defense = 0 if raise_after_launch else 2
                    fixture.advance(run, owner, rein, 5, defense=initial_defense, **pose)
                    fixture.step(run, owner, rein, fire=button, defense=initial_defense, **pose)
                    volley = tuple(fight.rockets)
                    self.assertEqual(len(volley), count)
                    self.assertTrue(all(shot.shooter is owner and shot.distance == 0 for shot in volley))
                    shield = fight.barriers.states[rein.body]
                    self.assertEqual(shield.active, not raise_after_launch)
                    self.assertEqual(shield.current, 1600)
                    defense = 2 if raise_after_launch else 0
                    for _ in range(5):
                        fixture.step(run, owner, rein, defense=defense, **pose)
                        if shield.active == raise_after_launch:
                            break
                    self.assertEqual(shield.active, raise_after_launch)

                    fixture.advance(run, owner, rein, 20, defense=defense, **pose)
                    self.assertFalse(fight.rockets or fight.molten_pools or fight.molten_burns)
                    shield_loss = shield_damage if raise_after_launch else 0
                    self.assertAlmostEqual(1600 - shield.current, shield_loss, places=3)
                    direct = 0 if raise_after_launch else armored_damage
                    self.assertEqual(hp.total, before - direct)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), direct)
                    self.assertEqual(charge.value(), 0 if weapon == "globule" else direct)
                    self.assertEqual((owner.entity, rein.body) in fight.torbjorn_marks,
                                     weapon == "rivet" and not raise_after_launch)

    def test_one_secondary_volley_breaks_reinhardt_then_projected_barrier_before_body(self):
        fixture = defenses.TorbjornDefenseTests()
        zarya_hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        cases = ((5, 12.75, 1, 2), (12.75, 5, 2, 1))
        for mesh_capacity, bubble_capacity, mesh_pellets, bubble_pellets in cases:
            with self.subTest(mesh_capacity=mesh_capacity, bubble_capacity=bubble_capacity):
                run = Run(KANEZAKA_DM, names=("Torbjorn", "Reinhardt", "Zarya", "Protected"))
                owner, rein, zarya, protected = run.players
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                for player, hero in ((owner, heroes()[hero_torbjorn.HERO]),
                                     (rein, heroes()[reinhardt_barrier.HERO]), (zarya, zarya_hero)):
                    self.assertTrue(match.switch_hero(player, hero))
                match.bots.clear()
                match._pending_bot_scripts.clear()
                rein.team = zarya.team = protected.team = 1 - owner.team
                x, y, z = owner.mover.position
                fixture.place(rein, (x + 1.6, y, z + 2))
                fixture.place(protected, (x, y, z + 4))
                fixture.place(zarya, (x - 4, y, z + 4))
                fixture.advance(run, owner, protected, 35)
                for _ in range(5):
                    run.step({rein: (-32768, 0, 2)})
                shield = fight.barriers.states[rein.body]
                self.assertTrue(shield.active)
                self.assertEqual(shield.current, 1600)
                run.step({rein: (-32768, 0, 2), zarya: (*fixture.body_aim(run, zarya, protected), 16)})
                bubble = fight.bubbles.for_target(protected)
                self.assertIsNotNone(bubble)
                self.assertEqual(bubble.health, 200)
                shield.current = mesh_capacity
                bubble.health = bubble_capacity
                owner.body_script.ult_seconds = 0
                owner.body_script.set_ult_charge(0)
                aim = fixture.body_aim(run, owner, protected)
                rein_hp = fixture.health(run, rein)
                protected_hp = fixture.health(run, protected)
                with (
                    patch.object(fight, "_shield_damage", wraps=fight._shield_damage) as shields,
                    patch.object(fight, "_damage", wraps=fight._damage) as bodies,
                ):
                    run.step({owner: (*aim, 2), rein: (-32768, 0, 2)})
                    self.assertEqual(len(fight.rockets), 10)
                    for _ in range(16):
                        run.step({rein: (-32768, 0, 2)})
                self.assertEqual([call.args[1] for call in shields.call_args_list],
                                 [rein] * mesh_pellets + [bubble] * bubble_pellets)
                self.assertEqual([call.args[2] for call in shields.call_args_list], [12.5] * 3)
                self.assertEqual([call.args[2] for call in bodies.call_args_list], [12.5] * 7)
                self.assertTrue(all(call.args[1].owner is protected for call in bodies.call_args_list))
                self.assertEqual(shield.current, 0)
                self.assertFalse(shield.active)
                self.assertGreater(shield.broken_until, fight.now)
                self.assertIsNone(fight.bubbles.for_target(protected))
                self.assertEqual(rein_hp.total, 500)
                self.assertEqual(protected_hp.total, 112.5)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 87.5)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 87.5)

    def test_turret_fire_hits_shield_before_body(self):
        fixture, run, owner, rein = self.arena(8)
        fight = run.match.combat
        pose = {"defender_aim": (-32768, 0), "defense": 2}
        run.step({owner: (0, 4000, 8), rein: (-32768, 0, 2)})
        fixture.advance(run, owner, rein, 225, **pose)
        turret = fight.turrets[owner.entity]
        shield = fight.barriers.states[rein.body]
        self.assertTrue(shield.active)
        self.assertGreater(turret.shots, 0)
        self.assertEqual(shield.current, 1600 - 14 * turret.shots)
        hp = fixture.health(run, rein)
        self.assertEqual(hp.total, 500)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        for _ in range(25):
            fixture.step(run, owner, rein, defender_aim=(-32768, 0))
            if not shield.active:
                break
        self.assertFalse(shield.active)
        shots = turret.shots
        shield_health = shield.current
        for _ in range(30):
            if hp.total < 500:
                break
            run.step({})
        self.assertEqual(turret.shots, shots + 1)
        self.assertEqual(shield.current, shield_health)
        self.assertEqual(hp.total, 491)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 9)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 9)

    def test_melee_damages_both_shield_and_body_once(self):
        for hammer, amount in ((False, 30), (True, 55)):
            with self.subTest(hammer=hammer):
                fixture, run, owner, rein = self.arena(2.4)
                if hammer:
                    run.frame += 1
                    owner.take_commands([Command(run.frame, action=2)])
                    fixture.advance(run, owner, rein, 45)
                for _ in range(5):
                    run.step({rein: (-32768, 0, 2)})
                shield = run.match.combat.barriers.states[rein.body]
                self.assertTrue(shield.active)
                run.step({owner: (0, 0, 1 if hammer else 0x800), rein: (-32768, 0, 2)})
                for _ in range(35):
                    run.step({rein: (-32768, 0, 2)})
                self.assertEqual(shield.current, 1600 - amount)
                self.assertEqual(fixture.health(run, rein).total, 500 - amount + 5)

    def test_actual_rivets_break_shield_without_spill_and_early_raise_fails(self):
        fixture, run, owner, rein = self.arena()
        fight = run.match.combat
        pose = {"defender_aim": (-32768, 0)}
        fixture.advance(run, owner, rein, 5, defense=2, **pose)
        shield = fight.barriers.states[rein.body]
        self.assertTrue(shield.active)
        for _ in range(1400):
            fixture.step(run, owner, rein, fire=1, defense=2, **pose)
            if shield.current == 0:
                break
        self.assertEqual(shield.current, 0)
        self.assertFalse(shield.active)
        self.assertEqual(fixture.health(run, rein).total, 500)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        self.assertGreater(shield.broken_until, fight.now)
        fixture.step(run, owner, rein, **pose)
        fixture.step(run, owner, rein, defense=2, **pose)
        self.assertFalse(shield.active)
        fixture.advance(run, owner, rein, 44, **pose)
        fixture.step(run, owner, rein, fire=1, **pose)
        fixture.advance(run, owner, rein, 20, **pose)
        self.assertEqual(fixture.health(run, rein).total, 435)
        while fight.now + combat.TICK < shield.broken_until:
            fixture.step(run, owner, rein, **pose)
            self.assertFalse(shield.active)
        fixture.advance(run, owner, rein, 4, **pose)
        fixture.advance(run, owner, rein, 8, defense=2, **pose)
        instance = fight.barriers.instance(rein)
        self.assertTrue(shield.active, (
            shield, fight.now, instance.find_var(0, 61).value(), instance.states[59].flags,
            [(s.node.pos, s.active, s.counter, s.flags_changed, getattr(s, "current", None))
             for s in instance.states.values() if s.active or s.index in (1, 35, 48, 17)],
        ))
        self.assertGreater(shield.current, 0)

    def test_wall_and_friendly_shield_are_ordered_before_enemy_body_contact(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                fixture, run, owner, rein = self.arena()
                x, y, z = owner.mover.position
                target = rein
                if wall:
                    front = z + 1.5
                    run.match.collision_world = World([
                        (x-30, y, z-30, x-30, y, z+30, x+30, y, z+30),
                        (x-30, y, z-30, x+30, y, z+30, x+30, y, z-30),
                        (x-3, y, front, x+3, y, front, x+3, y+4, front),
                        (x-3, y, front, x+3, y+4, front, x-3, y+4, front),
                    ], name="barrier-cover")
                else:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    rein.team = owner.team
                    target = run.players[2]
                    target.team = 1 - owner.team
                    fixture.place(target, (x, y, z + 6))
                pose = {"defender_aim": (-32768, 0), "defense": 2}
                fixture.advance(run, owner, rein, 5, **pose)
                shield = run.match.combat.barriers.states[rein.body]
                self.assertTrue(shield.active)
                hp = fixture.health(run, target)
                before = hp.total
                yaw, pitch = fixture.body_aim(run, owner, target)
                run.step({owner: (yaw, pitch, 1), rein: (-32768, 0, 2)})
                fixture.advance(run, owner, rein, 20, **pose)
                self.assertEqual(shield.current, 1600)
                self.assertEqual(hp.total, before if wall else before - 70)

    def test_molten_globule_damages_raised_shield_and_creates_no_remote_pool(self):
        fixture, run, owner, rein = self.arena()
        fight = run.match.combat
        owner.body_script.set_ult_charge(10000)
        pose = {"defender_aim": (-32768, 0), "defense": 2}
        fixture.step(run, owner, rein, fire=32, **pose)
        fixture.advance(run, owner, rein, 45, **pose)
        shield = fight.barriers.states[rein.body]
        self.assertTrue(shield.active)
        fixture.step(run, owner, rein, fire=1, **pose)
        fixture.advance(run, owner, rein, 25, **pose)
        self.assertEqual(shield.current, 1575)
        self.assertEqual(fixture.health(run, rein).total, 500)
        self.assertEqual(fight.molten_pools, {})

    def test_shield_capacity_and_cooldown_are_snapshot_state(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game import reinhardt_barrier
        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, _, players = case.scene()
        owner = players[0]
        session.apply("switch_hero", {"account": owner.account_lo, "hero": reinhardt_barrier.HERO})
        shield = owner.match.combat.barriers.sync(owner)
        self.assertEqual(shield.current, 1600)
        first = snapshot_digest(case.capture(session))
        self.assertEqual(first, snapshot_digest(case.capture(session)))
        shield.current -= 1
        self.assertNotEqual(first, snapshot_digest(case.capture(session)))
        shield.current += 1
        shield.broken_until += 1
        self.assertNotEqual(first, snapshot_digest(case.capture(session)))

    def test_allied_shield_protects_torbjorn_from_actual_flashbang(self):
        for raised in (False, True):
            with self.subTest(raised=raised):
                fixture, run, owner, rein = self.arena(distance=3)
                caster = run.players[2]
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                rein.team = owner.team
                caster.team = 1 - owner.team
                run.match.switch_hero(caster, heroes()[0x02E0000000000042])
                x, y, z = owner.mover.position
                fixture.place(caster, (x, y, z))
                fixture.place(owner, (x, y, z + 4.8))
                stance = (-32768, 0, 2 if raised else 0)
                for _ in range(45):
                    run.step({rein: stance})
                shield = fight.barriers.states[rein.body]
                self.assertEqual(shield.active, raised)
                yaw, pitch = fixture.body_aim(run, caster, rein)
                run.step({rein: stance, caster: (yaw, pitch, 16)})
                stunned = set()
                for _ in range(40):
                    run.step({rein: stance, caster: (yaw, pitch, 0)})
                    stunned.update(p.body for p in (owner, rein) if fight.is_stunned(p))
                self.assertEqual(stunned, set() if raised else {owner.body, rein.body})
                self.assertEqual(shield.current, 1575 if raised else 1600)
                self.assertEqual(fixture.health(run, owner).total, 250 if raised else 230)
                self.assertEqual(fixture.health(run, rein).total, 500 if raised else 480)
