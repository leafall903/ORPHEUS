"""Zarya fire against a deployed Orisa barrier."""

import os
import unittest

from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.collision import flat_world
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "requires Orisa 4062 mesh")
class ZaryaOrisaBarrierTests(unittest.TestCase):
    def test_weapons_against_deployed_barrier(self):
        for case in ("beam", "grenade", "break", "ally beam", "ally grenade"):
            with self.subTest(case=case):
                self.check_case(case)

    def launch_arena(self, *, allied=False):
        run = Run(names=("Orisa", "Zarya", "Target"))
        orisa, zarya, target = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.collision_world = flat_world(1)
        self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        self.assertTrue(match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        orisa.team, zarya.team, target.team = 0, 0 if allied else 1, 1 if allied else 0
        for player, x in ((orisa, 0), (zarya, 10), (target, 12)):
            player.mover.position[:] = [x, 1, 0]
            player.mover.body = None
        fight = match.combat
        run.step({orisa: (0, 0, 16)})
        for _ in range(120):
            if any(rocket.orisa_launch is not None for rocket in fight.rockets):
                break
            run.step({})
        rocket, = fight.rockets
        self.assertIsNotNone(rocket.orisa_launch)
        self.assertFalse(fight.reinhardt_barriers.orisa_pending)
        return run, fight, orisa, zarya, target

    def pending_arena(self, *, allied=False):
        run, fight, orisa, zarya, target = self.launch_arena(allied=allied)
        pending = fight.reinhardt_barriers.orisa_pending
        for _ in range(120):
            if pending:
                break
            run.step({})
        self.assertEqual(len(pending), 1)
        return run, fight, orisa, zarya, target

    def arena(self, *, allied=False):
        run, fight, orisa, zarya, target = self.pending_arena(allied=allied)
        manager = fight.reinhardt_barriers
        due, _, _ = manager.orisa_pending[0]
        while run.frame < due:
            run.step({})
        actor, = manager.orisa.values()
        self.assertEqual((actor.owner, actor.team, actor.health), (orisa, orisa.team, 600))
        self.assertEqual(actor.until - fight.tick, 1250)
        self.assertEqual(len(actor.geometry), 460)
        x, y, z = actor.position
        for player, dx, dz in (
            (orisa, 8 if allied else 0, -1),
            (zarya, 0, 5),
            (target, 0 if allied else 10, -3),
        ):
            player.mover.position[:] = [x + dx, y, z + dz]
            player.mover.body = None
        run.step({})
        for body in fight._targets():
            fight.vitals_of(body)
        return run, fight, orisa, zarya, target, actor

    def test_sparse_update_preserves_inflight_deployment_and_following_grenade_contact(self):
        reference, ref_fight, _, _, _ = self.pending_arena()
        due, _, position = ref_fight.reinhardt_barriers.orisa_pending[0]
        x, y, z = position
        self.assertEqual(due - reference.frame, 7)
        for sparse, shoot, expire in ((False, True, False), (True, True, False),
                                      (True, False, False), (True, False, True)):
            with self.subTest(sparse=sparse, shoot=shoot, expire=expire):
                run, fight, orisa, zarya, target = self.launch_arena()
                service = fight.reinhardt_barriers
                deploy, = fight.rockets
                self.assertLess(run.frame, reference.frame)
                next_entity = fight.next_bot
                if shoot:
                    while run.frame < reference.frame - 2:
                        run.step({})
                    for player, dx, dz in ((orisa, 10, -1), (zarya, 0, 11), (target, 0, -1)):
                        player.mover.position[:] = (x + dx, y, z + dz)
                        player.mover.body = None
                    receiver = next(body for body in fight._targets() if body.owner is target)
                    vitals = fight.vitals_of(receiver)
                    before = vitals.total
                    angles = aim(fight._eye(zarya), (x, y + 1.4, z))
                    run.step({zarya: (*angles, 2)})
                    grenade, = [rocket for rocket in fight.rockets if rocket is not deploy]
                self.assertIn(deploy, fight.rockets)
                self.assertFalse(service.orisa_pending)
                endpoint = due + (1250 if expire else 40)
                ticks = (endpoint,) if sparse else range(run.frame + 1, endpoint + 1)
                for tick in ticks:
                    run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertEqual(deploy.last_tick, reference.frame)
                self.assertFalse(service.orisa_pending)
                self.assertFalse(fight.rockets)
                self.assertEqual(fight.next_bot, next_entity + 1)
                if expire:
                    self.assertFalse(service.orisa)
                    continue
                actor, = service.orisa.values()
                self.assertEqual(actor.position, position)
                self.assertEqual(actor.until, due + 1250)
                self.assertEqual(actor.health, 553 if shoot else 600)
                if shoot:
                    self.assertGreater(grenade.last_tick, due)
                    self.assertEqual(vitals.total, before)

    def test_grenade_respects_pending_barrier_deployment_in_sparse_updates(self):
        for timing, launch_distance in (("before", 0.7), ("same tick", 1.5), ("after", 7)):
            for sparse in (False, True):
                with self.subTest(timing=timing, sparse=sparse):
                    run, fight, orisa, zarya, target = self.pending_arena()
                    manager = fight.reinhardt_barriers
                    due, _, (x, y, z) = manager.orisa_pending[0]
                    for player, dx, dz in (
                        (orisa, 10, -1),
                        (zarya, 0, launch_distance),
                        (target, 0, -1),
                    ):
                        player.mover.position[:] = (x + dx, y, z + dz)
                        player.mover.body = None
                    receiver = next(body for body in fight._targets() if body.owner is target)
                    vitals = fight.vitals_of(receiver)
                    before = vitals.total
                    point = (x, y + 1.4, z if timing == "after" else z - 1)
                    angles = aim(fight._eye(zarya), point)
                    run.step({zarya: (*angles, 2)})
                    self.assertLess(run.frame, due)
                    rocket, = fight.rockets
                    end = due + 35
                    ticks = (end,) if sparse else range(run.frame + 1, end + 1)
                    for tick in ticks:
                        run.match.update(tick * combat.TICK, tick, frames=False)
                    actor, = manager.orisa.values()
                    self.assertFalse(manager.orisa_pending)
                    self.assertFalse(fight.rockets)
                    if timing == "after":
                        self.assertGreater(rocket.last_tick, due)
                        self.assertEqual(actor.health, 553)
                        self.assertEqual(vitals.total, before)
                    else:
                        if timing == "before":
                            self.assertLess(rocket.last_tick, due)
                        else:
                            self.assertEqual(rocket.last_tick, due)
                        self.assertEqual(actor.health, 600)
                        self.assertLess(vitals.total, before)

    def check_case(self, case):
        allied = case.startswith("ally")
        secondary = case not in ("beam", "ally beam")
        run, fight, orisa, zarya, target, actor = self.arena(allied=allied)
        manager = fight.reinhardt_barriers
        x, y, z = actor.position
        orisa_hp = fight.vitals[orisa.body].total
        target_hp = fight.vitals[target.body].total
        if case == "break":
            manager.damage(actor, 570)
            self.assertEqual(actor.health, 30)
        angles = aim(fight._eye(zarya), (x, y + 1.4, z - 3 if allied else z))
        for frame in range(55 if secondary else 31):
            buttons = (2 if frame == 0 else 0) if secondary else 1
            run.step({zarya: (*angles, buttons)})
        self.assertAlmostEqual(actor.health, {"beam": 564, "grenade": 553, "break": 0}.get(case, 600))
        self.assertEqual(fight.vitals[orisa.body].total, orisa_hp)
        if allied:
            self.assertLess(fight.vitals[target.body].total, target_hp)
        else:
            self.assertFalse(zarya.client.messages(combat.HIT))
        if case == "break":
            self.assertNotIn(actor.entity, manager.orisa)

    def test_quick_melee_hits_enemy_mesh_and_leaves_friendly_mesh_intact(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, fight, orisa, zarya, target, actor = self.arena(allied=allied)
                x, y, z = actor.position
                victim = target if allied else orisa
                for player, offset in ((zarya, 1.8), (victim, -0.65)):
                    player.mover.position[:] = [x, y, z + offset]
                    player.mover.body = None
                run.step({})
                before = fight.vitals[victim.body].total
                angles = aim(fight._eye(zarya), (x, y + 1.4, z))
                for frame in range(30):
                    run.step({zarya: (*angles, 0x800 if frame == 0 else 0)})
                self.assertEqual(actor.health, 600 if allied else 570)
                self.assertEqual(fight.vitals[victim.body].total, before - (30 if allied else 25))

    def test_graviton_intercepts_enemy_mesh_and_crosses_friendly_mesh(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, fight, orisa, zarya, target, actor = self.arena(allied=allied)
                x, y, z = actor.position
                victim = target if allied else orisa
                before = fight.vitals[victim.body].total
                component = zarya.body_script.component
                zarya.body_script.set_ult_charge(component.entity_var(ULT_COST).value())
                point = (x, y + 1.45, z - 3 if allied else z)
                angles = aim(fight._eye(zarya), point)
                run.step({zarya: (*angles, 0x20)})
                for _ in range(100):
                    if fight.zarya_gravitons.fields:
                        break
                    run.step({})
                field, = fight.zarya_gravitons.fields
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(field.end_tick - field.last_tick, 250)
                self.assertEqual(actor.health, 600 if allied else 595)
                if allied:
                    self.assertLess(field.position[2], z - 2)
                else:
                    self.assertIsNotNone(actor.intersection(field.position, (0, 0, -1), 0.05))
                self.assertEqual(fight.vitals[victim.body].total, before - (5 if allied else 0))
                start = victim.mover.position[2]
                for _ in range(32):
                    run.step({})
                self.assertLess(fight.vitals[victim.body].total, before)
                if not allied:
                    self.assertGreater(victim.mover.position[2], start)
                while fight.tick < field.end_tick:
                    run.step({})
                self.assertFalse(fight.zarya_gravitons.fields)
                self.assertEqual(actor.health, 600 if allied else 595)

    def test_expiry_exposes_owner_to_beam(self):
        run, fight, orisa, zarya, _, actor = self.arena()
        manager = fight.reinhardt_barriers
        x, y, z = actor.position
        angles = aim(fight._eye(zarya), (x, y + 1.45, z - 1))
        before = fight.vitals[orisa.body].total
        while fight.tick < actor.until - 1:
            run.step({})
        self.assertIn(actor.entity, manager.orisa)
        run.step({})
        self.assertEqual(fight.tick, actor.until)
        self.assertNotIn(actor.entity, manager.orisa)
        for _ in range(31):
            run.step({zarya: (*angles, 1)})
        self.assertLess(fight.vitals[orisa.body].total, before)

    def test_cooldown_rejects_early_press_then_replacement_retires_old_mesh(self):
        run, fight, orisa, _, _, actor = self.arena()
        manager = fight.reinhardt_barriers
        run.step({orisa: (0, 0, 16)})
        for _ in range(120):
            self.assertFalse(any(rocket.orisa_launch is not None for rocket in fight.rockets))
            self.assertFalse(manager.orisa_pending)
            run.step({})
        self.assertEqual(list(manager.orisa), [actor.entity])
        while fight.tick < actor.until - 300:
            run.step({})
        run.step({orisa: (16384, 0, 16)})
        for _ in range(120):
            if actor.entity not in manager.orisa:
                break
            run.step({})
        replacement, = manager.orisa.values()
        self.assertNotEqual(replacement.entity, actor.entity)
        self.assertLess(fight.tick, actor.until)
        self.assertEqual(replacement.until - fight.tick, 1250)
        self.assertEqual(replacement.health, 600)
        self.assertNotEqual(replacement.position, actor.position)

    def test_earthshatter_respects_enemy_mesh_even_when_it_breaks(self):
        for case in ("enemy", "break", "ally", "adjacent", "adjacent break", "in front"):
            with self.subTest(case=case):
                run, fight, orisa, zarya, rein, actor = self.arena()
                self.assertTrue(run.match.switch_hero(rein, heroes()[0x02E0000000000007]))
                rein.team, zarya.team = (0, 1) if case == "ally" else (1, 0)
                x, y, z = actor.position
                for player, dx, dz in ((rein, 0, 8), (zarya, 0, -3), (orisa, 9, -2)):
                    player.mover.position[:] = [x + dx, y, z + dz]
                    player.mover.body = None
                run.step({})
                contact = actor.intersection((x, y + 0.1, z + 8), (0, 0, -1), 11)
                self.assertIsNotNone(contact)
                if case.startswith("adjacent") or case == "in front":
                    side = 0.1 if case == "in front" else -0.1
                    zarya.mover.position[2] = z + 8 - contact + side
                    zarya.mover.body = None
                breaking = case.endswith("break")
                if breaking:
                    fight.reinhardt_barriers.damage(actor, 575)
                component = rein.body_script.component
                rein.body_script.set_ult_charge(component.entity_var(ULT_COST).value())
                before = fight.vitals[zarya.body].total
                run.step({rein: (-32768, 0, 0x20)})
                saw_wave = False
                for _ in range(70):
                    saw_wave |= bool(fight.shatters)
                    run.step({rein: (-32768, 0, 0)})
                self.assertTrue(saw_wave)
                self.assertFalse(fight.shatters)
                self.assertLess(component.entity_var(ULT_CHARGE).value(), 5)
                self.assertEqual(actor.health, 0 if breaking else 600 if case == "ally" else 550)
                exposed = case in ("ally", "in front")
                self.assertEqual(fight.vitals[zarya.body].total, before - (50 if exposed else 0))
                self.assertEqual(fight.is_stunned(zarya), exposed)
                if breaking:
                    self.assertNotIn(actor.entity, fight.reinhardt_barriers.orisa)
