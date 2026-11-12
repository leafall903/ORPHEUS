"""Live authored Ice Wall casts against Ashe and the two charge paths."""

import unittest

import test_ashe_pve_npcs as fixtures
from test_ashe_combat import aim
from test_game_mover import box

from ow174.game import combat, heavy_charge, immunity, mei_wall, npc, npc_control, stats
from ow174.game.collision import World
from ow174.game.commands import JUMP, Command
from ow174.game.content import find_hero, heroes


@unittest.skipUnless(fixtures.CATALOG.is_file() and fixtures.BUNDLE.is_file(), "requires external PvE inputs")
class AsheIceWallTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.AshePveNpcTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.AshePveNpcTests.tearDownClass.__func__)
    fight = fixtures.AshePveNpcTests.fight
    press = fixtures.AshePveNpcTests.press

    def duel(self, *, distance=12):
        run, owner, mei = self.fight(distance=distance)
        self.assertTrue(run.match.switch_hero(mei, heroes()[immunity.MEI]))
        mei.mover.position[:] = (0, 1, distance)
        for player in run.players:
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        return run, owner, mei

    def place(self, run, mei, *, z=6):
        wall, pieces = self.cast_wall(run, mei, z=z)
        fight = run.match.combat
        self.assertEqual(len(pieces), 5)
        self.assertEqual({piece.health for piece in pieces}, {400})
        self.assertTrue(all(fight.ice_walls.marked(piece) for piece in pieces))
        self.assertTrue(all(abs(piece.feet[2] - z) < 0.01 for piece in pieces))
        self.assertEqual(sorted(round(piece.feet[0], 2) for piece in pieces), [-3.1, -1.55, 0, 1.55, 3.1])
        self.assertGreater(wall.vars[61].value(), 11.9)
        centre = min(pieces, key=lambda piece: abs(piece.feet[0]))
        return wall, pieces, centre

    def cast_wall(self, run, mei, *, z=6, rotated=False):
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(mei), (0, 1, z))
        wall = next(item for item in mei.body_script.component.instances.values()
                    if item.graph.index == mei_wall.CAST_GRAPH)
        for button in ((16, 0, 2, 0, 1, 0) if rotated else (16, 0, 1, 0)):
            run.step({mei: (yaw, pitch, button)})
        return wall, tuple(fight.ice_walls.pieces.values())

    def test_live_ice_wall_stops_sleep_dart_before_mei(self):
        run, ana, mei = self.duel()
        self.assertTrue(run.match.switch_hero(ana, find_hero("ana")))
        ana.mover.position[:] = (0, 1, 0)
        for _ in range(35):
            run.step({})
        _, pieces, centre = self.place(run, mei)
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is mei)
        before = fight.vitals_of(target).total
        yaw, pitch = aim(fight._eye(ana), (0, 2.2, 12))
        run.step({ana: (yaw, pitch, 8)})
        for _ in range(60):
            run.step({ana: (yaw, pitch, 0)})
        self.assertEqual(centre.health, 395)
        self.assertTrue(all(piece.health == 400 for piece in pieces if piece is not centre))
        self.assertEqual(fight.vitals_of(target).total, before)
        self.assertFalse(fight.sleeps)
        self.assertFalse(fight.rockets)
        self.assertEqual(ana.client.messages(combat.HIT), [])

    def test_live_ice_wall_stops_bob_and_loses_175_health(self):
        run, owner, mei = self.duel()
        _, pieces, centre = self.place(run, mei)
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is mei))
        before = vitals.total
        owner.body_script.set_ult_charge(2240)
        self.press(run, owner, 32, frames=1)
        actor, = fight.companions.active.values()
        for _ in range(100):
            run.step({})
            if actor.phase != "charge":
                break
        self.assertEqual(actor.stopped_reason, "ice_wall")
        self.assertLess(actor.bot.position[2], centre.feet[2])
        self.assertEqual(centre.health, 225)
        self.assertTrue(all(piece.health == 400 for piece in pieces if piece is not centre))
        self.assertIsNone(actor.uppercut_target)
        self.assertEqual(vitals.total, before)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

        while run.frame < actor.ready + 40:
            run.step({})
        self.assertEqual(actor.phase, "gun")
        self.assertIsNone(actor.target)
        self.assertEqual(actor.shots, 0)
        self.assertEqual(centre.health, 225)
        self.assertEqual(vitals.total, before)
        run.step({mei: (0, 0, 16)})
        self.assertFalse(fight.ice_walls.pieces)
        for _ in range(60):
            run.step({})
            if vitals.total < before:
                break
        self.assertEqual(actor.target, mei.body)
        self.assertEqual(actor.shots, 1)
        self.assertEqual(vitals.total, before - 14)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 14)

    def test_live_ice_wall_runs_heavy_charge_damage_state(self):
        run, owner, mei = self.duel()
        definition = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        bot = npc.spawn(run.match, definition, (0, 1, 24), team=owner.team)
        fight = run.match.combat
        mei.mover.yaw, mei.mover.pitch = aim(fight._eye(mei), (0, 2, 24))
        self.assertEqual(npc_control.start(run.match, mei), bot.entity)
        _, pieces, centre = self.place(run, mei, z=18)
        charge = next(item for item in bot.body_script.component.instances.values()
                      if item.graph.index == heavy_charge.CHARGE)
        for _ in range(120):
            run.step({})
            if charge.vars[59].value() == 2:
                break
        self.assertEqual(charge.vars[59].value(), 2)
        self.assertGreater(charge.vars[61].value(), 14.9)
        damage = charge.states[charge.graph.node(102).state]
        self.assertEqual(damage.counter, 1)
        self.assertGreater(bot.position[2], centre.feet[2])
        self.assertEqual(centre.health, 225)
        self.assertTrue(all(piece.health == 400 for piece in pieces if piece is not centre))
        self.assertFalse(immunity.heavy_carry_active(mei, mei.body))

    def test_viper_and_coach_gun_hit_live_pillars_and_spare_mei(self):
        for button, amount in ((1, 40), (3, 75), (8, 90)):
            with self.subTest(button=button):
                run, owner, mei = self.duel()
                _, pieces, centre = self.place(run, mei, z=2.5)
                fight = run.match.combat
                vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is mei))
                before = vitals.total
                if button == 3:
                    for _ in range(40):
                        run.step({owner: (0, 0, 2)})
                    for _ in range(20):
                        run.step({owner: (0, 0, 3)})
                else:
                    self.press(run, owner, button)
                self.assertEqual(sum(400 - piece.health for piece in pieces), amount)
                self.assertLess(centre.health, 400)
                self.assertEqual(vitals.total, before)
                self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_ice_wall_cancel_retires_pillars_and_preserves_cooldown(self):
        run, _, mei = self.duel()
        wall, pieces, _ = self.place(run, mei)
        run.step({mei: (0, 0, 16)})
        self.assertFalse(run.match.combat.ice_walls.pieces)
        self.assertTrue(all(not piece.component.instances for piece in pieces))
        self.assertGreater(wall.vars[61].value(), 11.8)
        run.step({})
        for button in (16, 0, 1, 0):
            run.step({mei: (-32768, 2000, button)})
        self.assertFalse(run.match.combat.ice_walls.pieces)

    def test_dynamite_bounces_from_live_wall_and_blast_stops_at_it(self):
        run, owner, mei = self.duel(distance=7)
        _, _, centre = self.place(run, mei, z=5)
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is mei))
        before = vitals.total
        self.press(run, owner, 16, frames=1)
        for _ in range(30):
            if fight.dynamite.grenades:
                break
            run.step({})
        grenade, = fight.dynamite.grenades
        for _ in range(40):
            run.step({})
            if grenade.bounces:
                break
        self.assertEqual(grenade.bounces, 1)
        self.assertLess(grenade.velocity[2], 0)
        self.assertLess(grenade.position[2], centre.feet[2] - mei_wall.HALF_DEPTH)
        self.assertEqual(centre.health, 400)
        self.assertEqual(vitals.total, before)

        # Hold the blast fixture on the approach side, within range of Mei.
        grenade.position = (0, 1.8, 4)
        self.assertTrue(fight.dynamite.detonate(grenade))
        self.assertEqual(centre.health, 350)
        self.assertEqual(vitals.total, before)
        self.assertNotIn((owner.body, mei.body), fight.dynamite.burns)

    def test_ice_wall_authored_timer_and_body_retirement_remove_pillars(self):
        for replaced in (False, True):
            with self.subTest(replaced=replaced):
                run, _, mei = self.duel()
                _, pieces, centre = self.place(run, mei)
                fight = run.match.combat
                instance, = centre.component.instances.values()
                if replaced:
                    self.assertTrue(run.match.switch_hero(mei, mei.hero))
                else:
                    run.frame = centre.expires - 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertEqual(len(fight.ice_walls.pieces), 5)
                    self.assertTrue(instance.states[instance.graph.node(2).state].active)
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertEqual(instance.states[instance.graph.node(3).state].counter, 1)
                self.assertFalse(fight.ice_walls.pieces)
                self.assertTrue(all(not piece.component.instances for piece in pieces))

    def test_breaking_a_pillar_exposes_mei_to_the_next_viper_shot(self):
        run, owner, mei = self.duel()
        _, pieces, centre = self.place(run, mei)
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is mei))
        before = vitals.total
        _, pitch = aim(fight._eye(owner), (0, 1.85, 12))
        for _ in range(10):
            self.press(run, owner, 1, frames=20, pitch=pitch)
            if centre.entity not in fight.ice_walls.pieces:
                break
        self.assertEqual(centre.health, 0)
        self.assertEqual(vitals.total, before)
        self.assertTrue(all(piece.health == 400 for piece in pieces if piece is not centre))
        self.press(run, owner, 1, frames=20, pitch=pitch)
        self.assertEqual(vitals.total, before - 40)

    def test_quick_melee_hits_pillar_in_front_of_mei(self):
        run, owner, mei = self.duel(distance=2)
        _, _, centre = self.place(run, mei, z=1)
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is mei))
        before = vitals.total
        self.press(run, owner, 0x800, frames=40)
        self.assertEqual(centre.health, 370)
        self.assertEqual(vitals.total, before)

    def test_live_wall_stops_coach_self_recoil_and_enemy_knockback(self):
        for effect in ("recoil", "knockback"):
            for walled in (False, True):
                with self.subTest(effect=effect, walled=walled):
                    recoil = effect == "recoil"
                    run, owner, mei = self.duel(distance=7 if recoil else 1.5)
                    z = 2 if recoil else 3
                    if walled:
                        _, _, centre = self.place(run, mei, z=z)
                    fight = run.match.combat
                    victim = owner if recoil else mei
                    target = next(item for item in fight._targets() if item.owner is mei)
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    for frame in range(35):
                        run.step({owner: (-32768 if recoil else 0, 0, 8 if frame < 5 else 0)})
                    if walled:
                        radius, _ = victim.mover._capsule()
                        stop = centre.feet[2] - mei_wall.HALF_DEPTH - radius
                        self.assertAlmostEqual(victim.mover.position[2], stop, delta=0.01)
                        self.assertAlmostEqual(victim.mover.velocity[2], 0, delta=0.01)
                        self.assertEqual(centre.health, 400)
                    else:
                        self.assertGreater(victim.mover.position[2], z)
                    self.assertEqual(vitals.total, before - (0 if recoil else 90))

    def test_walking_slides_along_the_live_wall_and_crosses_after_cancel(self):
        run, owner, mei = self.duel()
        _, _, centre = self.place(run, mei, z=3)

        def walk(forward, right, frames):
            for _ in range(frames):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                owner.take_commands([Command(run.frame, forward=forward, right=right)])

        walk(127, 0, 75)
        radius, _ = owner.mover._capsule()
        stop = centre.feet[2] - mei_wall.HALF_DEPTH - radius
        self.assertAlmostEqual(owner.mover.position[2], stop, delta=0.01)
        walk(127, 70, 18)
        self.assertAlmostEqual(owner.mover.position[2], stop, delta=0.01)
        self.assertGreater(abs(owner.mover.position[0]), 0.3)
        self.assertGreater(abs(owner.mover.velocity[0]), 1)
        run.step({mei: (0, 0, 16)})
        self.assertFalse(run.match.combat.ice_walls.pieces)
        walk(127, 0, 45)
        self.assertGreater(owner.mover.position[2], centre.feet[2] + mei_wall.HALF_DEPTH)

    def test_coach_npc_knockback_resolves_live_pillar_contact(self):
        run, owner, mei = self.duel()
        _, _, centre = self.place(run, mei, z=3)
        trooper = next(item for item in self.catalog.values() if item.name == "Trooper")
        bot = npc.spawn(run.match, trooper, (0, 1, 1), team=mei.team)
        fight = run.match.combat
        vitals = fight.vitals[bot.entity]
        before = vitals.total
        _, pitch = aim(fight._eye(owner), (0, 1.85, 1))
        self.press(run, owner, 8, frames=35, pitch=pitch)
        motion = fight.impulses.npcs[bot.entity]
        radius, _ = motion.mover._capsule()
        stop = centre.feet[2] - mei_wall.HALF_DEPTH - radius
        self.assertAlmostEqual(bot.position[2], stop, delta=0.01)
        self.assertAlmostEqual(bot.velocity[2], 0, delta=0.01)
        self.assertEqual(vitals.total, before - 90)
        self.assertEqual(centre.health, 400)

    def test_off_centre_prop_rejects_only_the_intersecting_pillar(self):
        for rotated in (False, True):
            with self.subTest(rotated=rotated):
                run, _, mei = self.duel()
                bounds = (-0.1, 0.1, 3, 3.2, 6.5, 6.7) if rotated else (0.5, 0.7, 3, 3.2, 5.8, 6.2)
                run.match.collision_world = World(self.floor(-20, 20) + box(*bounds))
                wall, pieces = self.cast_wall(run, mei, rotated=rotated)
                offsets = [piece.feet[2] - 6 if rotated else piece.feet[0] for piece in pieces]
                self.assertEqual(sorted(round(offset, 2) for offset in offsets), [-3.1, -1.55, 1.55, 3.1])
                self.assertGreater(wall.vars[61].value(), 11.9)

    def test_unsupported_footprint_keeps_preview_and_spends_no_cooldown(self):
        run, _, mei = self.duel()
        run.match.collision_world = World(self.floor(-0.4, 0.4))
        wall, pieces = self.cast_wall(run, mei)
        self.assertFalse(pieces)
        self.assertEqual(wall.vars[61].value(), 0)
        self.assertTrue(run.match.combat.ice_walls.inputs[mei.body].preview)

    def test_two_actual_walls_reject_overlap_then_allow_a_rotated_partial_cast(self):
        run, second, first = self.duel()
        self.assertTrue(run.match.switch_hero(second, first.hero))
        second.mover.position[:] = (0, 1, 0)
        _, original, _ = self.place(run, first)
        wall, pieces = self.cast_wall(run, second)
        self.assertEqual(pieces, original)
        self.assertEqual(wall.vars[61].value(), 0)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(second), (0, 1, 6))
        for button in (2, 0, 1, 0):
            run.step({second: (yaw, pitch, button)})
        placed = [piece for piece in fight.ice_walls.pieces.values() if piece.owner == second.body]
        self.assertEqual(len(placed), 4)
        self.assertEqual(sorted(round(piece.feet[2] - 6, 2) for piece in placed), [-3.1, -1.55, 1.55, 3.1])
        self.assertGreater(wall.vars[61].value(), 11.9)

    def test_live_wall_lifts_ashe_and_supports_walking_until_release(self):
        for release in ("cancel", "walk off"):
            with self.subTest(release=release):
                run, owner, mei = self.duel()
                fight = run.match.combat
                vitals = fight.vitals_of(next(item for item in fight._targets() if item.owner is owner))
                before = vitals.total
                _, _, centre = self.place(run, mei, z=0)
                top = centre.feet[1] + centre.height
                self.assertAlmostEqual(owner.mover.position[1], top, delta=0.01)
                self.assertFalse(owner.mover.airborne)
                for _ in range(30):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    owner.take_commands([Command(run.frame, right=127)])
                    self.assertAlmostEqual(owner.mover.position[1], top, delta=0.01)
                    self.assertFalse(owner.mover.airborne)
                self.assertGreater(abs(owner.mover.position[0]), 1)
                if release == "cancel":
                    run.step({mei: (0, 0, 16)})
                    self.assertFalse(fight.ice_walls.pieces)
                else:
                    for _ in range(70):
                        run.frame += 1
                        run.match.update(run.frame * combat.TICK, run.frame)
                        owner.take_commands([Command(run.frame, right=127)])
                        if owner.mover.airborne:
                            break
                    self.assertTrue(fight.ice_walls.pieces)
                    self.assertGreater(abs(owner.mover.position[0]), 4)
                run.step({})
                self.assertTrue(owner.mover.airborne)
                self.assertLess(owner.mover.velocity[1], 0)
                for _ in range(100):
                    run.step({})
                self.assertAlmostEqual(owner.mover.position[1], 1, delta=0.01)
                self.assertFalse(owner.mover.airborne)
                self.assertEqual(vitals.total, before)

    def test_wall_omits_occupied_pillar_when_ashe_cannot_fit_above_it(self):
        run, owner, mei = self.duel()
        run.match.collision_world = World(self.floor(-20, 20) + box(-0.4, 0.4, 6, 6.2, -0.4, 0.4))
        wall, pieces = self.cast_wall(run, mei, z=0)
        self.assertEqual(sorted(round(piece.feet[0], 2) for piece in pieces), [-3.1, -1.55, 1.55, 3.1])
        self.assertGreater(wall.vars[61].value(), 11.9)
        self.assertAlmostEqual(owner.mover.position[1], 1, delta=0.01)
        self.assertFalse(owner.mover.airborne)

    def test_ashe_jumps_from_live_wall_and_falls_when_viper_breaks_the_support(self):
        run, owner, mei = self.duel()
        _, pieces, centre = self.place(run, mei, z=0)
        fight = run.match.combat
        top = centre.feet[1] + centre.height
        run.step({owner: (0, 0, JUMP)})
        self.assertTrue(owner.mover.airborne)
        self.assertGreater(owner.mover.velocity[1], 0)
        for _ in range(100):
            run.step({})
            if not owner.mover.airborne:
                break
        self.assertFalse(owner.mover.airborne)
        self.assertAlmostEqual(owner.mover.position[1], top, delta=0.01)

        centre.health = 40  # A damaged pillar lets one real Viper shot exercise support destruction.
        self.press(run, owner, 1, frames=12, pitch=16000)
        self.assertEqual(centre.health, 0)
        self.assertNotIn(centre.entity, fight.ice_walls.pieces)
        neighbours = {piece.entity for piece in pieces if piece is not centre}
        self.assertEqual(set(fight.ice_walls.pieces), neighbours)
        self.assertTrue(owner.mover.airborne)
        self.assertLess(owner.mover.position[1], top)
        self.assertLess(owner.mover.velocity[1], 0)
        for _ in range(100):
            run.step({})
        self.assertAlmostEqual(owner.mover.position[1], 1, delta=0.01)
        self.assertFalse(owner.mover.airborne)

    def test_live_wall_lifts_all_five_npcs_and_releases_their_movers(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, _, mei = self.duel()
                fight = run.match.combat
                bot = npc.spawn(run.match, definition, (0, 1, 6), team=1 - mei.team)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                _, _, centre = self.place(run, mei)
                top = centre.feet[1] + centre.height
                motion = fight.impulses.npcs[bot.entity]
                self.assertAlmostEqual(bot.position[1], top, delta=0.01)
                self.assertIs(motion.bot, bot)
                self.assertFalse(motion.mover.airborne)
                for _ in range(18):
                    run.step({})
                    self.assertAlmostEqual(bot.position[1], top, delta=0.01)
                    self.assertIs(fight.impulses.npcs[bot.entity], motion)
                    self.assertFalse(motion.mover.airborne)
                run.step({mei: (0, 0, 16)})
                run.step({})
                self.assertFalse(fight.ice_walls.pieces)
                self.assertTrue(motion.mover.airborne)
                self.assertLess(bot.velocity[1], 0)
                for _ in range(100):
                    run.step({})
                    if bot.entity not in fight.impulses.npcs:
                        break
                self.assertAlmostEqual(bot.position[1], 1, delta=0.01)
                self.assertNotIn(bot.entity, fight.impulses.npcs)
                self.assertEqual(vitals.total, before)

    def test_charging_bob_keeps_its_charge_while_lifted_and_falls_after_cancel(self):
        run, owner, mei = self.duel()
        fight = run.match.combat
        owner.body_script.set_ult_charge(2240)
        self.press(run, owner, 32, frames=1)
        actor, = fight.companions.active.values()
        for _ in range(60):
            run.step({})
            if actor.bot.position[2] >= 4.5:
                break
        charge_until = actor.charge_until
        _, pieces = self.cast_wall(run, mei, rotated=True)
        self.assertEqual(len(pieces), 5)
        top = pieces[0].feet[1] + pieces[0].height
        self.assertEqual(actor.phase, "charge")
        self.assertEqual(actor.charge_until, charge_until)
        self.assertAlmostEqual(actor.bot.position[1], top, delta=0.01)
        self.assertFalse(actor.mover.airborne)
        self.assertNotIn(actor.bot.entity, fight.impulses.npcs)
        z = actor.bot.position[2]
        for _ in range(6):
            run.step({})
            self.assertAlmostEqual(actor.bot.position[1], top, delta=0.01)
        self.assertGreater(actor.bot.position[2], z)
        self.assertTrue(all(piece.health == 400 for piece in pieces))
        run.step({mei: (0, 0, 16)})
        run.step({})
        self.assertTrue(actor.mover.airborne)
        self.assertLess(actor.bot.velocity[1], 0)
        self.assertEqual(actor.phase, "charge")
        self.assertEqual(actor.charge_until, charge_until)
        self.assertIsNone(actor.uppercut_target)

    def test_coach_pushes_a_supported_npc_off_the_live_wall(self):
        run, owner, mei = self.duel()
        fight = run.match.combat
        definition = next(item for item in self.catalog.values() if item.name == "Trooper")
        bot = npc.spawn(run.match, definition, (1, 1, 0), team=1 - owner.team)
        vitals = fight.vitals[bot.entity]
        before = vitals.total
        _, pieces, centre = self.place(run, mei, z=0)
        top = centre.feet[1] + centre.height
        motion = fight.impulses.npcs[bot.entity]
        self.assertAlmostEqual(bot.position[1], top, delta=0.01)
        yaw, pitch = aim(fight._eye(owner), (1, top + 0.85, 0))
        for frame in range(35):
            run.step({owner: (yaw, pitch, 8 if frame < 5 else 0)})
        self.assertEqual(vitals.total, before - 90)
        self.assertIs(fight.impulses.npcs[bot.entity], motion)
        self.assertTrue(motion.mover.airborne)
        edge = max(piece.feet[0] + piece.width / 2 for piece in pieces)
        self.assertGreater(bot.position[0], edge + motion.mover.data.stand_radius)
        for _ in range(110):
            run.step({})
            if bot.entity not in fight.impulses.npcs:
                break
        self.assertTrue(fight.ice_walls.pieces)
        self.assertAlmostEqual(bot.position[1], 1, delta=0.01)
        self.assertNotIn(bot.entity, fight.impulses.npcs)

    def test_npc_falls_on_the_tick_its_pillar_expires(self):
        run, owner, mei = self.duel()
        fight = run.match.combat
        definition = next(item for item in self.catalog.values() if item.name == "Trooper")
        bot = npc.spawn(run.match, definition, (0, 1, 6), team=1 - owner.team)
        _, _, centre = self.place(run, mei)
        top = centre.feet[1] + centre.height
        motion = fight.impulses.npcs[bot.entity]
        while run.frame < centre.expires - 1:
            run.step({})
        self.assertAlmostEqual(bot.position[1], top, delta=0.01)
        self.assertFalse(motion.mover.airborne)
        self.assertIs(fight.impulses.npcs[bot.entity], motion)
        run.step({})
        self.assertFalse(fight.ice_walls.pieces)
        self.assertTrue(motion.mover.airborne)
        self.assertLess(bot.velocity[1], 0)

    @staticmethod
    def floor(low, high):
        return [
            (low, 1, -100, low, 1, 100, high, 1, 100),
            (low, 1, -100, high, 1, 100, high, 1, -100),
        ]
