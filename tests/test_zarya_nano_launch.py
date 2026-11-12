"""Original0068 launch Nano assertions on current single modifier/Pet hosts."""
import unittest
import test_server_snapshot as storage_fixtures
from test_game_combat import Run, aim
from ow174.game import combat, nano
from ow174.game.content import heroes

ZARYA = 0x02E0000000000068

class ZaryaNanoLaunchTests(unittest.TestCase):
    def encounter(self, projected, *, close=False, protect=True):
        run = Run(names=("Attacker", "Caster", "Ally"))
        attacker, caster, ally = run.players
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player in (attacker, caster):
            self.assertTrue(run.match.switch_hero(player, heroes()[ZARYA]))
        caster.team = ally.team = 1
        attacker.team = 0
        attacker.mover.position[:] = [0, 1, 3.5 if close else 0]
        caster.mover.position[:] = [3 if projected else 0, 1, 5]
        ally.mover.position[:] = [0, 1, 5 if projected else 20]
        victim = ally if projected else caster
        run.step({caster: (-16384 if projected else 0, 0, (16 if projected else 8) if protect else 0)})
        instance = next(i for i in caster.body_script.component.instances.values()
                        if i.graph.index == (0x025C if projected else 0x0244))
        self.assertEqual(instance.state(10 if projected else 2).active, protect)
        return run, fight, attacker, caster, victim


    @staticmethod
    def health(fight, owner):
        target = next(t for t in fight._targets() if t.owner is owner)
        return fight.vitals_of(target).total


    @staticmethod
    def energy(caster):
        return caster.body_script.component.entity_var(87).value()


    def nano_boost(self, run, ana, target):
        fight = run.match.combat
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(ana.body_script.component.entity_var(338).value())
        # Existing013B Nano candidate service uses body centre (old0068 used eye).
        body = next(t for t in fight._targets() if t.owner is target)
        angles = aim(fight._eye(ana), (body.feet[0], body.feet[1] + body.height / 2, body.feet[2]))
        run.step({ana: (*angles, 32)})
        for _ in range(25):
            run.step({ana: (*angles, 0)})
            if nano.offense(fight, target.body) > 1:
                break
        self.assertEqual(nano.offense(fight, target.body), 1.5)
        self.assertEqual(ana.body_script.component.entity_var(342).value(), 0)


    def nano_attack_scene(self, projected):
        run = Run(names=("Attacker", "Victim", "Projector", "Ana"))
        attacker, victim, projector, ana = run.players
        caster = projector if projected else victim
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player in (attacker, caster):
            self.assertTrue(run.match.switch_hero(player, heroes()[ZARYA]))
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        attacker.team = ana.team = 0
        victim.team = projector.team = 1
        attacker.mover.position[:] = [0, 1, 0]
        ana.mover.position[:] = [0, 1, -3]
        victim.mover.position[:] = [0, 1, 5]
        projector.mover.position[:] = [3 if projected else 10, 1, 5]
        self.nano_boost(run, ana, attacker)
        return run, fight, attacker, victim, caster


    def test_nano_boosted_secondary_retains_damage_after_nano_expires(self):
        run, fight, ana, zarya, _ = self.encounter(False, protect=False)
        victim = run.players[2]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.team = zarya.team
        ana.mover.position[:] = [0, 1, 0]
        victim.team = 1 - zarya.team
        victim.mover.position[:] = [0, 1, 12]
        victim.mover.body = None
        self.nano_boost(run, ana, zarya)
        run.frame = round(fight.vitals[zarya.body].nano_until / combat.TICK) - 2
        run.match.update(run.frame * combat.TICK, run.frame)
        run.step({zarya: (0, 0, 2)})
        rocket, = fight.rockets
        self.assertEqual(rocket.offense_scale, 1.5)
        self.assertAlmostEqual(rocket.blast.amount, 70.5)
        for _ in range(40):
            run.step({})
        self.assertEqual(nano.offense(fight, zarya.body), 1)
        self.assertAlmostEqual(self.health(fight, victim), 129.5)


    def test_nano_boosted_graviton_preserves_launch_scale_and_barrier_admission(self):
        for projected in (None, False, True):
            with self.subTest(projected=projected):
                run, fight, attacker, victim, caster = self.nano_attack_scene(projected)
                run.frame = round(fight.vitals[attacker.body].nano_until / combat.TICK) - 3
                run.match.update(run.frame * combat.TICK, run.frame)
                if projected is not None:
                    run.step({caster: (-16384 if projected else 0, 0, 16 if projected else 8)})
                before = self.health(fight, victim)
                attacker.body_script.ult_seconds = 0
                attacker.body_script.set_ult_charge(2100)
                run.step({attacker: (0, 0, 32)})
                for _ in range(30):
                    if fight.zarya_gravitons.fields:
                        break
                    run.step({})
                field, = fight.zarya_gravitons.fields
                self.assertEqual(nano.offense(fight, attacker.body), 1)
                self.assertEqual(field.offense_scale, 1.5)
                self.assertEqual(attacker.body_script.component.entity_var(342).value(), 0)
                after_impact = before if projected is not None else before - 7.5
                self.assertAlmostEqual(self.health(fight, victim), after_impact)

                for _ in range(20):
                    run.step({})
                barrier = None
                if projected is not None:
                    barrier = fight.barriers.authored_for(caster.body)
                    self.assertAlmostEqual(barrier.vitals.total, 190.1)
                    self.assertAlmostEqual(self.energy(caster), 1.98, places=4)
                    self.assertEqual(self.health(fight, victim), before)

                while run.frame < field.end_tick:
                    run.step({})
                self.assertFalse(fight.zarya_gravitons.fields)
                absorbed = 200 - barrier.vitals.total if barrier is not None else 0
                self.assertAlmostEqual(before - self.health(fight, victim), 37.5 - absorbed, places=4)



class ZaryaNanoStorageTests(unittest.TestCase):
    def test_real_flight_and_field_launch_scalars_are_causal(self):
        from ow174.game.server_snapshot import ServerSnapshotError, snapshot_digest

        case = storage_fixtures.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        match = player.match
        self.assertTrue(session.apply("switch_hero", {"account": player.account_lo, "hero": ZARYA}))
        fight = match.combat
        player.body_script.set_ult_charge(2100)
        peers[0].commands(player.body_script.frame + 1, 1, 32, pitch=8192)
        rocket, = fight.rockets
        before = snapshot_digest(case.capture(session))
        rocket.offense_scale += .5
        self.assertNotEqual(snapshot_digest(case.capture(session)), before)
        rocket.offense_scale -= .5
        self.assertEqual(snapshot_digest(case.capture(session)), before)
        for step in range(1, 301):
            session.tick(100.8 + step * combat.TICK)
            if fight.zarya_gravitons.fields:
                break
        field, = fight.zarya_gravitons.fields
        self.assertEqual(field.offense_scale, rocket.offense_scale)
        before = snapshot_digest(case.capture(session))
        field.offense_scale += .5
        self.assertNotEqual(snapshot_digest(case.capture(session)), before)
        field.offense_scale -= .5
        self.assertEqual(snapshot_digest(case.capture(session)), before)
        field.unregistered = True
        with self.assertRaises(ServerSnapshotError):
            case.capture(session)
        del field.unregistered
        self.assertEqual(snapshot_digest(case.capture(session)), before)
