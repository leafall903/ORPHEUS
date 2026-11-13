"""Manual heavy engagement executes authored motion and impact against Zen."""
import math
import unittest
from itertools import product

import test_zenyatta_nano as nano_tests
import test_zenyatta_npcs as scenes
from test_game_combat import aim

from ow174.game import combat, heavy_assault, npc_control
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.zenyatta import HERO


class HeavyCharge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scenes.ZenyattaNpcTests.setUpClass()
        cls.addClassCleanup(scenes.ZenyattaNpcTests.tearDownClass)

    def test_future_zen_command_starts_orb_flight_only_at_world_endpoint(self):
        fixture = scenes.ZenyattaNpcTests()
        run, zen, bot, _ = fixture.arena(fixture.catalog["5"])
        x, y, z = zen.mover.position
        bot.position = (x, y, z + 10)
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        self.assertEqual(npc_control.start(run.match, zen, charge=True), bot.entity)
        future = run.frame + 4
        yaw, pitch = aim(fight._eye(zen), (x, y + 1, z + 10))
        zen.take_commands([Command(future, yaw=yaw, pitch=pitch, buttons=FIRE, extra=0)])
        rocket, = (rocket for rocket in fight.rockets if rocket.shooter is zen)
        self.assertEqual(rocket.flown, 0)
        run.frame = future - 1
        run.step({})
        self.assertIn(rocket, fight.rockets)
        self.assertEqual(rocket.last_tick, future)
        self.assertEqual(rocket.flown, 0)
        run.step({})
        self.assertEqual(rocket.last_tick, future + 1)
        self.assertAlmostEqual(rocket.flown, combat.TICK)

    def test_actual_zen_orb_and_heavy_charge_resolve_before_on_and_after_impact(self):
        cases = ((86, 40, 87, 200), (87, 40, 88, 200), (88, 40, 89, 25), (87, 120, 88, 25))
        fixture = scenes.ZenyattaNpcTests()
        for (fire_offset, start_hp, hit_offset, expected_zen_hp), skipped in product(cases, (False, True)):
            with self.subTest(fire_offset=fire_offset, start_hp=start_hp, skipped=skipped):
                run, zen, bot, bot_vitals = fixture.arena(fixture.catalog["5"])
                x, y, z = zen.mover.position
                bot.position = (x, y, z + 10)
                for _ in range(35):
                    run.step({})
                fight = run.match.combat
                zen_vitals = fight.vitals[zen.body]
                zen_owner = fight.zenyatta.owners[zen.body]
                previous_damage = zen_owner.last_damage
                bot_vitals.damage(bot_vitals.total - start_hp)
                self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
                ability = heavy_assault.ability(bot.body_script.component, 0x1408, 82)
                phase = ability.instance
                start = run.frame
                for _ in range(fire_offset):
                    run.step({})
                self.assertIsNotNone(bot.special_mover)
                bx, by, bz = bot.position
                direction = aim(fight._eye(zen), (bx, by + 1, bz))
                run.step({zen: (*direction, FIRE)})
                self.assertTrue(any(rocket.shooter is zen for rocket in fight.rockets))
                if skipped:
                    run.frame += 2
                while run.frame < start + 102:
                    run.step({})
                hits = zen.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                hit = hits[0]
                lethal = start_hp == 40
                self.assertEqual(hit["+0x84"], start + hit_offset + 1)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
                self.assertEqual(hit["+0x78"]["+0x4"], 40 if lethal else 96)
                flags = (combat.KILLING if lethal else 0) | (combat.CRITICAL if fire_offset >= 87 else 0)
                self.assertEqual(hit["+0x78"]["+0x8"], flags)
                self.assertEqual(zen_vitals.total, expected_zen_hp)
                if expected_zen_hp == 200:
                    self.assertEqual(zen_owner.last_damage, previous_damage)
                else:
                    self.assertAlmostEqual(zen_owner.last_damage, (start + 89) * combat.TICK)
                self.assertEqual(bot.dead, lethal)
                if lethal:
                    self.assertEqual(bot_vitals.death_tick, start + hit_offset + 1)
                    self.assertEqual(bot_vitals.killer, zen.body)
                    self.assertIn(bot, run.match.bots)
                    self.assertIsNone(bot.special_mover)
                    self.assertIsNone(bot.special_target_body)
                else:
                    self.assertEqual(bot_vitals.total, 24)
                impact = phase.states.get(phase.graph.node(102).state)
                self.assertEqual(bool(impact and impact.counter), expected_zen_hp == 25)

    def test_actual_charge_moves_and_damages_zen(self):
        run, zen, bot, _ = scenes.ZenyattaNpcTests().arena(scenes.ZenyattaNpcTests.catalog["5"])
        x, y, z = zen.mover.position
        bot.position = (x, y, z+10)
        start = bot.position
        for _ in range(35):
            run.step({})
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        fight = run.match.combat
        saw_phase = False
        for _ in range(140):
            run.step({})
            phase = heavy_assault.ability(bot.body_script.component, 0x1408, 82).instance
            moving = phase.states.get(phase.graph.node(71).state)
            if moving is not None and moving.active and not saw_phase:
                for logical, radius in ((117, 3), (145, 2)):
                    node = phase.graph.node(logical)
                    explosion = phase.states[node.state]
                    self.assertEqual(explosion.counter, 1)
                    self.assertEqual(explosion.evaluate(node.field("m_radius"), watch=False), radius)
                    direction = explosion.evaluate(node.field("m_direction"), watch=False)
                    if logical == 145:
                        for value, expected in zip(direction, (0, 0, -1), strict=True):
                            self.assertAlmostEqual(value, expected)
                    else:
                        self.assertIsNone(direction)
                self.assertEqual(fight.vitals[zen.body].total, 200)
            saw_phase |= bool(moving and moving.active)
            if fight.vitals[zen.body].total < 200:
                break
        hp = fight.vitals[zen.body].total
        self.assertTrue(saw_phase)
        self.assertGreater(math.dist(bot.position, start), 8)
        self.assertEqual(hp, 25)

    def test_projected_bubble_absorbs_charge_impact_before_body_damage(self):
        fixture = scenes.ZenyattaNpcTests()
        run, zen, bot, _ = fixture.arena(fixture.catalog["5"], names=("Zen", "Zarya"))
        zarya = run.players[1]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
        zarya.team = zen.team
        x, y, z = zen.mover.position
        zarya.mover.position = [x+4, y, z]
        zarya.mover.body = None
        bot.position = (x, y, z+10)
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        for _ in range(75):
            run.step({})
            if bot.special_mover is not None:
                break
        self.assertIsNotNone(bot.special_mover)
        self.assertEqual(fight.vitals[zen.body].total, 200)
        direction = aim(fight._eye(zarya), (x, y+1, z))
        run.step({zarya: (*direction, 16)})
        bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
        self.assertEqual(bubble.target, zen.body)
        self.assertEqual(bubble.hp, 200)
        phase = heavy_assault.ability(bot.body_script.component, 0x1408, 82).instance
        for _ in range(65):
            run.step({})
            impact = phase.states.get(phase.graph.node(102).state)
            if impact is not None and impact.counter:
                break
        self.assertTrue(impact and impact.counter)
        self.assertEqual(bubble.hp, 25)
        self.assertEqual(fight.vitals[zen.body].total, 200)

    def test_actual_dart_stops_charge_and_later_weapon_recovers(self):
        fixture = scenes.ZenyattaNpcTests()
        run, zen, bot, _ = fixture.arena(fixture.catalog["5"], names=("Zen", "Ana"))
        ana = run.players[1]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.team = zen.team
        x, y, z = zen.mover.position
        ana.mover.position = [x, y, z-4]
        ana.mover.body = None
        bot.position = (x, y, z+14)
        origin = bot.position
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        phase = heavy_assault.ability(bot.body_script.component, 0x1408, 82).instance
        for _ in range(90):
            run.step({})
            if math.dist(bot.position, origin) > 1:
                break
        self.assertGreater(math.dist(bot.position, origin), 1)
        self.assertIsNotNone(bot.special_mover)
        bx, by, bz = bot.position
        direction = aim(fight._eye(ana), (bx, by+1, bz))
        run.step({ana: (*direction, 8)})
        for _ in range(55):
            run.step({ana: (*direction, 0)})
            if bot.entity in fight.sleeps:
                break
        self.assertIn(bot.entity, fight.sleeps)
        run.step({})
        self.assertIsNone(bot.special_mover)
        stopped = bot.position
        hp = fight.vitals[zen.body]
        self.assertEqual(hp.total, 200)
        end = fight.sleeps[bot.entity][1]
        while run.frame < end-1:
            run.step({})
            self.assertEqual(bot.position, stopped)
            self.assertEqual(hp.total, 200)
            self.assertFalse(bot.attack_input)
        impact = phase.states.get(phase.graph.node(102).state)
        self.assertFalse(impact and impact.counter)
        for _ in range(400):
            run.step({})
            if hp.total < 200:
                break
        self.assertNotIn(bot.entity, fight.sleeps)
        self.assertLess(hp.total, 200)
        self.assertGreater(hp.total, 0)

    def test_stop_midcharge_cancels_movement_damage_and_starts_cooldown(self):
        run, zen, bot, _ = scenes.ZenyattaNpcTests().arena(scenes.ZenyattaNpcTests.catalog["5"])
        x, y, z = zen.mover.position
        bot.position = (x, y, z+10)
        start = bot.position
        for _ in range(35):
            run.step({})
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        for _ in range(75):
            run.step({})
            if math.dist(bot.position, start) > 2:
                break
        self.assertGreater(math.dist(bot.position, start), 2)
        self.assertEqual(npc_control.stop(run.match), 1)
        stopped = bot.position
        ability = heavy_assault.ability(bot.body_script.component, 0x1408, 82)
        self.assertAlmostEqual(ability.remaining(), 15, delta=.1)
        for _ in range(90):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertEqual(run.match.combat.vitals[zen.body].total, 200)

    def test_charge_impact_does_not_damage_transcendent_zen(self):
        run, zen, bot, _ = scenes.ZenyattaNpcTests().arena(scenes.ZenyattaNpcTests.catalog["5"])
        x, y, z = zen.mover.position
        bot.position = (x, y, z+10)
        for _ in range(35):
            run.step({})
        script = zen.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({zen: (0, 0, 0x20)})
        fight = run.match.combat
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        phase = heavy_assault.ability(bot.body_script.component, 0x1408, 82).instance
        impact = None
        for _ in range(100):
            run.step({})
            impact = phase.states.get(phase.graph.node(102).state)
            if impact is not None and impact.counter:
                break
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertIsNotNone(impact)
        self.assertGreater(impact.counter, 0)


    def test_transcendence_started_during_charge_blocks_the_later_impact(self):
        fixture = scenes.ZenyattaNpcTests()
        run, zen, bot, _ = fixture.arena(fixture.catalog["5"])
        x, y, z = zen.mover.position
        bot.position = (x, y, z+10)
        start = bot.position
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        owner = fight.zenyatta.owners[zen.body]
        last_damage = owner.last_damage
        script = zen.body_script
        charge = script.component.entity_var(342)
        script.set_ult_charge(script.component.entity_var(338).value())
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        phase = heavy_assault.ability(bot.body_script.component, 0x1408, 82).instance
        impact_node = phase.graph.node(102)
        for _ in range(75):
            run.step({})
            if bot.special_mover is not None and math.dist(bot.position, start) > 1:
                break
        else:
            self.fail("Heavy never began moving toward Zen")
        impact = phase.states.get(impact_node.state)
        self.assertFalse(impact and impact.counter)
        self.assertFalse(fight.zenyatta.ultimate(zen))

        run.step({zen: (0, 0, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(charge.value(), 0)
        for _ in range(75):
            impact = phase.states.get(impact_node.state)
            if impact is not None and impact.counter:
                break
            run.step({})
        else:
            self.fail("The in-progress charge never reached its impact phase")
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertGreater(math.dist(bot.position, start), 8)
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertEqual(owner.last_damage, last_damage)
        self.assertEqual(zen.mover.velocity, (0.0, 0.0, 0.0))
        self.assertEqual(charge.value(), 0)

    def test_charge_impact_crosses_transcendence_expiry_with_projection(self):
        for delay, projected in ((285, True), (286, True), (286, False)):
            with self.subTest(delay=delay, projected=projected):
                fixture = scenes.ZenyattaNpcTests()
                run, zen, bot, _ = fixture.arena(fixture.catalog["5"], names=("Zen", "Zarya"))
                zarya = run.players[1]
                self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
                zarya.team = zen.team
                x, y, z = zen.mover.position
                zarya.mover.position = [x + 4, y, z]
                zarya.mover.body = None
                bot.position = (x, y, z + 10)
                for _ in range(35):
                    run.step({})
                fight = run.match.combat
                hp = fight.vitals[zen.body]
                direction = aim(fight._eye(zarya), (x, y + 1, z))
                script = zen.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({zen: (0, 0, 0x20)})
                self.assertTrue(fight.zenyatta.ultimate(zen))
                owner = fight.zenyatta.owner(zen)
                last_damage = owner.last_damage
                phase = heavy_assault.ability(bot.body_script.component, 0x1408, 82).instance
                impact_index = phase.graph.node(102).state
                bubble = None
                moved_during_ultimate = False

                # One frame of engagement delay moves impact from the final protected update to the next.
                for tick in range(1, 376):
                    active_before = fight.zenyatta.ultimate(zen)
                    if tick == delay + 1:
                        self.assertTrue(active_before)
                        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
                    cast = projected and bubble is None and bot.special_mover is not None
                    run.step({zarya: (*direction, 16)} if cast else {})
                    if cast:
                        bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                        self.assertEqual((bubble.target, bubble.hp), (zen.body, 200))
                    moved_during_ultimate |= active_before and bot.special_mover is not None
                    impact = phase.states.get(impact_index)
                    if impact and impact.counter:
                        self.assertEqual(impact.counter, 1)
                        self.assertEqual(tick, delay + 89)
                        self.assertEqual(active_before, delay == 285)
                        break
                    self.assertEqual(hp.total, 200)
                else:
                    self.fail("Heavy Assault never reached the authored charge impact")

                self.assertTrue(moved_during_ultimate)
                self.assertFalse(fight.zenyatta.ultimate(zen))
                self.assertEqual(zen.mover.velocity, (0.0, 0.0, 0.0))
                if projected:
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.hp, 200 if delay == 285 else 25)
                    self.assertEqual(hp.total, 200)
                    self.assertEqual(owner.last_damage, last_damage)
                else:
                    self.assertEqual(hp.total, 25)
                    self.assertAlmostEqual(owner.last_damage, fight.now)

    def test_charge_impact_samples_nano_expiry_discord_and_projection(self):
        for case, skipped in product(("active", "expired", "projection"), (False, True)):
            with self.subTest(case=case, skipped=skipped):
                fixture = scenes.ZenyattaNpcTests()
                run, zen, bot, _ = fixture.arena(
                    fixture.catalog["5"], names=("Zen", "Ana", "Enemy Zen", "Zarya"))
                match = run.match
                ana, marker, zarya = run.players[1:]
                x, y, z = zen.mover.position
                for actor, hero, team, dx in (
                    (ana, nano_tests.ANA, zen.team, 5),
                    (marker, HERO, bot.team, -5),
                    (zarya, ZARYA, zen.team, 8),
                ):
                    self.assertTrue(match.switch_hero(actor, heroes()[hero]))
                    actor.team = team
                    actor.mover.position = [x + dx, y, z]
                    actor.mover.body = None
                origin = bot.position = (x, y, z + 10)
                for _ in range(35):
                    run.step({})
                fight = match.combat
                hp = fight.vitals[zen.body]
                owner = fight.zenyatta.owner(zen)
                last_damage = owner.last_damage
                nano_tests.ZenyattaNanoTests().cast(run, ana, zen)
                expiry = hp.nano_until
                self.assertGreater(expiry, fight.now)
                lead = 89 if case == "expired" else 90
                while (run.frame + lead) * combat.TICK < expiry:
                    run.step({})
                self.assertEqual(npc_control.start(match, zen, special_attack=True), bot.entity)
                start = run.frame
                phase = heavy_assault.ability(bot.body_script.component, 0x1408, 82).instance
                impact_index = phase.graph.node(102).state
                marker_aim = aim(fight._eye(marker), (x, y + combat.EYE, z))
                project_aim = aim(fight._eye(zarya), (x, y + 1, z))
                discord = bubble = None
                for _ in range(100):
                    moving = bot.special_mover is not None
                    if moving and discord is None:
                        self.assertLess(fight.now, expiry)
                        run.step({marker: (*marker_aim, 0x08)})
                        discord = fight.zenyatta.owner(marker).discord
                        self.assertEqual(discord.target, zen.body)
                    elif moving and case == "projection" and bubble is None:
                        run.step({zarya: (*project_aim, 0x10)})
                        bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                        self.assertEqual((bubble.target, bubble.hp), (zen.body, 200))
                        self.assertIsNone(fight.zenyatta.owner(marker).discord)
                    else:
                        if skipped and run.frame == start + 86:
                            run.frame += 2
                        run.step({})
                    impact = phase.states.get(impact_index)
                    if impact and impact.counter:
                        self.assertEqual(impact.counter, 1)
                        break
                    self.assertEqual(hp.total, 200)
                else:
                    self.fail("Heavy Assault never reached the authored charge impact")

                self.assertIsNotNone(discord)
                self.assertGreater(math.dist(bot.position, origin), 8)
                self.assertEqual(run.frame, start + 89)
                self.assertEqual(fight.now >= expiry, case == "expired")
                self.assertEqual(zen.mover.velocity, (0.0, 0.0, 0.0))
                if case == "projection":
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.hp, 25)
                    self.assertEqual(hp.total, 200)
                    self.assertEqual(owner.last_damage, last_damage)
                elif case == "expired":
                    self.assertEqual(hp.total, 0)
                    self.assertEqual((hp.killer, hp.death_tick), (bot.entity, run.frame))
                    self.assertIs(fight.zenyatta.owner(marker).discord, None)
                else:
                    self.assertIs(fight.zenyatta.owner(marker).discord, discord)
                    self.assertAlmostEqual(hp.total, 90.625)
                    self.assertAlmostEqual(owner.last_damage, fight.now)

    def test_wall_appearing_during_charge_cancels_impact(self):
        from test_game_mover import box, wall

        from ow174.game.collision import World

        run, zen, bot, _ = scenes.ZenyattaNpcTests().arena(scenes.ZenyattaNpcTests.catalog["5"])
        x, y, z = zen.mover.position
        bot.position = (x, y, z+10)
        for _ in range(35):
            run.step({})
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        for _ in range(75):
            run.step({})
            if bot.position[2] < z+8:
                break
        self.assertLess(bot.position[2], z+8)
        boundary = z+4
        run.match.collision_world = World(
            box(x-25, x+25, y-2, y, z-25, z+25) + wall(x-8, boundary, x+8, boundary, y, y+5)
        )
        stopped = bot.position
        for _ in range(65):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertEqual(run.match.combat.vitals[zen.body].total, 200)
