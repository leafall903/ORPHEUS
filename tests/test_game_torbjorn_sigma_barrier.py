"""Actual Torbjorn attacks against Sigma's moving and parked barrier."""

import os
import unittest
from dataclasses import replace

import test_game_torbjorn_defenses as defenses
from test_game_combat import aim
from test_game_mover import FLOOR, box, wall

from ow174.game import combat, sigma_grasp, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "ORPHEUS_SIGMA_COLLISION", "ORPHEUS_TORBJORN_GRAPH_BUNDLE", "ORPHEUS_PROJECTILE_TAGS",
)), "needs Sigma geometry and prepared combat inputs")
class TorbjornSigmaBarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        defenses.TorbjornDefenseTests.setUpClass()
        cls.addClassCleanup(defenses.TorbjornDefenseTests.tearDownClass)

    def arena(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, sigma, other = fixture.arena("Sigma", 12)
        x, y, z = torb.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        fixture.advance(run, torb, sigma, 25)
        actor = run.match.combat.sigma_barriers.actors[sigma.body]
        self.assertEqual(expr.to_entity(sigma.body_script.component.entity_var(512).value()), actor.body)
        self.assertNotIn(512, dict(sigma.body_script.replicated_vars()))
        self.assertEqual(actor.health, 700)
        self.assertEqual(actor.speed, 16.5)
        self.assertFalse(actor.active())
        return fixture, run, torb, sigma, actor

    def deploy(self, fixture, run, torb, sigma, actor):
        fixture.advance(run, torb, sigma, 15, defense=2, defender_aim=(-32768, 0))
        self.assertEqual(actor.mode, 2)
        fixture.step(run, torb, sigma, defender_aim=(-32768, 0))
        self.assertEqual(actor.mode, 3)
        self.assertTrue(actor.active())

    def test_actual_hold_travel_release_recall_and_redeploy(self):
        fixture, run, torb, sigma, actor = self.arena()
        pose = {"defender_aim": (-32768, 0)}
        fixture.advance(run, torb, sigma, 20, defense=2, **pose)
        start = actor.position
        fixture.advance(run, torb, sigma, 6, defense=2, **pose)
        self.assertEqual(actor.mode, 2)
        self.assertAlmostEqual(start[2] - actor.position[2], 6 * 16.5 * combat.TICK)
        fixture.step(run, torb, sigma, **pose)
        parked = actor.position
        fixture.advance(run, torb, sigma, 20, **pose)
        self.assertEqual(actor.position, parked)
        self.assertEqual(actor.mode, 3)
        fixture.step(run, torb, sigma, defense=2, **pose)
        self.assertFalse(actor.active())
        self.assertGreater(actor.source.vars[61].value(), 1.9)
        fixture.step(run, torb, sigma, **pose)
        fixture.advance(run, torb, sigma, 30, defense=2, **pose)
        self.assertEqual(actor.mode, 1)
        fixture.advance(run, torb, sigma, 130, **pose)
        self.assertEqual(actor.source.vars[61].value(), 0)
        self.deploy(fixture, run, torb, sigma, actor)

    def test_rivet_secondary_and_molten_consume_parked_capacity(self):
        fixture, run, torb, sigma, actor = self.arena()
        self.deploy(fixture, run, torb, sigma, actor)
        hp = fixture.health(run, sigma)
        before = hp.total
        for button, remaining in ((1, 630), (2, 505)):
            fixture.step(run, torb, sigma, fire=button)
            fixture.advance(run, torb, sigma, 44)
            self.assertEqual(actor.health, remaining)
        torb.body_script.set_ult_charge(10000)
        fixture.step(run, torb, sigma, fire=32)
        fixture.advance(run, torb, sigma, 45)
        fixture.step(run, torb, sigma, fire=1)
        fixture.advance(run, torb, sigma, 35)
        self.assertEqual(actor.health, 480)
        self.assertFalse(run.match.combat.molten_pools)
        self.assertEqual(hp.total, before)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)

    def test_experimental_barrier_deploys_and_recalls_during_projectile_flight(self):
        cases = (("rivet", 1, 1, 70, 70), ("secondary", 2, 10, 115.74347, 125), ("globule", 1, 1, 25, 25))
        for weapon, button, count, shield_damage, body_damage in cases:
            for recall in (False, True):
                with self.subTest(weapon=weapon, recall=recall):
                    fixture, run, torb, sigma, actor = self.arena()
                    fight = run.match.combat
                    pose = {"defender_aim": (-32768, 0)}
                    if recall:
                        self.deploy(fixture, run, torb, sigma, actor)
                        x, _, z = actor.position
                        y = torb.mover.position[1]
                        fixture.place(torb, (x, y, z - 4.5))
                        fixture.place(sigma, (x, y, z + 1))
                        fixture.advance(run, torb, sigma, 45, **pose)
                    elif weapon != "secondary":
                        x, y, z = torb.mover.position
                        fixture.place(sigma, (x, y, z + 8))
                        fixture.advance(run, torb, sigma, 5, **pose)
                    if weapon == "globule":
                        torb.body_script.set_ult_charge(10000)
                        fixture.step(run, torb, sigma, fire=32, **pose)
                        fixture.advance(run, torb, sigma, 45, **pose)
                    hp = fixture.health(run, sigma)
                    before = hp.total
                    charge = torb.body_script.component.entity_var(342)

                    self.assertEqual(actor.mode, 3 if recall else 1)
                    fixture.step(run, torb, sigma, fire=button, **pose)
                    volley = tuple(fight.rockets)
                    self.assertEqual(len(volley), count)
                    self.assertTrue(all(shot.shooter is torb and shot.distance == 0 for shot in volley))
                    self.assertEqual(actor.health, 700)
                    next_mode = 1 if recall else 2
                    for _ in range(5):
                        fixture.step(run, torb, sigma, defense=2, **pose)
                        if actor.mode == next_mode:
                            break
                    self.assertEqual(actor.mode, next_mode)
                    self.assertEqual(tuple(fight.rockets), volley)

                    start = actor.position
                    fixture.advance(run, torb, sigma, 24, defense=0 if recall else 2, **pose)
                    self.assertEqual(actor.mode, next_mode)
                    if not recall:
                        self.assertAlmostEqual(start[2] - actor.position[2], 24 * 16.5 * combat.TICK)
                    self.assertFalse(fight.rockets or fight.molten_pools or fight.molten_burns)
                    direct = body_damage if recall else 0
                    self.assertAlmostEqual(actor.health, 700 if recall else 700 - shield_damage, places=3)
                    self.assertEqual(hp.total, before - direct)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), direct)
                    self.assertEqual(charge.value(), 0 if weapon == "globule" else direct)
                    self.assertEqual((torb.entity, sigma.body) in fight.torbjorn_marks,
                                     recall and weapon == "rivet")

    def test_recall_repairs_only_after_authored_wait(self):
        fixture, run, torb, sigma, actor = self.arena()
        self.deploy(fixture, run, torb, sigma, actor)
        fixture.step(run, torb, sigma, fire=1)
        fixture.advance(run, torb, sigma, 140)
        self.assertEqual(actor.health, 630)
        fixture.step(run, torb, sigma, defense=2)
        fixture.advance(run, torb, sigma, 120)
        self.assertEqual(actor.health, 630)
        fixture.advance(run, torb, sigma, 30)
        before = actor.health
        self.assertGreater(before, 630)
        fixture.advance(run, torb, sigma, 10)
        self.assertAlmostEqual(actor.health - before, 12.8, places=2)
        self.assertEqual(actor.source.vars[586].value(), actor.health)

    def test_break_sets_distinct_cooldown_and_later_rivet_reaches_body(self):
        fixture, run, torb, sigma, actor = self.arena()
        self.deploy(fixture, run, torb, sigma, actor)
        hp = fixture.health(run, sigma)
        before = hp.total
        for shot in range(10):
            fixture.step(run, torb, sigma, fire=1)
            fixture.advance(run, torb, sigma, 39)
            self.assertEqual(hp.total, before)
            if shot < 9:
                self.assertEqual(actor.health, 700 - (shot + 1) * 70)
        self.assertFalse(actor.active())
        self.assertEqual(actor.mode, 1)
        self.assertGreater(actor.source.vars[61].value(), 4)
        fixture.step(run, torb, sigma, fire=1)
        fixture.advance(run, torb, sigma, 35)
        self.assertEqual(hp.total, before - 70)

    def test_held_mesh_stops_at_world_then_release_parks(self):
        fixture, run, torb, sigma, actor = self.arena()
        x, y, z = sigma.mover.position
        front = z - 2.5
        run.match.collision_world = World(FLOOR + wall(x - 6, front, x + 6, front, y0=y, y1=y + 4))
        pose = {"defender_aim": (-32768, 0)}
        fixture.advance(run, torb, sigma, 25, defense=2, **pose)
        self.assertTrue(actor.blocked)
        self.assertEqual(actor.mode, 2)
        self.assertAlmostEqual(actor.position[2] - front, actor.clearance, places=5)
        point = actor.position
        fixture.advance(run, torb, sigma, 8, defense=2, **pose)
        fixture.step(run, torb, sigma, **pose)
        self.assertEqual(actor.position, point)
        self.assertEqual(actor.mode, 3)

    def test_quick_melee_and_hammer_damage_parked_mesh_once(self):
        for button, damage in ((0x800, 30), (1, 55)):
            with self.subTest(button=button):
                fixture, run, torb, sigma, actor = self.arena()
                self.deploy(fixture, run, torb, sigma, actor)
                x, _, z = actor.position
                fixture.place(torb, (x, torb.mover.position[1], z - 1.7))
                if button == 1:
                    run.frame += 1
                    torb.take_commands([Command(run.frame, action=2)])
                fixture.advance(run, torb, sigma, 45)
                fixture.step(run, torb, sigma, fire=button)
                fixture.advance(run, torb, sigma, 45)
                self.assertEqual(actor.health, 700 - damage)

    def test_allied_mesh_consumes_flashbang_and_protects_torbjorn(self):
        fixture, run, torb, sigma, actor = self.arena()
        caster = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        sigma.team = torb.team
        caster.team = 1 - torb.team
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        self.deploy(fixture, run, torb, sigma, actor)
        x, _, z = actor.position
        y = torb.mover.position[1]
        fixture.place(caster, (x, y, z - 3))
        fixture.place(torb, (x, y, z + 1.5))
        fixture.advance(run, torb, sigma, 25)
        yaw, pitch = fixture.body_aim(run, caster, torb)
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(40):
            run.step({})
            self.assertFalse(run.match.combat.is_stunned(torb))
        self.assertEqual(actor.health, 675)
        self.assertEqual(fixture.health(run, torb).total, 250)

    def test_live_actor_passive_component_capture(self):
        from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest

        fixture, run, torb, sigma, actor = self.arena()
        self.deploy(fixture, run, torb, sigma, actor)
        first = snapshot_digest(export_component_vm(actor.component))
        self.assertEqual(first, snapshot_digest(export_component_vm(actor.component)))
        fixture.step(run, torb, sigma, fire=1)
        fixture.advance(run, torb, sigma, 40)
        self.assertNotEqual(first, snapshot_digest(export_component_vm(actor.component)))

    def test_active_mesh_retires_when_sigma_dies_to_actual_rivets(self):
        fixture, run, torb, sigma, actor = self.arena()
        self.deploy(fixture, run, torb, sigma, actor)
        x, y, z = sigma.mover.position
        fixture.place(torb, (x, y, z + 5))
        fixture.advance(run, torb, sigma, 4)
        for _ in range(400):
            fixture.step(run, torb, sigma, fire=1)
            if run.match.combat.is_dead(sigma):
                break
        self.assertTrue(run.match.combat.is_dead(sigma))
        self.assertEqual(actor.health, 700)
        self.assertNotIn(sigma.body, run.match.combat.sigma_barriers.actors)
        self.assertFalse(actor.active())

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_COLLISION"), "needs Orisa geometry")
    def test_nearer_sigma_mesh_intercepts_before_orisa_then_recall_exposes_it(self):
        fixture, run, torb, sigma, near = self.arena()
        orisa = run.players[2]
        run.match.switch_hero(orisa, heroes()[0x02E000000000013E])
        x, y, z = torb.mover.position
        fixture.place(sigma, (x + 2.2, y, z + 8))
        fixture.place(orisa, (x, y, z + 10))
        fixture.advance(run, torb, sigma, 35)
        run.step({sigma: (-32768, 0, 2), orisa: (-32768, 8192, 16)})
        for _ in range(14):
            run.step({sigma: (-32768, 0, 2), orisa: (-32768, 8192, 0)})
        for _ in range(21):
            run.step({orisa: (-32768, 8192, 0)})
        far = run.match.combat.orisa_barriers.actors[orisa.body]
        self.assertEqual(near.mode, 3)
        run.step({torb: (0, 0, 1)})
        fixture.advance(run, torb, orisa, 45)
        self.assertEqual(near.health, 630)
        self.assertEqual(far.health, 600)
        run.step({sigma: (-32768, 0, 2)})
        self.assertFalse(near.active())
        run.step({torb: (0, 0, 1)})
        fixture.advance(run, torb, orisa, 40)
        self.assertEqual(far.health, 530)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        before = fixture.health(run, orisa).total
        fight = run.match.combat
        fixture.place(sigma, (x + 100, y, z + 100))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, orisa, 185)
        turret = fight.turrets[torb.entity]
        for _ in range(800):
            run.step({})
            self.assertEqual(fixture.health(run, orisa).total, before)
            self.assertEqual(far.health, max(0, 530 - turret.shots * 14))
            if not far.active():
                break
        self.assertFalse(far.active())
        self.assertEqual(far.health, 0)
        self.assertEqual(turret.shots, 38)
        shots = turret.shots
        for _ in range(30):
            run.step({})
            if turret.shots > shots:
                break
        self.assertEqual(turret.shots, shots + 1)
        self.assertEqual(fixture.health(run, orisa).total, before - 9)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 9)

    def test_deployed_turret_shoots_parked_mesh(self):
        fixture, run, torb, sigma, actor = self.arena()
        x, y, z = sigma.mover.position
        fixture.place(sigma, (x, y, z + 30))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, sigma, 100)
        turret, = run.match.combat.turrets.values()
        x, y, z = turret.actor.position
        fixture.place(sigma, (x, y, z + 8))
        hp = fixture.health(run, sigma)
        before = hp.total
        self.deploy(fixture, run, torb, sigma, actor)
        fixture.advance(run, torb, sigma, 100)
        self.assertGreater(turret.shots, 0)
        self.assertEqual(actor.health, 700 - turret.shots * 14)
        self.assertEqual(hp.total, before)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        shots = turret.shots
        shield_health = actor.health
        fixture.step(run, torb, sigma, defense=2, defender_aim=(-32768, 0))
        self.assertFalse(actor.active())
        for _ in range(30):
            if hp.total < before:
                break
            run.step({})
        self.assertEqual(turret.shots, shots + 1)
        self.assertEqual(actor.health, shield_health)
        self.assertEqual(hp.total, before - 14)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 14)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 14)

    def test_hypersphere_impact_and_splash_respect_mesh_team_and_break_frame(self):
        for airburst in (False, True):
            for mode in ("intact", "break", "allied"):
                with self.subTest(airburst=airburst, mode=mode):
                    self.check_hypersphere_mesh(airburst, mode)

    def mesh_duel(self, mode):
        fixture, run, torb, sigma, actor = self.arena()
        match = run.match
        caster = run.players[2]
        self.assertTrue(match.switch_hero(caster, heroes()[sigma_grasp.HERO]))
        match.game_map = replace(match.game_map, free_for_all=False)
        caster.team = 1 - torb.team
        sigma.team = caster.team if mode == "allied" else torb.team
        self.deploy(fixture, run, torb, sigma, actor)
        return fixture, run, torb, sigma, caster, actor

    def check_hypersphere_mesh(self, airburst, mode):
        fixture, run, torb, sigma, caster, actor = self.mesh_duel(mode)
        fight = run.match.combat
        x, _, z = actor.position
        y = torb.mover.position[1]
        fixture.place(torb, (x, y, z + (.5 if airburst else 1.3)))
        fixture.place(caster, (x, y, z - (23.1 if airburst else 5.5)))
        fixture.place(sigma, (x + 10, y, z + 5))
        if mode == "break":
            fight.sigma_barriers.damage(actor, 680 if airburst else 650)
            self.assertTrue(actor.active())
        run.step({torb: (0, 0, 16)})
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        yaw, pitch = (0, 0) if airburst else fixture.body_aim(run, caster, torb)
        run.step({caster: (yaw, pitch, 1)})
        launches = set(fight.rockets)
        broke = False
        for _ in range(75):
            run.step({caster: (yaw, pitch, 0)})
            launches.update(fight.rockets)
            if mode == "break" and not actor.active() and not broke:
                broke = True
                self.assertEqual(hp.over_armour.amount, 100)
                self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(len(launches), 2)
        self.assertFalse(fight.rockets)
        if airburst:
            self.assertTrue(all(rocket.left <= combat.EPSILON for rocket in launches))
        hits = [22.760067] if airburst else [20, 25]
        if mode == "intact":
            self.assertEqual(actor.health, 640 if airburst else 590)
            hits = []
        elif mode == "break":
            self.assertTrue(broke)
            self.assertEqual(actor.health, 0)
        else:
            self.assertEqual(actor.health, 700)
            hits *= 2
        body_damage = sum(hits)
        self.assertAlmostEqual(hp.over_armour.amount, 100 - body_damage, places=5)
        self.assertEqual(hp.now, base)
        self.assertAlmostEqual(caster.stats.value(stats.HERO_DAMAGE), body_damage, places=5)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in caster.client.messages(combat.HIT)],
                         [round(amount) for amount in hits])

    def test_accretion_impact_respects_mesh_team_and_break_frame(self):
        for wall_burst in (False, True):
            for mode in ("intact", "break", "allied"):
                with self.subTest(wall_burst=wall_burst, mode=mode):
                    self.check_accretion_mesh(wall_burst, mode)

    def check_accretion_mesh(self, wall_burst, mode):
        fixture, run, torb, sigma, caster, actor = self.mesh_duel(mode)
        fight = run.match.combat
        x, _, z = actor.position
        y = torb.mover.position[1]
        if wall_burst:
            run.match.collision_world = World(
                box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                + wall(x + .2, z - 2.5, x + .2, z - .15, y0=y, y1=y + 4)
            )
            fixture.place(torb, (x - .8, y, z + .65))
            fixture.place(caster, (x - .9, y, z - 6))
            direction = aim(fight._eye(caster), (x + .2, y + 1.5, z - .8))
        else:
            fixture.place(torb, (x, y, z + 1.3))
            fixture.place(caster, (x, y, z - 5.5))
            direction = aim(fight._eye(caster), fight._eye(torb))
        fixture.place(sigma, (x + 10, y, z + 5))
        if mode == "break":
            capacity = 30 if wall_burst else 50
            fight.sigma_barriers.damage(actor, 700 - capacity)
            self.assertEqual(actor.health, capacity)
            self.assertTrue(actor.active())
        run.step({torb: (0, 0, 16)})
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        self.assertEqual(hp.over_armour.amount, 100)
        run.step({caster: (*direction, 16)})
        for _ in range(65):
            if fight.rockets:
                break
            run.step({caster: (*direction, 0)})
        rock, = fight.rockets
        self.assertIsNotNone(rock.accretion_source)
        for _ in range(30):
            if not fight.rockets:
                break
            run.step({})
        self.assertFalse(fight.rockets)

        if mode == "allied":
            self.assertEqual(actor.health, 700)
            hits = [35] if wall_burst else [25, 35]
        else:
            self.assertEqual(actor.health, 0 if mode == "break" else (660 if wall_burst else 630))
            hits = []
        self.assertEqual(hp.over_armour.amount, 100 - sum(hits))
        self.assertEqual(hp.now, base)
        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), sum(hits))
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in caster.client.messages(combat.HIT)], hits)
        if mode == "allied" and not wall_burst:
            self.assertEqual(fight.stuns[torb.body] - fight.tick, 50)
        else:
            self.assertNotIn(torb.body, fight.stuns)
        if mode == "break":
            self.assertFalse(actor.active())
            self.assertGreater(actor.source.vars[61].value(), 4.9)
        else:
            self.assertTrue(actor.active())

    def test_turret_breaks_experimental_barrier_without_spill_then_hits_sigma(self):
        for close, break_shot, residual in ((True, 41, 1), (False, 42, 0)):
            with self.subTest(close=close):
                self.check_turret_break(close, break_shot, residual)

    def check_turret_break(self, close, break_shot, residual):
        fixture, run, torb, sigma, actor = self.arena()
        fight = run.match.combat
        x, y, z = sigma.mover.position
        fixture.place(sigma, (x, y, z + 30))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, sigma, 100)
        turret = fight.turrets[torb.entity]
        x, y, z = turret.actor.position
        if close:
            fixture.place(torb, (x, y, z + 1))
        fixture.place(sigma, (x, y, z + 8))
        hp = fixture.health(run, sigma)
        before = hp.total
        self.deploy(fixture, run, torb, sigma, actor)
        fixture.step(run, torb, sigma, fire=2)
        for _ in range(25):
            if actor.health < 700:
                break
            run.step({})
        capacity = actor.health
        if close:
            self.assertEqual(capacity, 575)
        else:
            self.assertGreater(capacity, 575)
            self.assertLess(capacity, 588)
        self.assertEqual(turret.shots, 0)

        for _ in range(1050):
            run.step({})
            self.assertEqual(actor.health, max(0, capacity - turret.shots * 14))
            self.assertEqual(hp.total, before)
            if not actor.active():
                break
        self.assertEqual(turret.shots, break_shot)
        self.assertEqual(actor.mode, 1)
        self.assertEqual(actor.health, residual)
        self.assertGreater(actor.source.vars[61].value(), 4.9)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)

        for _ in range(30):
            run.step({})
            if turret.shots > break_shot:
                break
        self.assertEqual(turret.shots, break_shot + 1)
        self.assertEqual(hp.total, before - 14)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 14)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 14)

        fixture.place(sigma, (x + 45, y, z + 8))
        fixture.step(run, torb, sigma, defense=2, defender_aim=(-32768, 0))
        fixture.advance(run, torb, sigma, 25)
        self.assertFalse(actor.active())
        self.assertGreater(actor.source.vars[61].value(), 0)
        for _ in range(320):
            if actor.source.vars[61].value() <= 0:
                break
            run.step({})
        self.assertEqual(actor.source.vars[61].value(), 0)
        self.assertGreater(actor.health, 0)
        self.deploy(fixture, run, torb, sigma, actor)
