"""Actual Trooper close attack through the opt-in NPC engagement."""
import unittest

import test_zenyatta_npcs as scenes

from ow174.game import heavy_assault, npc_control


class TrooperMelee(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scenes.ZenyattaNpcTests.setUpClass()
        cls.addClassCleanup(scenes.ZenyattaNpcTests.tearDownClass)

    def test_melee_damage_and_transcendence(self):
        for mode in ("normal", "q", "lethal"):
            with self.subTest(mode=mode):
                run, zen, trooper, _ = scenes.ZenyattaNpcTests().arena(scenes.ZenyattaNpcTests.catalog["1"])
                x, y, z = zen.mover.position
                trooper.position = (x, y, z+2)
                for _ in range(35):
                    run.step({})
                fight = run.match.combat
                health = fight.vitals[zen.body]
                if mode == "q":
                    script = zen.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                elif mode == "lethal":
                    health.damage(175)
                self.assertEqual(npc_control.start(run.match, zen, special_attack=True), trooper.entity)
                instance = heavy_assault.ability(trooper.body_script.component).instance
                for _ in range(80):
                    run.frame += 1
                    run.match.update(run.frame*.016, run.frame)
                    impact = instance.states.get(instance.graph.node(4).state)
                    if impact and impact.counter:
                        break
                self.assertTrue(impact and impact.counter)
                self.assertEqual(health.total, {"normal": 170, "q": 200, "lethal": 0}[mode])
                if mode == "lethal":
                    self.assertEqual(health.killer, trooper.entity)
                else:
                    self.assertEqual(zen.mover.velocity, (0.0, 0.0, -1.0))

    def test_sleep_interrupts_windup_and_melee_resumes_after_expiry(self):
        from test_game_combat import aim

        from ow174.game.content import heroes

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
