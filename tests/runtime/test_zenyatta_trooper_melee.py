"""Actual Trooper close attack through the opt-in NPC engagement."""
import unittest
from itertools import product
from unittest.mock import patch

import test_zenyatta_npcs as scenes
from test_game_combat import aim

from ow174.game import combat, heavy_assault, npc_control
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import SOLDIER, heroes
from ow174.game.zenyatta import HERO


class TrooperMelee(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scenes.ZenyattaNpcTests.setUpClass()
        cls.addClassCleanup(scenes.ZenyattaNpcTests.tearDownClass)

    def melee_arena(self):
        fixture = scenes.ZenyattaNpcTests()
        run, zen, trooper, trooper_vitals = fixture.arena(fixture.catalog["1"], names=("Zen", "Zarya"))
        zarya = run.players[1]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
        zarya.team = zen.team
        x, y, z = zen.mover.position
        trooper.position = (x, y, z + 2)
        zarya.mover.position = [x + 4, y, z]
        zarya.mover.body = None
        for _ in range(35):
            run.step({})
        return run, zen, trooper, zarya, trooper_vitals

    def begin_melee(self, run, zen, trooper):
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), trooper.entity)
        run.step({})
        ability = heavy_assault.ability(trooper.body_script.component)
        self.assertTrue(ability.flags & ability.RUNNING)
        impact = ability.instance.states.get(ability.instance.graph.node(4).state)
        self.assertFalse(impact and impact.counter)
        return ability

    def windup(self):
        run, zen, trooper, zarya, trooper_vitals = self.melee_arena()
        ability = self.begin_melee(run, zen, trooper)
        return run, zen, trooper, zarya, ability, trooper_vitals

    def test_actual_winding_melee_samples_late_projection_before_body_and_push(self):
        for break_bubble in (False, True):
            with self.subTest(break_bubble=break_bubble):
                run, zen, trooper, zarya, ability, _ = self.windup()
                fight = run.match.combat
                x, y, z = zen.mover.position
                direction = aim(fight._eye(zarya), (x, y + 1.5, z))
                run.step({zarya: (*direction, 0x10)})
                key = zarya.body, PROJECTED
                bubble = fight.barriers.bubbles[key]
                self.assertEqual(bubble.target, zen.body)
                self.assertEqual(bubble.hp, 200)
                if break_bubble:
                    bubble.hp = 20
                vitals = fight.vitals[zen.body]
                last_damage = fight.zenyatta.owners[zen.body].last_damage
                for _ in range(50):
                    run.step({})
                    impact = ability.instance.states.get(ability.instance.graph.node(4).state)
                    if impact and impact.counter:
                        break
                self.assertTrue(impact and impact.counter)
                self.assertEqual(vitals.total, 200)
                self.assertEqual(fight.zenyatta.owners[zen.body].last_damage, last_damage)
                self.assertEqual(zen.mover.velocity, (0.0, 0.0, 0.0))
                self.assertEqual(bubble.hp, 0 if break_bubble else 170)
                self.assertEqual(key in fight.barriers.bubbles, not break_bubble)
                energy = zarya.body_script.component.entity_var(87, create=False)
                self.assertAlmostEqual(energy.value(), 4 if break_bubble else 6, places=4)

    def test_actual_melee_evaluates_explosion_pose_even_when_projection_blocks_damage(self):
        for case in ("open", "projection", "stop"):
            with self.subTest(case=case):
                run, zen, trooper, zarya, ability, _ = self.windup()
                fight = run.match.combat
                instance = ability.instance
                node = instance.graph.node(27)
                self.assertEqual(node.cls, "STUStatescriptStatePhysicsExplosion")
                bubble = None
                if case == "projection":
                    x, y, z = zen.mover.position
                    direction = aim(fight._eye(zarya), (x, y + 1.5, z))
                    run.step({zarya: (*direction, 0x10)})
                    bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                    self.assertEqual(bubble.target, zen.body)
                elif case == "stop":
                    self.assertEqual(npc_control.stop(run.match), 1)

                for _ in range(55):
                    run.step({})
                    explosion = instance.states.get(node.state)
                    if explosion and explosion.counter:
                        break
                hp = fight.vitals[zen.body]
                if case == "stop":
                    self.assertFalse(explosion and explosion.counter)
                    self.assertEqual(hp.total, 200)
                    continue
                self.assertTrue(explosion and explosion.counter)
                self.assertEqual(explosion.counter, 1)
                self.assertEqual(explosion.evaluate(node.field("m_radius"), watch=False), .75)
                self.assertEqual(explosion.evaluate(node.field("m_6EFD110C"), watch=False), 30)
                self.assertEqual(explosion.evaluate(node.field("m_D80AEDB1"), watch=False), 1.25)
                direction = explosion.evaluate(node.field("m_direction"), watch=False)
                self.assertIsNotNone(direction)
                for actual, expected in zip(direction, (0, 0, -1), strict=True):
                    self.assertAlmostEqual(actual, expected, places=5)
                x, y, z = trooper.position
                center = explosion.evaluate(node.field("m_position"), watch=False)
                for actual, expected in zip(center, (x, y, z - 1.25), strict=True):
                    self.assertAlmostEqual(actual, expected, places=5)
                self.assertEqual(hp.total, 170 if case == "open" else 200)
                if bubble is not None:
                    self.assertEqual(bubble.hp, 170)

    def test_actual_winding_melee_revalidates_wall_range_team_and_body(self):
        for case in ("wall", "range", "team", "body"):
            with self.subTest(case=case):
                run, zen, trooper, _, ability, _ = self.windup()
                fight = run.match.combat
                x, y, z = zen.mover.position
                body = zen.body
                if case == "wall":
                    plane = z + 1
                    run.match.collision_world = World([
                        (x-40, y, z-40, x+40, y, z+40, x+40, y, z-40),
                        (x-40, y, z-40, x-40, y, z+40, x+40, y, z+40),
                        (x-4, y-2, plane, x+4, y-2, plane, x-4, y+4, plane),
                        (x+4, y-2, plane, x+4, y+4, plane, x-4, y+4, plane),
                    ])
                elif case == "range":
                    trooper.position = (x, y, z + 5)
                elif case == "team":
                    trooper.team = zen.team
                else:
                    self.assertTrue(run.match.switch_hero(zen, heroes()[SOLDIER]))
                    self.assertNotEqual(zen.body, body)
                for _ in range(50):
                    run.step({})
                    self.assertEqual(fight.vitals[zen.body].total, 200)
                impact = ability.instance.states.get(ability.instance.graph.node(4).state)
                self.assertFalse(impact and impact.counter)
                self.assertFalse(ability.flags & ability.RUNNING)
                self.assertEqual(zen.mover.velocity[2], 0.0)
                if case == "body":
                    self.assertFalse(trooper.firing)
                    self.assertIsNone(trooper.special_target_body)
                    self.assertTrue(run.match.switch_hero(zen, heroes()[HERO]))
                    zen.mover.position = [x, y, z]
                    zen.mover.body = None
                    run.step({})
                    self.assertEqual(npc_control.start(run.match, zen, special_attack=True), trooper.entity)
                    for _ in range(80):
                        run.step({})
                        if fight.vitals[zen.body].total < 200:
                            break
                    self.assertEqual(fight.vitals[zen.body].total, 170)

    def test_actual_zen_primary_kills_trooper_before_melee_impact(self):
        run, zen, trooper, _, ability, npc_vitals = self.windup()
        fight = run.match.combat
        npc_vitals.damage(npc_vitals.total - 25)
        x, y, z = trooper.position
        direction = aim(fight._eye(zen), (x, y + 1, z))
        run.step({zen: (*direction, FIRE)})
        for _ in range(55):
            run.step({})
        self.assertTrue(trooper.dead)
        self.assertEqual(npc_vitals.killer, zen.body)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertEqual(zen.mover.velocity[2], 0.0)
        hits = zen.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["+0x78"]["+0x8"], combat.KILLING)
        impact = ability.instance.states.get(ability.instance.graph.node(4).state)
        self.assertFalse(impact and impact.counter)

    def test_melee_damage_and_transcendence(self):
        for mode in ("normal", "q", "late_q", "lethal"):
            with self.subTest(mode=mode):
                run, zen, trooper, _ = scenes.ZenyattaNpcTests().arena(scenes.ZenyattaNpcTests.catalog["1"])
                x, y, z = zen.mover.position
                trooper.position = (x, y, z+2)
                for _ in range(35):
                    run.step({})
                fight = run.match.combat
                health = fight.vitals[zen.body]
                owner = fight.zenyatta.owners[zen.body]
                damage_clock = owner.last_damage
                script = zen.body_script
                if mode in ("q", "late_q"):
                    script.set_ult_charge(script.component.entity_var(338).value())
                if mode == "q":
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                elif mode == "lethal":
                    health.damage(175)
                self.assertEqual(npc_control.start(run.match, zen, special_attack=True), trooper.entity)
                ability = heavy_assault.ability(trooper.body_script.component)
                instance = ability.instance
                if mode == "late_q":
                    run.step({})
                    self.assertTrue(ability.flags & ability.RUNNING)
                    impact = instance.states.get(instance.graph.node(4).state)
                    self.assertFalse(impact and impact.counter)
                    self.assertFalse(fight.zenyatta.ultimate(zen))
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                    self.assertEqual(script.component.entity_var(342).value(), 0)
                for _ in range(80):
                    run.frame += 1
                    run.match.update(run.frame*.016, run.frame)
                    impact = instance.states.get(instance.graph.node(4).state)
                    if impact and impact.counter:
                        break
                self.assertTrue(impact and impact.counter)
                self.assertEqual(health.total, {"normal": 170, "q": 200, "late_q": 200, "lethal": 0}[mode])
                if mode in ("q", "late_q"):
                    self.assertEqual(owner.last_damage, damage_clock)
                if mode == "lethal":
                    self.assertEqual(health.killer, trooper.entity)
                else:
                    self.assertEqual(zen.mover.velocity, (0.0, 0.0, -1.0))

    def test_melee_impact_crosses_transcendence_expiry_with_projection(self):
        run, zen, trooper, _, _ = self.melee_arena()
        began = run.frame
        ability = self.begin_melee(run, zen, trooper)
        for _ in range(55):
            run.step({})
            impact = ability.instance.states.get(ability.instance.graph.node(4).state)
            if impact and impact.counter:
                break
        else:
            self.fail("Calibration did not reach the authored Trooper melee impact")
        windup_frames = run.frame - began

        for after_expiry, projected, skipped in product((False, True), repeat=3):
            with self.subTest(after_expiry=after_expiry, projected=projected, skipped=skipped):
                run, zen, trooper, zarya, _ = self.melee_arena()
                fight = run.match.combat
                hp = fight.vitals[zen.body]
                owner = fight.zenyatta.owner(zen)
                last_damage = owner.last_damage
                script = zen.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({zen: (0, 0, 0x20)})
                self.assertTrue(fight.zenyatta.ultimate(zen))
                self.assertEqual(script.component.entity_var(342).value(), 0)
                expiry = fight.zenyatta.ultimate_last_world_tick(zen)
                impact_tick = expiry + int(after_expiry)
                while run.frame < impact_tick - windup_frames:
                    run.step({})
                ability = self.begin_melee(run, zen, trooper)
                bubble = None
                if projected:
                    angles = aim(fight._eye(zarya), fight._eye(zen))
                    run.step({zarya: (*angles, 0x10)})
                    bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                    self.assertEqual((bubble.target, bubble.hp), (zen.body, 200))
                while run.frame < impact_tick - (4 if skipped else 1):
                    run.step({})
                self.assertEqual(hp.total, 200)
                self.assertTrue(ability.flags & ability.RUNNING)

                damage = fight._damage
                attempts = []

                def observe(source, target, amount, *args, **kwargs):
                    if source is trooper and target.owner is zen:
                        attempts.append((fight.tick, fight.zenyatta.ultimate(zen), amount))
                    return damage(source, target, amount, *args, **kwargs)

                with patch.object(fight, "_damage", side_effect=observe):
                    run.frame = impact_tick
                    run.match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(attempts, [(impact_tick, not after_expiry, 30)])
                impact = ability.instance.states.get(ability.instance.graph.node(4).state)
                self.assertEqual(impact.counter, 1)
                self.assertEqual(zen.mover.velocity, (0.0, 0.0, 0.0 if projected else -1.0))
                if after_expiry and not projected:
                    self.assertEqual(hp.total, 170)
                    self.assertAlmostEqual(owner.last_damage, fight.now)
                else:
                    self.assertEqual(hp.total, 200)
                    self.assertEqual(owner.last_damage, last_damage)
                if projected:
                    self.assertEqual(bubble.hp, 170 if after_expiry else 200)
                    energy = zarya.body_script.component.entity_var(87, create=False)
                    if after_expiry:
                        self.assertAlmostEqual(energy.value(), 6, places=4)
                    else:
                        self.assertIsNone(energy.value())

    def test_sleep_interrupts_windup_and_melee_resumes_after_expiry(self):
        fixture = scenes.ZenyattaNpcTests()
        run, zen, trooper, _ = fixture.arena(fixture.catalog["1"], names=("Zen", "Ana"))
        ana = run.players[1]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.team = zen.team
        x, y, z = zen.mover.position
        trooper.position = (x, y, z+2)
        ana.mover.position = [x+3, y, z+2]
        ana.mover.body = None
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        angles = aim(fight._eye(ana), (x, y+1, z+2))
        run.step({ana: (*angles, 8)})
        for _ in range(13):
            run.step({ana: (*angles, 0)})
        self.assertNotIn(trooper.entity, fight.sleeps)
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), trooper.entity)
        ability = heavy_assault.ability(trooper.body_script.component)
        run.step({})
        self.assertTrue(ability.flags & ability.RUNNING)
        for _ in range(40):
            run.step({ana: (*angles, 0)})
            if trooper.entity in fight.sleeps:
                break
        self.assertIn(trooper.entity, fight.sleeps)
        self.assertFalse(ability.flags & ability.RUNNING)
        hp = fight.vitals[zen.body]
        while run.frame < fight.sleeps[trooper.entity][1]-1:
            run.step({})
            self.assertEqual(hp.total, 200)
        for _ in range(75):
            run.step({})
            if hp.total < 200:
                break
        self.assertEqual(hp.total, 170)
