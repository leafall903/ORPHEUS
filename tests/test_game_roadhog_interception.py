"""Real Deflect/Grasp casts; geometry/redirect policy remains reconstructed."""

import os
import unittest
from dataclasses import replace
from itertools import product
from unittest.mock import patch

import test_game_roadhog_defenses as phases
import test_game_roadhog_winston as domes
from test_game_combat import aim

from ow174.game import combat, defenses, stats
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes


class RoadhogInterceptionTests(unittest.TestCase):
    session = phases.RoadhogDefenseTests.session
    health = phases.RoadhogDefenseTests.health

    def step(self, run, hog, defender, *, fire=0, cast=0, facing=-32768):
        x, y, z = defender.mover.position
        angles = aim(run.match.combat._eye(hog), (x, y + 1, z))
        run.step({hog: (*angles, fire), defender: (facing, 0, cast)})

    def encounter(self, name, *, distance=3, facing=-32768):
        run, hog, defender, behind = self.session(name, distance=distance)
        run.match.combat.vitals[hog.body] = combat.Vitals(hog.body, 20000)
        self.step(run, hog, defender, cast=16 if name == "Genji" else 8, facing=facing)
        for _ in range(5):
            self.step(run, hog, defender, facing=facing)
        target = next(t for t in run.match.combat._targets() if t.owner is defender)
        low, high = target.segment()
        origin = run.match.combat._eye(hog)
        front = combat.ray_capsule(origin, (0, 0, 1), low, high, target.radius)
        gate = defenses.projectile_gate(origin, (0, 0, 1), front,
                                        run.match.combat._enemies(hog))
        self.assertEqual(gate is not None, facing == -32768)
        return run, hog, defender, behind

    def assert_grasp_conversion(self, run, hog, sigma, *, capped=False):
        fight = run.match.combat
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        absorbed = grasp.vars[21312].value()
        self.assertGreater(absorbed, 400 / 0.6 if capped else 0)
        # Whole Hog can still be active after Grasp expires. Aim away from the
        # target so later volleys cannot spend the newly granted shield.
        for _ in range(125):
            run.step({hog: (-32768, 0, 0), sigma: (-32768, 0, 0)})
        vitals = fight.vitals[sigma.body]
        shield = vitals.temporary_shields[defenses.SHIELD_TAG]
        self.assertAlmostEqual(shield.now, min(400, absorbed * 0.6), delta=0.0001)
        self.assertEqual(sum(vitals.now.values()), 20000)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_deflect_and_grasp_activate_after_secondary_launch_or_fragmentation(self):
        for hero, fragmented in product(("Genji", "Sigma"), (False, True)):
            with self.subTest(hero=hero, fragmented=fragmented):
                self.check_late_secondary_interception(hero, fragmented)

    def check_late_secondary_interception(self, hero, fragmented):
        run, hog, defender, rear = self.session(hero, distance=16 if fragmented else 8)
        fight = run.match.combat
        fight.vitals[hog.body] = combat.Vitals(hog.body, 20000)
        rear.mover.position[0] += 30
        rear.mover.body = None
        for _ in range(35):
            self.step(run, hog, defender)
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        self.step(run, hog, defender, fire=2)
        slug, = fight.rockets
        self.assertIsNotNone(slug.burst)
        self.assertEqual(weapon.vars[53].value(), 4)
        if fragmented:
            for _ in range(12):
                self.step(run, hog, defender)
                if slug not in fight.rockets:
                    break
            self.assertEqual(len(fight.rockets), 25)
            self.assertTrue(all(rocket.burst is None for rocket in fight.rockets))
        self.assertEqual(self.health(run, defender), 20000)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.step(run, hog, defender, cast=16 if hero == "Genji" else 8)
        reflected = False
        for _ in range(90):
            self.step(run, hog, defender)
            reflected |= any(rocket.reflected and rocket.shooter is defender for rocket in fight.rockets)
        self.assertEqual(self.health(run, defender), 20000)
        self.assertEqual(self.health(run, rear), 20000)
        self.assertEqual(weapon.vars[53].value(), 4)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.assertFalse(fight.rockets)
        if hero == "Genji":
            self.assertTrue(reflected)
            self.assertLess(self.health(run, hog), 20000)
            self.assertTrue(defender.client.messages(combat.HIT))
        else:
            self.assertEqual(self.health(run, hog), 20000)
            grasp = next(i for i in defender.body_script.component.instances.values()
                         if i.graph.index == 0x1D02)
            if fragmented:
                self.assertGreater(grasp.vars[21312].value(), 0)
            else:
                self.assertEqual(grasp.vars[21312].value(), 50)

    def test_primary_slug_and_fragments_reflect_or_absorb_without_extra_ammo(self):
        for name in ("Genji", "Sigma"):
            for button, distance in ((1, 3), (2, 3), (2, 10), (2, 14)):
                with self.subTest(hero=name, button=button, distance=distance):
                    run, hog, defender, behind = self.encounter(name, distance=distance)
                    weapon = next(i for i in hog.body_script.component.instances.values()
                                  if i.graph.index == 0x01C6)
                    ammo = weapon.vars[53].value()
                    self.step(run, hog, defender, fire=button)
                    for _ in range(65):
                        self.step(run, hog, defender)
                    self.assertEqual(self.health(run, defender), 20000)
                    self.assertEqual(self.health(run, behind), 20000)
                    self.assertEqual(weapon.vars[53].value(), ammo - 1)
                    self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
                    self.assertFalse(hog.client.messages(combat.HIT))
                    if name == "Genji":
                        self.assertLess(self.health(run, hog), 20000)
                        hits = defender.client.messages(combat.HIT)
                        self.assertGreater(len(hits), 1 if distance >= 10 else 0)
                    else:
                        self.assertEqual(self.health(run, hog), 20000)
                        self.assertFalse(defender.client.messages(combat.HIT))
                    self.assertFalse(run.match.combat.rockets)
                    if name == "Sigma":
                        self.assert_grasp_conversion(run, hog, defender)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_reflected_scrap_and_whole_hog_hit_each_npc_with_genji_credit(self):
        self.check_interception_against_npcs("Genji")

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_grasp_protects_each_npc_from_scrap_and_whole_hog(self):
        self.check_interception_against_npcs("Sigma")

    def check_interception_against_npcs(self, name):
        import test_game_roadhog_npcs as actual

        case = actual.RoadhogFiveNpcTests()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        for definition in case.definitions.values():
            for attack, button, distance in (("primary", 1, 4), ("slug", 2, 4),
                                             ("fragments", 2, 14), ("whole hog", 32, 4)):
                with self.subTest(npc=definition.name, attack=attack):
                    self.check_interception_against_npc(case, definition, name, attack, button, distance)

    def check_interception_against_npc(self, case, definition, name, attack, button, distance):
        from test_game_roadhog_npcs import NpcClient

        reflect = name == "Genji"
        npc_distance = -3 if reflect else distance + 2
        run, hog, bot, pool, _ = case.session(definition, distance=npc_distance, caster=True)
        defender = run.players[1]
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(defender, next(h for h in heroes().values() if h.name == name)))
        defender.client = NpcClient(defender)
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, defender.team = (bot.team, 0) if reflect else (0, bot.team)
        x, y, z = hog.mover.position
        defender.mover.position[:] = (x, y, z + distance)
        defender.mover.body = None
        if reflect:
            hog.mover.position[0] += 2
            hog.mover.body = None
        for player in (hog, defender):
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
        for _ in range(12):
            run.step({})
        look = aim(fight._eye(hog), (x, y + 1, z + distance))
        npc = next(t for t in fight._targets() if t.owner is bot)
        low, high = npc.segment()
        point = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        facing = aim(fight._eye(defender), point) if reflect else (-32768, 0)
        run.step({defender: (*facing, 16 if reflect else 8)})
        defense = {defender: (*facing, 0)}
        for _ in range(6):
            run.step(defense)
        before, defender_hp = pool.total, self.health(run, defender)
        component = hog.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        if button == 32:
            hog.body_script.set_ult_charge(component.entity_var(338).value())
        run.step({**defense, hog: (*look, button)})
        fragments_released = False
        for _ in range(65):
            fragments_released |= len(fight.rockets) > 1
            run.step({**defense, hog: (*look, 0)})
            if reflect and pool.total < before:
                break
        self.assertEqual(weapon.vars[53].value(), ammo if button == 32 else ammo - 1)
        self.assertEqual(component.entity_var(342).value(), 0)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.assertEqual(self.health(run, defender), defender_hp)
        if attack == "fragments":
            self.assertTrue(fragments_released)
        if reflect:
            loss = before - pool.total
            self.assertGreater(loss, 0)
            hits = defender.client.messages(combat.HIT)
            self.assertTrue(hits)
            self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == bot.entity for hit in hits))
            self.assertAlmostEqual(defender.stats.value(stats.HERO_DAMAGE), loss, places=3)
            self.assertEqual(defender.body_script.component.entity_var(342).value(), 0)
            if button == 32:
                self.assertLess(fight.impulses.velocity(bot.entity)[2], 0)
        else:
            self.assertEqual(pool.total, before)
            self.assertNotIn(bot.entity, fight.impulses.live)
            grasp = next(i for i in defender.body_script.component.instances.values() if i.graph.index == 0x1D02)
            absorbed = grasp.vars[21312].value()
            self.assertGreater(absorbed, 0)
            for _ in range(115):
                run.step({**defense, hog: (-32768, 0, 0)})
            shield = fight.vitals[defender.body].temporary_shields[defenses.SHIELD_TAG]
            self.assertAlmostEqual(shield.now, min(400, absorbed * .6), places=3)
            self.assertEqual(pool.total, before)

    def test_running_whole_hog_hitscan_is_redirected_or_consumed(self):
        for name in ("Genji", "Sigma"):
            with self.subTest(hero=name):
                run, hog, defender, behind = self.encounter(name)
                hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
                self.step(run, hog, defender, fire=32)
                for _ in range(90):
                    self.step(run, hog, defender)
                self.assertEqual(self.health(run, defender), 20000)
                self.assertEqual(self.health(run, behind), 20000)
                self.assertEqual(self.health(run, hog) < 20000, name == "Genji")
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
                if name == "Sigma":
                    self.assert_grasp_conversion(run, hog, defender, capped=True)

    def test_whole_hog_force_resumes_after_grasp_ends_during_the_channel(self):
        run, hog, sigma, behind = self.encounter("Sigma")
        fight = run.match.combat
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        vitals = fight.vitals[sigma.body]
        base = dict(vitals.now)
        start = tuple(sigma.mover.position)
        self.step(run, hog, sigma, fire=32)
        for _ in range(90):
            self.step(run, hog, sigma)
            self.assertNotIn(sigma.body, fight.impulses.live)
            self.assertNotIn(behind.body, fight.impulses.live)
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(tuple(sigma.mover.position), start)
        self.assertEqual(vitals.now, base)
        self.assertFalse(hog.client.messages(combat.HIT))

        for _ in range(85):
            self.step(run, hog, sigma)
            if sigma.body in fight.impulses.live:
                break
        self.assertFalse(grasp.states[1].active)
        self.assertIn(sigma.body, fight.impulses.live)
        bonus = vitals.temporary_shields[defenses.SHIELD_TAG]
        self.assertGreater(bonus.now, 0)
        self.assertLess(bonus.now, 400)
        self.assertEqual(vitals.now, base)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertTrue(hog.client.messages(combat.HIT))
        self.step(run, hog, sigma)
        self.assertGreater(sigma.mover.position[2], start[2])

    def test_whole_hog_command_gap_across_grasp_expiry_and_channel_end_does_not_replay(self):
        run, hog, sigma, behind = self.encounter("Sigma")
        fight = run.match.combat
        script = hog.body_script
        ultimate, = script.ultimates
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        vitals = fight.vitals[sigma.body]
        base = dict(vitals.now)
        self.step(run, hog, sigma, fire=32)
        for _ in range(90):
            self.step(run, hog, sigma)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertTrue(grasp.states[1].active)
        self.assertGreater(grasp.vars[21312].value(), 400 / .6)
        self.assertFalse(hog.client.messages(combat.HIT))
        last_command = script.frame

        # Keep Sigma's commands live while Roadhog sends none.
        for _ in range(95):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            sigma.take_commands([Command(run.frame)])
        self.assertEqual(script.frame, last_command)
        self.assertFalse(grasp.states[1].active)
        self.assertEqual(vitals.temporary_shields[defenses.SHIELD_TAG].now, 400)
        self.assertEqual(vitals.now, base)
        self.assertNotIn(sigma.body, fight.impulses.live)

        run.frame += 455
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(script.frame, last_command)
        self.step(run, hog, sigma, fire=32)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(vitals.now, base)
        self.assertEqual(self.health(run, behind), 20000)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.assertNotIn(sigma.body, fight.impulses.live)
        self.assertNotIn(behind.body, fight.impulses.live)

    def test_actual_whole_hog_reflection_chain_is_bounded_between_two_genjis(self):
        run, hog, front, rear = self.session("Genji", distance=4)
        match = run.match
        fight = match.combat
        match.switch_hero(rear, front.hero)
        y = hog.mover.position[1]
        hog.mover.position[:] = (2, y, 0)
        front.mover.position[:] = (0, y, 4)
        rear.mover.position[:] = (0, y, -4)
        for actor in (hog, front, rear):
            actor.mover.body = None
            fight.vitals[actor.body] = combat.Vitals(actor.body, 20000)
        match.collision_world = flat_world(y)
        run.step({front: (-32768, 0, 16), rear: (0, 0, 16)})
        for _ in range(5):
            run.step({front: (-32768, 0, 0), rear: (0, 0, 0)})
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        angles = aim(fight._eye(hog), (0, fight._eye(front)[1], 4))
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
            run.step({hog: (*angles, 32), front: (-32768, 0, 0), rear: (0, 0, 0)})
            for _ in range(75):
                run.step({hog: (*angles, 0), front: (-32768, 0, 0), rear: (0, 0, 0)})
        levels = [call.kwargs.get("reflections", 0) for call in hitscan.call_args_list]
        self.assertEqual(max(levels), combat.MAX_DEFLECTIONS)
        end = levels.index(combat.MAX_DEFLECTIONS)
        start = end - combat.MAX_DEFLECTIONS
        self.assertEqual(levels[start:end + 1], list(range(combat.MAX_DEFLECTIONS + 1)))
        actors = [call.args[0] for call in hitscan.call_args_list[start:end + 1]]
        self.assertEqual(actors, [hog, *([front, rear] * (combat.MAX_DEFLECTIONS // 2))])
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for actor in (hog, front, rear):
            self.assertEqual(self.health(run, actor), 20000)
            self.assertFalse(actor.client.messages(combat.HIT))

    def test_grasp_converts_reflected_attacks_using_genjis_hostility(self):
        for button, allied_to_genji in product((2, 32), (False, True)):
            with self.subTest(button=button, allied_to_genji=allied_to_genji):
                run, hog, genji, sigma = self.session("Genji", distance=4)
                match = run.match
                fight = match.combat
                match.switch_hero(sigma, heroes()[0x02E000000000023B])
                match.game_map = replace(match.game_map, free_for_all=False)
                hog.team, genji.team = 0, 1
                sigma.team = genji.team if allied_to_genji else hog.team
                y = hog.mover.position[1]
                match.collision_world = flat_world(y)
                for actor, position in ((hog, (2, y, 0)), (genji, (0, y, 4)), (sigma, (0, y, -4))):
                    actor.mover.position[:] = position
                    actor.mover.body = None
                    fight.vitals[actor.body] = combat.Vitals(actor.body, 20000)

                run.step({genji: (-32768, 0, 16), sigma: (0, 0, 8)})
                commands = {genji: (-32768, 0, 0), sigma: (0, 0, 0)}
                for _ in range(6):
                    run.step(commands)
                grasp = next(i for i in sigma.body_script.component.instances.values()
                             if i.graph.index == 0x1D02)
                self.assertTrue(grasp.states[1].active)
                angles = aim(fight._eye(hog), fight._eye(genji))
                if button == 32:
                    script = hog.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
                        run.step({hog: (*angles, button), **commands})
                        for _ in range(45):
                            run.step({hog: (*angles, 0), **commands})
                    self.assertTrue(any(call.args[0] is genji and call.kwargs.get("reflections") == 1
                                        for call in hitscan.call_args_list))
                else:
                    run.step({hog: (*angles, button), **commands})
                    reflected = False
                    for _ in range(45):
                        run.step(commands)
                        reflected |= any(rocket.shooter is genji for rocket in fight.rockets)
                    self.assertTrue(reflected)
                absorbed = grasp.vars[21312].value()
                if allied_to_genji:
                    self.assertEqual(absorbed, 0)
                elif button == 2:
                    self.assertEqual(absorbed, 50)
                else:
                    self.assertGreater(absorbed, 0)
                for actor in (hog, genji, sigma):
                    self.assertEqual(self.health(run, actor), 20000)
                    self.assertFalse(actor.client.messages(combat.HIT))
                for _ in range(120):
                    run.step({hog: (16384, 0, 0), **commands})
                pools = fight.vitals[sigma.body].temporary_shields
                if allied_to_genji:
                    self.assertFalse(pools)
                else:
                    expected = min(400, absorbed * 0.6)
                    self.assertAlmostEqual(pools[defenses.SHIELD_TAG].now, expected, delta=0.0001)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)

    def test_dome_and_live_deflect_or_grasp_resolve_nearest_for_projectile_and_ray(self):
        for name in ("Genji", "Sigma"):
            for gate_first in (False, True):
                for button in (2, 32):
                    with self.subTest(hero=name, gate_first=gate_first, button=button):
                        self.check_dome_and_gate(name, gate_first, button)

    def check_dome_and_gate(self, name, gate_first, button):
        run, hog, defender, winston = self.session(name, distance=8)
        match = run.match
        fight = match.combat
        match.switch_hero(winston, next(hero for hero in heroes().values() if hero.name == "Winston"))
        y = hog.mover.position[1]
        winston.mover.position[:] = (0, y, 8)
        winston.mover.body = None
        dome = domes.RoadhogWinstonTests().cast(run, hog, winston)
        winston.mover.position[0] = 20
        winston.mover.body = None
        defender.mover.position[2] = 3 if gate_first else 8
        defender.mover.body = None
        fight.vitals[hog.body] = combat.Vitals(hog.body, 20000)
        self.step(run, hog, defender, cast=16 if name == "Genji" else 8)
        for _ in range(6):
            self.step(run, hog, defender)
        origin = fight._eye(hog)
        direction = (0, 0, 1)
        barrier = fight._barrier_contact(hog, origin, direction, 20)
        gate = defenses.projectile_gate(origin, direction, 20, fight._enemies(hog))
        self.assertIs(barrier[1], dome)
        self.assertIs(gate[1].owner, defender)
        self.assertEqual(gate[0] < barrier[0], gate_first)
        if button == 32:
            hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
        pellet = fight._shoot_pellet
        impacts = set()

        def observe(*args, **kwargs):
            body_before, shield_before = self.health(run, hog), dome.health
            pellet(*args, **kwargs)
            impacts.add((self.health(run, hog) < body_before, dome.health < shield_before))

        with patch.object(fight, "_shoot_pellet", side_effect=observe):
            self.step(run, hog, defender, fire=button)
            for _ in range(65):
                self.step(run, hog, defender)
        self.assertEqual(self.health(run, defender), 20000)
        self.assertEqual(self.health(run, hog) < 20000, gate_first and name == "Genji")
        if gate_first and name == "Genji" and button == 32:
            # Some rays miss the Deflect box but still reach the dome.
            self.assertIn((True, False), impacts)
            self.assertIn((False, True), impacts)
            self.assertNotIn((True, True), impacts)
        else:
            self.assertEqual(dome.health == 700, gate_first)
        self.assertGreater(dome.health, 0)
        self.assertFalse(dome.retired)

    def test_facing_and_expiry_restore_ordinary_projectile_damage(self):
        for name in ("Genji", "Sigma"):
            for expired in (False, True):
                with self.subTest(hero=name, expired=expired):
                    facing = -32768 if expired else 0
                    run, hog, defender, _ = self.encounter(name, facing=facing)
                    if expired:
                        for _ in range(200):
                            self.step(run, hog, defender)
                    self.step(run, hog, defender, fire=1, facing=facing)
                    for _ in range(25):
                        self.step(run, hog, defender, facing=facing)
                    self.assertLess(self.health(run, defender), 20000)
                    self.assertEqual(self.health(run, hog), 20000)
                    self.assertGreater(hog.body_script.component.entity_var(342).value(), 0)

    def test_hook_bypasses_both_while_only_front_deflect_parries_melee(self):
        for name in ("Genji", "Sigma"):
            for attack in (8, 0x800):
                with self.subTest(hero=name, attack=attack):
                    run, hog, defender, _ = self.encounter(name, distance=1.5)
                    self.step(run, hog, defender, fire=attack)
                    controlled = False
                    for _ in range(30):
                        self.step(run, hog, defender)
                        controlled |= run.match.combat.hooks.controlled(defender.body)
                    parried = name == "Genji" and attack == 0x800
                    self.assertEqual(self.health(run, defender), 20000 if parried else 19970)
                    self.assertEqual(self.health(run, hog), 20000)
                    self.assertEqual(controlled, attack == 8)

    def test_hook_interrupts_grasp_after_absorption_without_delayed_grant(self):
        run, hog, sigma, _ = self.encounter("Sigma", distance=3)
        fight = run.match.combat
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.step(run, hog, sigma, fire=2)
        for _ in range(45):
            self.step(run, hog, sigma)
        self.assertEqual(grasp.vars[21312].value(), 50)
        self.assertTrue(grasp.states[1].active)
        self.step(run, hog, sigma, fire=8)
        for _ in range(30):
            self.step(run, hog, sigma)
        self.assertFalse(grasp.states[1].active)
        self.assertEqual(self.health(run, sigma), 19970)
        for _ in range(120):
            self.step(run, hog, sigma)
        self.assertEqual(self.health(run, sigma), 19970)
        self.assertFalse(fight.vitals[sigma.body].temporary_shields)
        self.assertNotIn(sigma.body, fight.grasp_shields)

    def test_lethal_roadhog_attacks_clear_grasp_before_or_after_grant(self):
        for granted in (False, True):
            with self.subTest(granted=granted):
                run, hog, sigma, _ = self.encounter("Sigma", distance=1.5)
                fight = run.match.combat
                old_body, old_script = sigma.body, sigma.body_script
                vitals = fight.vitals[old_body]
                vitals.now[combat.HEALTH_POOL] = 20
                grasp = next(i for i in old_script.component.instances.values() if i.graph.index == 0x1D02)
                self.step(run, hog, sigma, fire=2)
                for _ in range(150 if granted else 45):
                    self.step(run, hog, sigma)
                self.assertEqual(grasp.vars[21312].value(), 50)
                self.assertEqual(grasp.states[1].active, not granted)
                self.assertEqual(old_body in fight.grasp_shields, granted)
                self.assertAlmostEqual(vitals.total, 50 if granted else 20, places=5)

                # Melee bypasses live Grasp. After conversion, a primary
                # volley can spend the temporary shield and finish the body.
                self.step(run, hog, sigma, fire=1 if granted else 0x800)
                for _ in range(30):
                    self.step(run, hog, sigma)
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, hog.body)
                self.assertEqual(vitals.total, 0)
                self.assertNotIn(old_body, fight.grasp_shields)
                if granted:
                    self.assertEqual(vitals.temporary_shields[defenses.SHIELD_TAG].now, 0)
                else:
                    self.assertFalse(vitals.temporary_shields)

                for _ in range(600):
                    run.step({})
                    if sigma.body != old_body:
                        break
                self.assertNotEqual(sigma.body, old_body)
                self.assertTrue(old_script.retired)
                self.assertNotIn(old_body, fight.grasp_grants)
                self.assertNotIn(sigma.body, fight.grasp_shields)
                target = next(t for t in fight._targets() if t.owner is sigma)
                fresh = fight.vitals_of(target)
                self.assertFalse(fresh.temporary_shields)
                self.assertFalse(fresh.dead)
                self.assertEqual(fresh.total, fresh.maximum)

    def test_back_facing_deflect_does_not_parry_melee(self):
        run, hog, defender, _ = self.encounter("Genji", distance=1.5, facing=0)
        self.step(run, hog, defender, fire=0x800, facing=0)
        for _ in range(25):
            self.step(run, hog, defender, facing=0)
        self.assertEqual(self.health(run, defender), 19970)

    def test_friendly_deflector_is_skipped_and_static_wall_wins(self):
        for mode in ("ally", "wall"):
            with self.subTest(mode=mode):
                run, hog, defender, behind = self.encounter("Genji")
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    defender.team = hog.team
                    behind.team = 1 - hog.team
                y = hog.mover.position[1]
                wall = World([(-5, y, 1, 5, y, 1, 5, y + 5, 1),
                              (-5, y, 1, 5, y + 5, 1, -5, y + 5, 1)])
                fight = run.match.combat
                collision = fight._collision()
                with patch.object(fight, "_collision", return_value=wall if mode == "wall" else collision):
                    self.step(run, hog, defender, fire=2)
                    for _ in range(35):
                        self.step(run, hog, defender)
                self.assertEqual(self.health(run, hog), 20000)
                self.assertEqual(self.health(run, defender), 20000)
                self.assertEqual(self.health(run, behind) < 20000, mode == "ally")

    def test_reflected_scrap_lifecycle_follows_the_deflector(self):
        for owner, replace_body, fragmented in (
            ("shooter", False, False),
            ("shooter", True, False),
            ("deflector", False, False),
            ("deflector", False, True),
            ("deflector", True, False),
            ("deflector", True, True),
        ):
            with self.subTest(owner=owner, replace_body=replace_body, fragmented=fragmented):
                self.check_reflected_scrap_retirement(owner, replace_body=replace_body, fragmented=fragmented)

    def check_reflected_scrap_retirement(self, owner, *, replace_body, fragmented):
        run, hog, genji, target = self.session("Genji", distance=4)
        fight = run.match.combat
        y = hog.mover.position[1]
        run.match.collision_world = flat_world(y)
        for actor, position in ((hog, (2, y, 0)), (genji, (0, y, 4)), (target, (0, y, -7))):
            actor.mover.position[:] = position
            actor.mover.body = None
        aim_at_genji = aim(fight._eye(hog), fight._eye(genji))
        run.step({genji: (-32768, 0, 16)})
        for _ in range(6):
            run.step({genji: (-32768, 0, 0)})
        run.step({hog: (*aim_at_genji, 2), genji: (-32768, 0, 0)})
        for _ in range(20):
            reflected = [rocket for rocket in fight.rockets if rocket.shooter is genji]
            if any((rocket.burst is None) == fragmented for rocket in reflected):
                break
            run.step({genji: (-32768, 0, 0)})
        else:
            self.fail("Roadhog scrap did not reach the requested reflected flight phase")
        self.assertEqual(self.health(run, target), 20000)
        player = hog if owner == "shooter" else genji
        retired_body, retired_script = player.body, player.body_script
        if replace_body:
            self.assertTrue(run.match.switch_hero(player, heroes()[0x02E000000000006E]))
            self.assertNotEqual(player.body, retired_body)
        else:
            run.match.leave(player)
            run.players.remove(player)
        self.assertTrue(retired_script.retired)
        self.assertEqual(any(rocket.shooter is genji for rocket in fight.rockets), player is hog)
        for _ in range(40):
            run.step({})
        if player is hog:
            self.assertLess(self.health(run, target), 20000)
            self.assertTrue(genji.client.messages(combat.HIT))
        else:
            self.assertEqual(self.health(run, target), 20000)

    def test_reflected_slug_expiry_and_fragments_keep_scheduler_remainder(self):
        results = []
        for gap in (False, True):
            run, hog, defender, _ = self.encounter("Genji", distance=10)
            self.step(run, hog, defender, fire=2)
            self.step(run, hog, defender)
            if gap:
                run.frame += 40
                run.match.update(run.frame * combat.TICK, run.frame)
            else:
                for _ in range(40):
                    self.step(run, hog, defender)
            results.append((self.health(run, hog), len(defender.client.messages(combat.HIT)),
                            len(run.match.combat.rockets)))
        self.assertLess(results[0][0], 20000)
        self.assertAlmostEqual(results[0][0], results[1][0], places=5)
        self.assertEqual(results[0][1:], results[1][1:])

    def test_whole_hog_reflection_reverses_force_and_attributes_damage_to_genji(self):
        run, hog, genji, behind = self.encounter("Genji")
        fight = run.match.combat
        script = hog.body_script
        ultimate, = script.ultimates
        script.set_ult_charge(script.component.entity_var(338).value())
        genji_charge = genji.body_script.component.entity_var(342)
        self.step(run, hog, genji, fire=32)
        for _ in range(65):
            before_charge = genji_charge.value()
            self.step(run, hog, genji)
            if hog.body in fight.impulses.live:
                break
        reflected = fight.impulses.live.get(hog.body)
        self.assertIsNotNone(reflected)
        self.assertAlmostEqual(reflected.velocity[2], -10)
        self.assertAlmostEqual(reflected.velocity[1], 1.8)
        self.assertAlmostEqual(reflected.velocity[0], 0)
        self.assertEqual(len(fight.impulses.seen), 1)
        self.assertNotIn(genji.body, fight.impulses.live)
        self.assertEqual(self.health(run, genji), 20000)
        self.assertEqual(self.health(run, behind), 20000)
        body = self.health(run, hog)
        self.assertLess(body, 20000)
        self.assertEqual(genji_charge.value(), before_charge)
        hits = genji.client.messages(combat.HIT)
        self.assertTrue(hits)
        self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == hog.body for hit in hits))
        self.assertEqual(sum(hit["+0x78"]["+0x4"] for hit in hits), 20000 - body)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)

        z = hog.mover.position[2]
        run.step({hog: (16384, 0, 0), genji: (-32768, 0, 0)})
        self.assertLess(hog.mover.position[2], z)
        for _ in range(150):
            gate = defenses.projectile_gate(fight._eye(hog), (0, 0, 1), 15, fight._enemies(hog))
            if gate is None:
                break
            run.step({hog: (16384, 0, 0), genji: (-32768, 0, 0)})
            self.assertEqual(self.health(run, hog), body)
        self.assertIsNone(gate)
        self.assertNotIn(hog.body, fight.impulses.live)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)

        for _ in range(30):
            self.step(run, hog, genji)
            if genji.body in fight.impulses.live:
                break
        forward = fight.impulses.live.get(genji.body)
        self.assertIsNotNone(forward)
        self.assertGreater(forward.velocity[2], 0)
        self.assertLess(self.health(run, genji), 20000)
        self.assertTrue(hog.client.messages(combat.HIT))
        self.assertEqual(self.health(run, hog), body)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_projected_barrier_absorbs_reflected_whole_hog_before_body_force(self):
        run, hog, genji, zarya = self.encounter("Genji")
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, genji.team, zarya.team = 0, 1, 0
        x, y, z = hog.mover.position
        zarya.mover.position[:] = (x - 4, y, z)
        zarya.mover.body = None
        look = aim(fight._eye(zarya), fight._eye(hog))
        for _ in range(8):
            run.step({zarya: (*look, 0), genji: (-32768, 0, 0)})
        run.step({zarya: (*look, 16), genji: (-32768, 0, 0)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        energy = zarya.body_script.component.entity_var(87)
        before = energy.value() or 0
        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
        with patch.object(fight.zarya_bubbles, "damage", wraps=fight.zarya_bubbles.damage) as absorbed:
            self.step(run, hog, genji, fire=32)
            for _ in range(65):
                self.step(run, hog, genji)
                if bubble.health < 200:
                    break
        self.assertGreater(bubble.health, 0)
        self.assertLess(bubble.health, 200)
        absorbed.assert_called()
        self.assertTrue(all(call.args[0] is bubble and call.args[2] is genji
                            for call in absorbed.call_args_list))
        self.assertAlmostEqual(energy.value() - before, (200 - bubble.health) * 0.2, places=3)
        self.assertEqual(self.health(run, hog), 20000)
        self.assertEqual(self.health(run, genji), 20000)
        self.assertFalse(fight.impulses.live)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_reflected_whole_hog_can_kill_caster_and_stops_its_channel(self):
        run, hog, defender, _ = self.encounter("Genji")
        fight = run.match.combat
        fight.vitals[hog.body] = combat.Vitals(hog.body, 100)
        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
        self.step(run, hog, defender, fire=32)
        for _ in range(100):
            self.step(run, hog, defender)
        self.assertTrue(fight.is_dead(hog))
        self.assertEqual(fight.vitals[hog.body].killer, defender.body)
        ultimate, = hog.body_script.ultimates
        self.assertFalse(ultimate.flags & 1)
        ended = len(defender.client.messages(combat.HIT))
        for _ in range(20):
            self.step(run, hog, defender)
        self.assertEqual(len(defender.client.messages(combat.HIT)), ended)


if __name__ == "__main__":
    unittest.main()
