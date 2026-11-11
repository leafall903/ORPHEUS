"""Sombra's actual inputs against Reinhardt's authored defensive Pet."""

import unittest
from dataclasses import replace

import test_game_sombra_combat as scenes
from test_game_combat import KANEZAKA_DM, Run, aim, in_world

from ow174.game import combat, phase_defenses, reinhardt_barrier, world
from ow174.game.commands import FIRE, Command
from ow174.game.content import SOLDIER
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import HEALTH_POOL, SHIELDS_POOL


class SombraBarrierTests(unittest.TestCase):
    def projected_arena(self, caster_z=6):
        fixture = scenes.SombraCombatTests()
        fixture.setUp()
        fixture.run = Run(KANEZAKA_DM, ("Sombra", "Ally", "Zarya"))
        fixture.a, fixture.b, zarya = fixture.run.players
        match = fixture.run.match
        match.bots.clear()
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team, zarya.team = 0, 1, 1
        match.switch_hero(fixture.a, scenes.heroes()[scenes.SOMBRA])
        match.switch_hero(zarya, scenes.heroes()[reinhardt_barrier.ZARYA])
        for player, z in zip(fixture.run.players, (0, 1.5, caster_z), strict=True):
            player.mover.position[:] = [0, 0, z]
            player.mover.body = None
        fixture.geometry()
        fixture.press(0, 50)
        return fixture, zarya

    def test_actual_zarya_projected_bubble_protects_ally_and_credits_caster(self):
        fixture, zarya = self.projected_arena()
        match = fixture.run.match
        yaw, pitch = aim(match.combat._eye(zarya), (0, 1.3, 1.5))
        fixture.run.step({zarya: (yaw, pitch, 16)})
        fixture.press(0, 12)
        barrier, = match.combat.barriers.values()
        self.assertIs(barrier.owner, zarya)
        self.assertIs(barrier.protected, fixture.b)
        self.assertEqual(barrier.vitals.total, 200)
        child, = barrier.component.instances.values()
        self.assertEqual(child.graph.index, 0x256)
        self.assertEqual(child.source_entity(), zarya.body)

        fixture.press(FIRE)
        fixture.press(scenes.MELEE, 20)
        self.assertEqual(barrier.vitals.total, 162)
        self.assertEqual(fixture.health(fixture.b), 200)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 7.6, places=4)
        fixture.press(2, 60)
        self.assertNotIn(fixture.b.body, match.combat.hacks)
        match.switch_hero(fixture.b, fixture.b.hero)
        self.assertNotIn(barrier.entity, match.combat.barriers)
        self.assertFalse(barrier.component.instances)

    def test_actual_zarya_projected_bubble_cast_requires_visible_allied_target(self):
        for case in ("enemy", "wall", "range", "aim"):
            with self.subTest(case=case):
                fixture, zarya = self.projected_arena(caster_z=70 if case == "range" else 6)
                match = fixture.run.match
                if case == "enemy":
                    fixture.b.team = fixture.a.team
                elif case == "wall":
                    fixture.geometry((-3, 0, 4, 3, 0, 4, 3, 4, 4), (-3, 0, 4, 3, 4, 4, -3, 4, 4))
                yaw, pitch = aim(match.combat._eye(zarya), (0, 1.3, 1.5))
                fixture.run.step({zarya: (0 if case == "aim" else yaw, pitch, 16)})
                fixture.press(0, 20)
                self.assertFalse(match.combat.barriers)

    def zarya_arena(self):
        fixture = scenes.SombraCombatTests()
        fixture.setUp()
        match = fixture.run.match
        match.switch_hero(fixture.b, scenes.heroes()[reinhardt_barrier.ZARYA])
        fixture.b.mover.position[:] = (0, 0, 1.5)
        fixture.b.mover.body = None
        fixture.press(0, 40)
        fixture.run.step({fixture.b: (-32768, 0, 8)})
        fixture.press(0, 5)
        barrier, = match.combat.barriers.values()
        self.assertEqual(barrier.vitals.total, 200)
        child, = barrier.component.instances.values()
        self.assertEqual(child.graph.index, 0x256)
        self.assertEqual(child.source_entity(), fixture.b.body)
        return fixture, barrier

    def test_actual_zarya_self_bubble_blocks_pistol_melee_and_hack_until_expiry(self):
        fixture, barrier = self.zarya_arena()
        fixture.press(FIRE)
        self.assertEqual(barrier.vitals.total, 192)
        fixture.press(scenes.MELEE, 20)
        self.assertEqual(barrier.vitals.total, 162)
        self.assertEqual(fixture.health(fixture.b), 400)
        fixture.press(2, 70)
        self.assertFalse(fixture.hack_applications())
        fixture.press(0, 65)
        self.assertNotIn(barrier.entity, fixture.run.match.combat.barriers)
        fixture.press(2, 82)
        self.assertTrue(fixture.hack_applications())

    def test_actual_zarya_self_bubble_break_does_not_spill_pistol_damage(self):
        fixture, barrier = self.zarya_arena()
        for _ in range(116):
            fixture.press(FIRE)
            if barrier.vitals.total == 0:
                break
        self.assertEqual(barrier.vitals.total, 0)
        self.assertAlmostEqual(fixture.b.body_script.component.entity_var(87).value(), 40, places=4)
        self.assertEqual(fixture.health(fixture.b), 400)
        self.assertNotIn(barrier.entity, fixture.run.match.combat.barriers)
        fixture.press(FIRE, 12)
        self.assertLess(fixture.health(fixture.b), 400)

    def test_actual_emp_breaks_zarya_self_and_projected_bubbles(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                if projected:
                    fixture, zarya = self.projected_arena(caster_z=25)
                    yaw, pitch = aim(fixture.run.match.combat._eye(zarya), (0, 1.3, 1.5))
                    fixture.run.step({zarya: (yaw, pitch, 16)})
                    fixture.press(0, 12)
                    barrier, = fixture.run.match.combat.barriers.values()
                    self.assertIs(barrier.protected, fixture.b)
                else:
                    fixture, barrier = self.zarya_arena()
                fight = fixture.run.match.combat
                fixture.a.body_script.set_ult_charge(1400)
                fixture.press(32, 50)
                self.assertEqual(barrier.vitals.total, 0)
                self.assertNotIn(barrier.entity, fight.barriers)
                self.assertIn(fixture.b.body, fight.hacks)
                self.assertEqual(fixture.health(fixture.b), 200)
                if projected:
                    self.assertNotIn(zarya.body, fight.hacks)

    def test_paid_emp_breaks_zarya_bubble_during_world_only_update(self):
        fixture, barrier = self.zarya_arena()
        match = fixture.run.match
        fight = match.combat
        fixture.a.body_script.set_ult_charge(1400)
        fixture.press(0x20)
        cast = fixture.run.frame
        at = cast + 90
        match.update(at * combat.TICK, at)
        self.assertGreater(barrier.expiry_tick, at)
        self.assertNotIn(barrier.entity, fight.barriers)
        effect, = fight.hacks[fixture.b.body]
        self.assertEqual(effect.until_tick, cast + 334)
        self.assertEqual(fight.vitals[fixture.b.body].now[SHIELDS_POOL], 0)

    def test_actual_emp_excludes_covered_distant_and_allied_zarya_bubbles(self):
        for case in ("wall", "range", "ally"):
            with self.subTest(case=case):
                fixture, barrier = self.zarya_arena()
                match = fixture.run.match
                if case == "wall":
                    fixture.geometry((-3, 0, 0.75, 3, 0, 0.75, 3, 4, 0.75),
                                     (-3, 0, 0.75, 3, 4, 0.75, -3, 4, 0.75))
                elif case == "range":
                    fixture.b.mover.position[:] = (0, 0, 25)
                    fixture.b.mover.body = None
                else:
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.b.team = fixture.a.team
                fixture.a.body_script.set_ult_charge(1400)
                fixture.press(32, 50)
                self.assertEqual(barrier.vitals.total, 200)
                self.assertIn(barrier.entity, match.combat.barriers)
                self.assertNotIn(fixture.b.body, match.combat.hacks)

    def test_actual_zarya_bubble_energy_increases_beam_damage_against_sombra(self):
        fixture, barrier = self.zarya_arena()
        fixture.press(FIRE)
        fixture.press(scenes.MELEE, 20)
        energy = fixture.b.body_script.component.entity_var(87)
        self.assertEqual(barrier.vitals.total, 162)
        self.assertAlmostEqual(energy.value(), 7.6, places=4)
        fight = fixture.run.match.combat
        yaw, pitch = aim(fight._eye(fixture.b), (0, 1.3, 0))
        for _ in range(20):
            fixture.run.step({fixture.b: (yaw, pitch, FIRE)})
        self.assertAlmostEqual(fixture.health(fixture.a), 200 - (75 + 95 * 0.076) * 0.32, places=4)
        self.assertEqual(fixture.health(fixture.b), 400)
        fixture.wall()
        before = fixture.health(fixture.a)
        for _ in range(10):
            fixture.run.step({fixture.b: (yaw, pitch, FIRE)})
        self.assertEqual(fixture.health(fixture.a), before)
        fixture.geometry()
        fixture.press(0, 130)
        self.assertEqual(fixture.health(fixture.a), before)
        fixture.press(2, 82)
        self.assertIn(fixture.b.body, fight.hacks)
        for _ in range(20):
            fixture.run.step({fixture.b: (yaw, pitch, FIRE)})
        self.assertLess(fixture.health(fixture.a), before)

    def test_actual_zarya_secondary_uses_energy_and_spends_ammo_once(self):
        for charged in (False, True):
            with self.subTest(charged=charged):
                fixture, _ = self.zarya_arena()
                if charged:
                    fixture.press(FIRE)
                    fixture.press(scenes.MELEE, 20)
                fight = fixture.run.match.combat
                weapon = next(i for i in fixture.b.body_script.component.instances.values()
                              if i.graph.index == 0x0241)
                self.assertEqual(weapon.vars[53].value(), 100)
                fixture.press(2, shooter=fixture.b, target=fixture.a)
                shot, = fight.rockets
                self.assertIs(shot.shooter, fixture.b)
                self.assertEqual(shot.speed, 25)
                self.assertAlmostEqual(shot.gravity, 9.8, places=5)
                if charged:
                    self.assertGreater(shot.blast.amount, 47)
                else:
                    self.assertEqual(shot.blast.amount, 47)
                self.assertEqual(fixture.health(fixture.a), 200)
                fixture.press(0, 85)
                self.assertAlmostEqual(fixture.health(fixture.a), 200 - shot.blast.amount, places=5)
                self.assertEqual(weapon.vars[53].value(), 75)
                self.assertFalse(fight.rockets)

    def test_actual_zarya_secondary_respects_wall_aim_and_team(self):
        for case in ("wall", "miss", "allied"):
            with self.subTest(case=case):
                fixture, _ = self.zarya_arena()
                match = fixture.run.match
                if case == "wall":
                    fixture.wall()
                if case == "allied":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.b.team = fixture.a.team
                yaw = 0 if case == "miss" else -32768
                fixture.run.step({fixture.b: (yaw, 0, 2)})
                shot, = match.combat.rockets
                launch_height = shot.position[1]
                fixture.press(0, 8)
                self.assertEqual(fixture.health(fixture.a), 200)
                self.assertEqual(shot in match.combat.rockets, case != "wall")
                if case != "wall":
                    self.assertLess(shot.position[1], launch_height)
                    self.assertLess(shot.velocity[1], 0)

    def test_actual_zarya_secondary_wall_impact_splashes_only_in_range(self):
        for offset in (1.5, 4):
            with self.subTest(offset=offset):
                fixture, _ = self.zarya_arena()
                fixture.a.mover.position[:] = (offset, 0, 0)
                fixture.a.mover.body = None
                fixture.geometry((-5, 0, -0.5, 5, 0, -0.5, 5, 4, -0.5),
                                 (-5, 0, -0.5, 5, 4, -0.5, -5, 4, -0.5))
                fixture.run.step({fixture.b: (-32768, 0, 2)})
                fight = fixture.run.match.combat
                shot, = fight.rockets
                fixture.press(0, 15)
                self.assertNotIn(shot, fight.rockets)
                damage = 200 - fixture.health(fixture.a)
                if offset == 1.5:
                    self.assertGreater(damage, 0)
                    self.assertLess(damage, 47)
                else:
                    self.assertEqual(damage, 0)

    def test_actual_zarya_secondary_remains_available_under_hack_and_emp(self):
        for button in (2, 0x20):
            with self.subTest(button=button):
                fixture, barrier = self.zarya_arena()
                fight = fixture.run.match.combat
                fixture.press(0, 150)
                self.assertNotIn(barrier.entity, fight.barriers)
                fixture.a.body_script.set_ult_charge(1400 if button == 0x20 else 0)
                fixture.press(button, 85 if button == 2 else 50)
                self.assertIn(fixture.b.body, fight.hacks)
                fixture.press(2, shooter=fixture.b, target=fixture.a)
                fixture.press(0, 15)
                self.assertEqual(fixture.health(fixture.a), 153)
                self.assertIn(fixture.b.body, fight.hacks)

    def test_actual_zarya_secondary_interrupts_running_hack_on_impact(self):
        fixture, barrier = self.zarya_arena()
        fight = fixture.run.match.combat
        fixture.press(0, 150)
        self.assertNotIn(barrier.entity, fight.barriers)
        fixture.press(2, 12)
        hack = fixture.weapon().state(53)
        self.assertTrue(hack.flags & hack.RUNNING)
        target = aim(fight._eye(fixture.a), (0, 1, 1.5))
        shot = aim(fight._eye(fixture.b), (0, 1, 0))
        fixture.run.step({fixture.a: (*target, 2), fixture.b: (*shot, 2)})
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertTrue(hack.flags & hack.RUNNING)
        fixture.press(2, 30)
        self.assertEqual(fixture.health(fixture.a), 153)
        self.assertFalse(hack.flags & hack.RUNNING)
        self.assertGreater(hack.remaining(), 0)
        self.assertLess(hack.remaining(), 2)
        self.assertNotIn(fixture.b.body, fight.hacks)

    def test_actual_zarya_secondary_interrupts_camo_only_if_damage_reaches_owner(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                fixture, _ = self.zarya_arena()
                fixture.press(8)
                fixture.press(0, 60)
                camo = fixture.camo().state(13)
                self.assertTrue(camo.flags & camo.RUNNING)
                if wall:
                    fixture.wall()
                fixture.press(2, shooter=fixture.b, target=fixture.a)
                fixture.press(0, 65)
                self.assertEqual(fixture.health(fixture.a), 200 if wall else 153)
                self.assertEqual(bool(camo.flags & camo.RUNNING), wall)
                if not wall:
                    self.assertGreater(camo.remaining(), 0)

    def test_actual_zarya_secondary_lethal_hit_prevents_emp_pulse(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                fixture, _ = self.zarya_arena()
                fight = fixture.run.match.combat
                if lethal:
                    for _ in range(4):
                        fixture.press(2, shooter=fixture.b, target=fixture.a)
                        fixture.press(0, 65)
                    self.assertEqual(fixture.health(fixture.a), 12)
                    fixture.press(scenes.RELOAD, shooter=fixture.b, target=fixture.a)
                    fixture.press(0, 100)
                fixture.a.body_script.set_ult_charge(1400)
                emp = next(i for i in fixture.a.body_script.component.instances.values()
                           if i.graph.index == 0x0B31)
                pulse = emp.node_state(emp.graph.node(40))
                fixture.press(0x20)
                cast = emp.state(1)
                self.assertTrue(cast.flags & cast.RUNNING)
                self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), 0)
                fixture.press(2, shooter=fixture.b, target=fixture.a)
                fixture.press(0, 12)
                self.assertEqual(fixture.health(fixture.a), 0 if lethal else 153)
                self.assertEqual(pulse.counter, 0)
                fixture.press(0, 35)
                self.assertEqual(pulse.counter, 0 if lethal else 1)
                self.assertEqual(fixture.b.body in fight.hacks, not lethal)

    def test_actual_zarya_graviton_launch_uses_charge_arc_and_direct_hit(self):
        for case in ("enemy", "wall", "allied", "miss"):
            with self.subTest(case=case):
                fixture, _ = self.zarya_arena()
                match = fixture.run.match
                fight = match.combat
                script = fixture.b.body_script
                script.ult_seconds = 0
                root = next(i for i in script.component.instances.values() if i.graph.index == 0x025F)
                cost = script.component.entity_var(338).value()
                self.assertGreater(cost, 0)
                script.set_ult_charge(cost - 1)
                fixture.press(32, shooter=fixture.b, target=fixture.a)
                self.assertFalse(fight.rockets)
                self.assertEqual(script.component.entity_var(342).value(), cost - 1)
                fixture.press(0, 10)
                if case == "wall":
                    fixture.wall()
                if case == "allied":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.b.team = fixture.a.team
                yaw = 0 if case == "miss" else -32768
                script.set_ult_charge(cost)
                fixture.run.step({fixture.b: (yaw, 0, 32)})
                shot, = fight.rockets
                ability = root.state(2)
                self.assertTrue(ability.flags & ability.RUNNING)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(shot.speed, 25)
                self.assertAlmostEqual(shot.gravity, 9.8, places=5)
                self.assertEqual(shot.direct.amount, 5)
                for _ in range(8):
                    fixture.press(0)
                    if shot not in fight.rockets:
                        break
                self.assertEqual(fixture.health(fixture.a), 195 if case == "enemy" else 200)
                self.assertEqual(shot in fight.rockets, case in ("allied", "miss"))
                if case in ("allied", "miss"):
                    self.assertLess(shot.velocity[1], 0)
                fixture.press(0, 90)
                self.assertFalse(ability.flags & ability.RUNNING)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                if case != "enemy":
                    self.assertEqual(fixture.health(fixture.a), 200)
                    self.assertFalse(fight.gravitons.pulling(fixture.a))

    def test_actual_zarya_graviton_admission_is_blocked_by_hack_and_emp(self):
        for button in (2, 0x20):
            with self.subTest(button=button):
                fixture, _ = self.zarya_arena()
                fixture.press(0, 150)
                fight = fixture.run.match.combat
                script = fixture.b.body_script
                script.ult_seconds = 0
                cost = script.component.entity_var(338).value()
                script.set_ult_charge(cost)
                fixture.a.body_script.set_ult_charge(1400 if button == 0x20 else 0)
                fixture.press(button, 85 if button == 2 else 50)
                effect, = fight.hacks[fixture.b.body]
                fixture.press(32, 8, shooter=fixture.b, target=fixture.a)
                self.assertFalse(fight.rockets)
                self.assertEqual(script.component.entity_var(342).value(), cost)
                fixture.press(0, effect.until_tick - fixture.run.frame + 2)
                fixture.press(32, shooter=fixture.b, target=fixture.a)
                fixture.press(0, 12)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertLess(fixture.health(fixture.a), 195)

    def land_graviton(self, fixture, held=0, *, caster=None):
        fight = fixture.run.match.combat
        caster = fixture.b if caster is None else caster
        fixture.geometry((-5, 0, -0.5, 5, 0, -0.5, 5, 4, -0.5),
                         (-5, 0, -0.5, 5, 4, -0.5, -5, 4, -0.5))
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        yaw, pitch = aim(fight._eye(fixture.a), (0, 1, 1.5))
        fixture.run.step({fixture.a: (yaw, pitch, held), caster: (-32768, 0, 32)})
        for _ in range(12):
            fixture.press(held)
            if any(field.shooter is caster for field in fight.gravitons.fields):
                break
        field, = [field for field in fight.gravitons.fields if field.shooter is caster]
        return field

    def test_actual_graviton_reacquires_phase_targets_after_immunity_ends(self):
        for guid in phase_defenses.PHASES:
            with self.subTest(hero=guid):
                fixture, zarya = self.projected_arena(caster_z=1.5)
                match = fixture.run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(fixture.b, scenes.heroes()[guid]))
                fixture.b.team = fixture.a.team
                for player, x in ((fixture.a, 2), (fixture.b, 3)):
                    player.mover.position[:] = (x, 0, 0)
                    player.mover.body = None
                fixture.press(0, 50)
                fixture.press(8, shooter=fixture.b, target=fixture.a)
                self.assertTrue(phase_defenses.active(fixture.b))
                before = fixture.health(fixture.b)
                x = fixture.b.mover.position[0]
                field = self.land_graviton(fixture, caster=zarya)
                fixture.press(0, 15)
                self.assertTrue(phase_defenses.active(fixture.b))
                self.assertFalse(fight.gravitons.pulling(fixture.b))
                self.assertEqual(fixture.health(fixture.b), before)
                self.assertEqual(fixture.b.mover.position[0], x)
                self.assertTrue(fight.gravitons.pulling(fixture.a))
                self.assertLess(fixture.health(fixture.a), 200)
                while phase_defenses.active(fixture.b) and fixture.run.frame < field.end_tick:
                    fixture.press(0)
                self.assertIn(field, fight.gravitons.fields)
                self.assertFalse(phase_defenses.active(fixture.b))
                fixture.press(0, 8)
                self.assertTrue(fight.gravitons.pulling(fixture.b))
                self.assertLess(fixture.b.mover.position[0], x)
                self.assertGreater(before - fixture.health(fixture.b), 0)
                self.assertLess(before - fixture.health(fixture.b), 1)

    def test_player_death_clears_silence_and_graviton_on_the_lethal_frame(self):
        for button in (2, 32):
            with self.subTest(button=button):
                f, zarya = self.projected_arena(caster_z=1.5)
                match = f.run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(f.b, scenes.heroes()[scenes.SOMBRA]))
                for player, position in ((f.a, (2, 0, 0)), (f.b, (3, 0, 1.5))):
                    player.mover.position[:] = position
                    player.mover.body = None
                f.press(0, 50)
                if button == 32:
                    f.b.body_script.set_ult_charge(1400)
                f.press(button, 70, shooter=f.b, target=f.a)
                effect, = fight.hacks[f.a.body]
                field = self.land_graviton(f, caster=zarya)
                f.press(0, 4)
                self.assertTrue(fight.gravitons.pulling(f.a))
                self.assertIn(f.a.body, fight.hacks)
                vitals = fight.vitals[f.a.body]
                vitals.now[HEALTH_POOL] = 1 if button == 2 else 0.05
                if button == 2:
                    f.press(FIRE, shooter=f.b, target=f.a)
                    killer = f.b
                else:
                    f.press(0)
                    killer = zarya
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, killer.body)
                self.assertNotIn(f.a.body, fight.hacks)
                self.assertNotIn(f.a.body, fight.hack_bases)
                component = f.a.body_script.component
                self.assertNotIn(effect.application.id, component.instances)
                self.assertFalse(component.entity_var(888).value())
                self.assertFalse(fight.gravitons.pulling(f.a))
                self.assertFalse(f.a.mover.driven)
                self.assertIn(field, fight.gravitons.fields)
                notice = f.a.client.messages(combat.DEATH_NOTICE)[-1]
                self.assertEqual(notice["+0x7C"]["+0x0"], killer.body)

    def test_actual_zarya_graviton_field_pulls_without_input_and_expires(self):
        fixture, _ = self.zarya_arena()
        match = fixture.run.match
        fight = match.combat
        # Land on a wall to the side of Sombra, so she receives field damage only.
        fixture.a.mover.position[:] = (2, 0, 0)
        fixture.a.mover.body = None
        field = self.land_graviton(fixture)
        self.assertEqual(field.radius, 6)
        self.assertEqual(field.end_tick - field.last_tick, 250)
        self.assertEqual(fixture.health(fixture.a), 200)

        def digest():
            encoder = _Graph({"match": match, "owner": fixture.b}, None)
            root = encoder.value(fight.gravitons)
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": {"gravitons": root}, "nodes": encoder.finish(), "assets": {},
            })

        initial = digest()
        self.assertEqual(digest(), initial)
        x = fixture.a.mover.position[0]
        for _ in range(30):
            fixture.run.frame += 1
            match.update(fixture.run.frame * 0.016, fixture.run.frame)
        self.assertLess(fixture.a.mover.position[0], x - 1)
        self.assertAlmostEqual(fixture.health(fixture.a), 200 - 5 * 30 * 0.016, places=5)
        self.assertTrue(fight.gravitons.pulling(fixture.a))
        self.assertNotEqual(digest(), initial)
        x = fixture.a.mover.position[0]
        for _ in range(10):
            fixture.run.frame += 1
            match.update(fixture.run.frame * 0.016, fixture.run.frame)
            fixture.a.take_commands([Command(fixture.run.frame, yaw=16384, forward=127)])
        self.assertLess(fixture.a.mover.position[0], x)
        while fixture.run.frame < field.end_tick:
            fixture.run.frame += 1
            match.update(fixture.run.frame * 0.016, fixture.run.frame)
        self.assertFalse(fight.gravitons.fields)
        self.assertFalse(fight.gravitons.pulling(fixture.a))
        self.assertFalse(fixture.a.mover.driven)
        self.assertAlmostEqual(fixture.health(fixture.a), 180, places=5)

    def test_actual_graviton_field_survives_caster_death_and_replacement(self):
        for change in ("death", "replacement"):
            with self.subTest(change=change):
                fixture, _ = self.zarya_arena()
                fixture.press(0, 150)
                match = fixture.run.match
                fight = match.combat
                fixture.a.mover.position[:] = (2, 0, 0)
                fixture.a.mover.body = None
                field = self.land_graviton(fixture)
                original = fixture.b.body
                self.assertEqual(field.source_body, original)
                if change == "death":
                    self.assertEqual(fixture.health(fixture.b), 400)
                    attacker = fight.vitals[original]
                    attacker.now[SHIELDS_POOL] = 0
                    attacker.now[HEALTH_POOL] = 1
                    fixture.press(FIRE)
                    self.assertTrue(attacker.dead)
                else:
                    self.assertTrue(match.switch_hero(fixture.b, scenes.heroes()[scenes.SOMBRA]))
                    self.assertNotEqual(fixture.b.body, original)
                    fixture.b.body_script.ult_seconds = 0
                    fixture.b.body_script.set_ult_charge(0)
                victim = fight.vitals[fixture.a.body]
                victim.now[HEALTH_POOL] = 0.1
                fixture.press(0, 3)
                self.assertTrue(victim.dead)
                self.assertEqual(victim.killer, original)
                notice = fixture.a.client.messages(combat.DEATH_NOTICE)[-1]
                self.assertEqual(notice["+0x7C"]["+0x0"], original)
                self.assertIn(field, fight.gravitons.fields)
                self.assertFalse(fight.gravitons.pulling(fixture.a))
                if change == "replacement":
                    self.assertEqual(fixture.b.body_script.component.entity_var(342).value(), 0)

    def test_actual_graviton_retires_old_lease_and_keeps_flying_after_body_switch(self):
        fixture, _ = self.zarya_arena()
        match = fixture.run.match
        fight = match.combat
        fixture.a.mover.position[:] = (0, 0, -6)
        fixture.a.mover.body = None
        fixture.press(0, 3)
        script = fixture.b.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        fixture.run.step({fixture.b: (-32768, 0, 32)})
        rocket, = fight.rockets
        shot = rocket.shot
        self.assertIsNotNone(shot)
        self.assertIn(shot, fight.projectiles.shots)
        original = fixture.b.body
        for _ in range(12):
            if shot.created:
                break
            fixture.press(0)
        self.assertTrue(shot.created)
        self.assertIn(rocket, fight.rockets)
        self.assertTrue(match.switch_hero(fixture.b, scenes.heroes()[scenes.SOMBRA]))
        self.assertNotEqual(fixture.b.body, original)
        self.assertNotIn(shot, fight.projectiles.shots)
        self.assertIsNone(rocket.shot)
        for entity in shot.created:
            self.assertTrue(any(update.entity == entity and update.op == world.OP_DESTROY
                                for update in fixture.b.client.entities))
        self.assertIn(rocket, fight.rockets)
        self.assertEqual(fixture.health(fixture.a), 200)
        victim = fight.vitals[fixture.a.body]
        victim.now[HEALTH_POOL] = 1
        fixture.press(0, 30)
        self.assertTrue(victim.dead)
        self.assertEqual(victim.killer, original)
        field, = fight.gravitons.fields
        self.assertEqual(field.source_body, original)

    def test_actual_graviton_preserves_cast_team_after_caster_changes_team(self):
        fixture, _ = self.zarya_arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        script = fixture.b.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        fixture.press(32, shooter=fixture.b, target=fixture.a)
        shot, = fight.rockets
        self.assertEqual(shot.team, 1)
        fixture.b.team = 0
        fixture.press(0, 20)
        field, = fight.gravitons.fields
        self.assertEqual(field.team, 1)
        self.assertLess(fixture.health(fixture.a), 195)
        self.assertTrue(fight.gravitons.pulling(fixture.a))

    def test_actual_graviton_caster_departure_cancels_flight_or_releases_pull(self):
        for landed in (False, True):
            with self.subTest(landed=landed):
                fixture, _ = self.zarya_arena()
                match = fixture.run.match
                fight = match.combat
                if landed:
                    fixture.a.mover.position[:] = (2, 0, 0)
                    fixture.a.mover.body = None
                    self.land_graviton(fixture)
                    fixture.press(0, 8)
                    self.assertTrue(fight.gravitons.pulling(fixture.a))
                else:
                    script = fixture.b.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    fixture.press(32, shooter=fixture.b, target=fixture.a)
                    self.assertTrue(fight.rockets)
                before = fixture.health(fixture.a)
                match.leave(fixture.b)
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.gravitons.fields)
                self.assertFalse(fight.gravitons.pulling(fixture.a))
                self.assertFalse(fixture.a.mover.driven)
                fixture.run.players.remove(fixture.b)
                fixture.press(0, 20)
                self.assertEqual(fixture.health(fixture.a), before)

    def test_actual_graviton_blocks_translocator_recall_until_release(self):
        fixture, _ = self.zarya_arena()
        fight = fixture.run.match.combat
        fixture.press(16, 20)
        fixture.press(0, 300)
        actor, = fight.translocators.entries.values()
        self.assertTrue(actor.settled)
        fixture.a.mover.position[:] = (2, 0, 0)
        fixture.a.mover.body = None
        field = self.land_graviton(fixture)
        self.assertTrue(fight.gravitons.pulling(fixture.a))
        fixture.press(16, 30)
        self.assertEqual(fight.vitals[actor.entity].total, 5)
        self.assertGreater(abs(fixture.a.mover.position[2] - actor.position[2]), 5)
        fixture.press(0, field.end_tick - fixture.run.frame + 2)
        self.assertFalse(fight.gravitons.pulling(fixture.a))
        fixture.press(16)
        fixture.press(0, 60)
        self.assertNotIn(actor.entity, fight.translocators.entries)
        self.assertAlmostEqual(fixture.a.mover.position[2], actor.position[2], places=2)

    def test_overlapping_actual_gravitons_keep_recall_locked_and_release_on_death(self):
        for release in ("expiry", "departure", "death"):
            with self.subTest(release=release):
                fixture, second_caster = self.projected_arena(caster_z=1.5)
                match = fixture.run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(fixture.b, scenes.heroes()[reinhardt_barrier.ZARYA]))
                fixture.b.mover.position[:] = (0, 0, 1.5)
                second_caster.mover.position[:] = (2, 0, 1.5)
                for caster in (fixture.b, second_caster):
                    caster.mover.body = None
                fixture.press(0, 50)
                fixture.press(16, 20)
                fixture.press(0, 300)
                actor, = fight.translocators.entries.values()
                self.assertTrue(actor.settled)
                fixture.a.mover.position[:] = (1, 0, 0)
                fixture.a.mover.body = None
                first = self.land_graviton(fixture)
                fixture.press(0, 50)
                second = self.land_graviton(fixture, caster=second_caster)
                before = fixture.health(fixture.a)
                fixture.press(0)
                self.assertAlmostEqual(before - fixture.health(fixture.a), 0.16, places=5)
                self.assertTrue(fight.gravitons.pulling(fixture.a))
                if release == "death":
                    victim = fight.vitals[fixture.a.body]
                    victim.now[HEALTH_POOL] = 0.1
                    fixture.press(0)
                    self.assertTrue(victim.dead)
                    self.assertEqual(victim.killer, second.source_body)
                    self.assertFalse(fight.gravitons.pulling(fixture.a))
                    self.assertFalse(fixture.a.mover.driven)
                    continue
                if release == "departure":
                    match.leave(fixture.b)
                    fixture.run.players.remove(fixture.b)
                else:
                    fixture.press(0, first.end_tick - fixture.run.frame)
                self.assertEqual(fight.gravitons.fields, [second])
                self.assertTrue(fight.gravitons.pulling(fixture.a))
                # After departure the standard fixture input still uses the old
                # caster as its aim reference, but sends commands only to live players.
                x = fixture.a.mover.position[0]
                fixture.press(16, 12)
                self.assertGreater(fixture.a.mover.position[0], x)
                self.assertEqual(fight.vitals[actor.entity].total, 5)
                fixture.press(0, second.end_tick - fixture.run.frame + 2)
                self.assertFalse(fight.gravitons.pulling(fixture.a))
                fixture.press(16)
                fixture.press(0, 60)
                self.assertNotIn(actor.entity, fight.translocators.entries)
                self.assertAlmostEqual(fixture.a.mover.position[2], actor.position[2], places=2)

    def test_actual_graviton_damage_interrupts_hack_and_camo_but_not_emp(self):
        for mode in ("hack", "camo", "emp"):
            with self.subTest(mode=mode):
                fixture, _ = self.zarya_arena()
                fixture.press(0, 150)
                fight = fixture.run.match.combat
                fixture.a.mover.position[:] = (2, 0, 0)
                fixture.a.mover.body = None
                fixture.press(0, 3)
                if mode == "hack":
                    fixture.press(2, 12)
                    ability = fixture.weapon().state(53)
                elif mode == "camo":
                    fixture.press(8)
                    fixture.press(0, 60)
                    ability = fixture.camo().state(13)
                else:
                    fixture.a.body_script.set_ult_charge(1400)
                    fixture.press(32)
                    root = next(i for i in fixture.a.body_script.component.instances.values()
                                if i.graph.index == 0x0B31)
                    ability = root.state(1)
                self.assertTrue(ability.flags & ability.RUNNING)
                self.land_graviton(fixture, held=2 if mode == "hack" else 0)
                self.assertEqual(fixture.health(fixture.a), 200)
                self.assertTrue(ability.flags & ability.RUNNING)
                fixture.press(2 if mode == "hack" else 0, 70)
                self.assertLess(fixture.health(fixture.a), 200)
                self.assertFalse(ability.flags & ability.RUNNING)
                self.assertEqual(fixture.b.body in fight.hacks, mode == "emp")
                if mode != "emp":
                    self.assertGreater(ability.remaining(), 0)

    def arena(self, distance=4, *, rear=False, wall=False, allied=False, raised=True):
        fixture = scenes.SombraCombatTests()
        fixture.setUp()
        match = fixture.run.match
        self.assertTrue(match.switch_hero(fixture.b, scenes.heroes()[reinhardt_barrier.HERO]))
        fixture.b.mover.position[:] = (0, 0, distance)
        fixture.b.mover.body = None
        if allied:
            match.game_map = replace(match.game_map, free_for_all=False)
            fixture.b.team = fixture.a.team
        fixture.press(0, 90)
        facing = 0 if rear else -32768
        barrier = None
        if raised:
            for _ in range(40):
                fixture.run.step({fixture.b: (facing, 0, 2)})
            barrier, = match.combat.barriers.values()
            self.assertTrue(barrier.mesh)
            self.assertEqual(barrier.vitals.total, 1600)
            self.assertEqual({i.graph.index for i in barrier.component.instances.values()}, {0x25B4, 0x96})
        if wall:
            fixture.wall()
        fixture.a.body_script.ult_seconds = 0
        fixture.a.body_script.set_ult_charge(0)
        return fixture, barrier, facing

    @staticmethod
    def press(fixture, facing, buttons, frames=1):
        fight = fixture.run.match.combat
        yaw, pitch = aim(
            fight._eye(fixture.a), (0, 1.5, fixture.b.mover.position[2]),
        )
        for _ in range(frames):
            fixture.run.step({fixture.a: (yaw, pitch, buttons), fixture.b: (facing, 0, 2)})

    def test_actual_pistol_drains_front_barrier_without_character_charge(self):
        fixture, barrier, facing = self.arena()
        self.press(fixture, facing, FIRE)
        self.assertEqual(barrier.vitals.total, 1592)
        self.assertEqual(fixture.health(fixture.b), 500)
        self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), 0)

    def test_actual_rear_shot_hits_reinhardt_and_preserves_barrier(self):
        fixture, barrier, facing = self.arena(rear=True)
        self.press(fixture, facing, FIRE)
        self.assertEqual(barrier.vitals.total, 1600)
        self.assertEqual(fixture.health(fixture.b), 489)  # 16 critical damage, reduced by 5 armour

    def test_front_barrier_blocks_hack_until_released(self):
        fixture, barrier, facing = self.arena()
        self.press(fixture, facing, 2, 82)
        self.assertFalse(fixture.hack_applications())
        self.assertFalse(fixture.weapon().vars[375].value())
        self.assertEqual(fixture.weapon().state(53).remaining(), 0)
        self.assertEqual(barrier.vitals.total, 1600)
        fixture.press(0, 30)
        fixture.press(2, 82)
        self.assertTrue(fixture.hack_applications())

    def test_rear_hack_silences_owner_and_lowers_barrier(self):
        fixture, barrier, facing = self.arena(rear=True)
        self.press(fixture, facing, 2, 82)
        self.assertTrue(fixture.hack_applications())
        self.assertFalse(barrier.entity in fixture.run.match.combat.barriers)
        self.assertEqual(barrier.vitals.total, 1600)
        self.press(fixture, facing, 0, 120)
        self.assertFalse(fixture.run.match.combat.barriers)
        fixture.press(0, 220)
        self.press(fixture, facing, 0, 40)
        raised, = fixture.run.match.combat.barriers.values()
        self.assertNotEqual(raised.entity, barrier.entity)
        self.assertEqual(raised.vitals.total, 1600)

    def test_barrier_raised_during_channel_interrupts_hack(self):
        fixture, _, facing = self.arena(raised=False)
        fixture.press(2, 12)
        self.assertTrue(fixture.weapon().vars[8909].value())
        self.press(fixture, facing, 2, 82)
        self.assertTrue(fixture.run.match.combat.barriers)
        self.assertFalse(fixture.hack_applications())
        self.assertFalse(fixture.weapon().vars[8909].value())

    def test_allied_barrier_does_not_screen_enemy_hack_target(self):
        fixture, barrier, facing = self.arena(allied=True)
        match = fixture.run.match
        enemy = match.add_player(3, "Target", SOLDIER, 1 - fixture.a.team, False)
        in_world(match, enemy)
        self.assertTrue(match.switch_hero(enemy, scenes.heroes()[SOLDIER]))
        enemy.mover.position[:] = (0, 0, 7)
        enemy.mover.body = None
        angles = aim(match.combat._eye(fixture.a), (0, 1, 7))
        for _ in range(82):
            fixture.run.step({fixture.a: (*angles, 2), fixture.b: (facing, 0, 2)})
        self.assertIn(enemy.body, match.combat.hacks)
        self.assertNotIn(fixture.b.body, match.combat.hacks)
        self.assertEqual(barrier.vitals.total, 1600)

    def test_actual_emp_breaks_tagged_barrier_and_silences_owner(self):
        fixture, barrier, facing = self.arena()
        fixture.a.body_script.set_ult_charge(1400)
        self.press(fixture, facing, 32, 50)
        self.assertEqual(barrier.vitals.total, 0)
        self.assertNotIn(barrier.entity, fixture.run.match.combat.barriers)
        self.assertTrue(fixture.hack_applications())
        self.assertEqual(fixture.health(fixture.b), 500)
        self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), 0)

    def test_actual_emp_excludes_covered_distant_and_allied_barriers(self):
        for options in ({"wall": True}, {"distance": 25}, {"allied": True}):
            with self.subTest(options=options):
                fixture, barrier, facing = self.arena(**options)
                fixture.a.body_script.set_ult_charge(1400)
                self.press(fixture, facing, 32, 50)
                self.assertEqual(barrier.vitals.total, 1600)
                self.assertIn(barrier.entity, fixture.run.match.combat.barriers)
                self.assertFalse(fixture.hack_applications())

    def test_release_and_raise_replaces_child_and_preserves_pistol_damage(self):
        fixture, barrier, facing = self.arena()
        self.press(fixture, facing, FIRE)
        fixture.press(0, 30)
        fight = fixture.run.match.combat
        self.assertFalse(fight.barriers)
        self.assertFalse(barrier.component.instances)
        self.press(fixture, facing, 0, 40)
        raised, = fight.barriers.values()
        self.assertNotEqual(raised.entity, barrier.entity)
        self.assertEqual(raised.vitals.total, 1592)
        fixture.press(0, 210)
        self.press(fixture, facing, 0, 40)
        recovered, = fight.barriers.values()
        self.assertEqual(recovered.vitals.total, 1600)

    def test_emp_break_lock_expires_and_owner_can_raise_again(self):
        fixture, barrier, facing = self.arena()
        fixture.a.body_script.set_ult_charge(1400)
        self.press(fixture, facing, 32, 50)
        owner_graph = barrier.pet.instance
        self.assertEqual(owner_graph.vars[586].value(), 0)
        self.assertGreater(owner_graph.vars[61].value(), 0)
        self.press(fixture, facing, 0, 120)
        fight = fixture.run.match.combat
        self.assertFalse(fight.barriers)
        self.press(fixture, facing, 0, 200)
        self.assertEqual(owner_graph.vars[61].value(), 0)
        fixture.press(0)
        self.press(fixture, facing, 0, 40)
        recovered, = fight.barriers.values()
        self.assertNotEqual(recovered.entity, barrier.entity)
        self.assertGreater(recovered.vitals.total, 0)

    def test_body_replacement_retires_the_active_barrier(self):
        fixture, barrier, _ = self.arena()
        match = fixture.run.match
        self.assertTrue(match.switch_hero(fixture.b, fixture.b.hero))
        self.assertFalse(match.combat.barriers)
        self.assertFalse(barrier.component.instances)

    def test_actual_fatal_rear_fire_retires_the_active_barrier(self):
        fixture, barrier, facing = self.arena(rear=True)
        fight = fixture.run.match.combat
        for _ in range(400):
            self.press(fixture, facing, FIRE)
            if fight.is_dead(fixture.b):
                break
        self.assertTrue(fight.is_dead(fixture.b))
        self.press(fixture, facing, 0)
        self.assertFalse(fight.barriers)
        self.assertFalse(barrier.component.instances)
        self.assertEqual(barrier.vitals.total, 1600)

    def test_actual_melee_reaches_body_through_barrier(self):
        fixture, barrier, facing = self.arena(distance=1.5)
        self.press(fixture, facing, scenes.MELEE, 30)
        self.assertEqual(barrier.vitals.total, 1600)
        self.assertEqual(fixture.health(fixture.b), 475)

    def test_live_child_and_source_pet_are_stored_with_barrier_balance(self):
        fixture, barrier, facing = self.arena()

        def digest():
            encoder = _Graph({"match": fixture.run.match, "owner": fixture.b}, None)
            root = encoder.value(barrier)
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": {"barrier": root}, "nodes": encoder.finish(), "assets": {},
            })

        initial = digest()
        self.assertEqual(digest(), initial)
        self.press(fixture, facing, FIRE)
        self.assertEqual(barrier.vitals.total, 1592)
        after_hit = digest()
        barrier.vitals.damage(1)
        self.assertNotEqual(digest(), after_hit)
