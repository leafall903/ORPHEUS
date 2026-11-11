import unittest

import test_mei_blizzard as blizzard_fixtures
import test_mei_combat as combat_fixtures
import test_mei_shields as shield_fixtures

from ow174.game import combat, mei, sombra_hack
from ow174.game.content import heroes


class MeiSombraInterruptionTests(unittest.TestCase):
    def test_hack_and_emp_interrupt_only_unreleased_actions(self):
        for attack in (2, 32):
            for action in ("preview", "windup", "released"):
                with self.subTest(attack=attack, action=action):
                    self.check_interruption(attack, action)

    def check_interruption(self, attack, action):
        run, victim, sombra, _ = combat_fixtures.MeiCombatTests().duel()
        assert run.match.switch_hero(sombra, heroes()[sombra_hack.HERO])
        fight = run.match.combat
        x, y, z = victim.mover.position
        fight.walls.place_player(sombra, (x, y, z + 4.0), ((0, 1, 0),))
        sombra.mover.velocity = (0.0, 0.0, 0.0)
        for _ in range(8):
            run.step({})
        face = combat_fixtures.aim(fight._eye(sombra), (x, y + 0.9, z))
        script = victim.body_script
        script.ult_seconds = 0
        if attack == 32:
            sombra.body_script.set_ult_charge(sombra.body_script.component.entity_var(338).value())
        if action == "released":
            blizzard_fixtures.BlizzardTests().launch(run, victim)
        if action == "windup":
            script.set_ult_charge(script.component.entity_var(338).value())
        start = 15 if attack == 32 else 34
        for frame in range(110):
            cmds = {sombra: (*face, attack if frame < 65 else 0)}
            if frame == start and action != "released":
                cmds[victim] = (0, 9000, 32 if action == "windup" else 16)
            run.step(cmds)
            if frame == start and action == "windup":
                assert script.component.entity_var(342).value() == 0
            if frame == start and action == "preview":
                assert fight.walls.inputs[victim.body].preview
        assert sombra_hack.active_on(script.component)
        if action == "windup":
            assert not fight.blizzard.flights and not fight.blizzard.areas
            assert script.component.entity_var(342).value() == 0
        elif action == "preview":
            assert not fight.walls.inputs[victim.body].preview
            assert not any(p.owner == victim.body for p in fight.walls.pieces.values())
        else:
            assert fight.blizzard.flights or fight.blizzard.areas

    def test_deployed_wall_blocks_lock_and_survives_blocked_cancel(self):
        for attack in (2, 32):
            for crossing in (False, True):
                with self.subTest(attack=attack, crossing=crossing):
                    run, victim, sombra, _ = combat_fixtures.MeiCombatTests().duel()
                    assert run.match.switch_hero(sombra, heroes()[sombra_hack.HERO])
                    fight = run.match.combat
                    x, y, z = victim.mover.position
                    fight.walls.place_player(sombra, (x, y, z + 4.5), ((0, 1, 0),))
                    sombra.mover.velocity = (0.0, 0.0, 0.0)
                    for _ in range(8):
                        run.step({})
                    end = (x, y, z + 2.5) if crossing else (x + 3, y, z)
                    wall_aim = combat_fixtures.aim(fight._eye(victim), end)
                    for bit in (16, 0, 1, 0):
                        run.step({victim: (*wall_aim, bit)})
                    pieces = set(fight.walls.pieces)
                    assert len(pieces) == 5, (attack, crossing, len(pieces))
                    face = combat_fixtures.aim(fight._eye(sombra), (x, y + 0.9, z))
                    if attack == 32:
                        sombra.body_script.set_ult_charge(
                            sombra.body_script.component.entity_var(338).value()
                        )
                    for _ in range(65):
                        run.step({sombra: (*face, attack)})
                    locked = sombra_hack.active_on(victim.body_script.component)
                    assert locked != crossing, (attack, crossing, locked)
                    if not crossing:
                        run.step({victim: (*wall_aim, 16)})
                        assert set(fight.walls.pieces) == pieces

    def test_projected_barrier_absorbs_emp_without_hacking_mei(self):
        for button in (2, 32):
            with self.subTest(button=button):
                fixture = shield_fixtures.ShieldTests()
                run, sombra, zarya, mei = fixture.scene()
                assert run.match.switch_hero(sombra, heroes()[sombra_hack.HERO])
                assert run.match.switch_hero(mei, heroes()[combat_fixtures.MEI])
                fight = run.match.combat
                for _ in range(24):
                    run.step({})
                bubble = fixture.cast(run, zarya, mei)
                vitals = fight.vitals_of(fixture.target(run, mei))
                health = vitals.total
                x, y, z = mei.mover.position
                face = combat_fixtures.aim(fight._eye(sombra), (x, y + 0.9, z))
                if button == 32:
                    sombra.body_script.set_ult_charge(sombra.body_script.component.entity_var(338).value())
                for _ in range(55):
                    run.step({sombra: (*face, button)})
                assert not sombra_hack.active_on(mei.body_script.component)

                assert vitals.total == health
                if button == 2:
                    assert fight.shields.shielded(mei) is bubble and bubble.health == 200
                else:
                    assert fight.shields.shielded(mei) is None and bubble.health == 0
                    assert not any(
                        i.graph.index in (sombra_hack.EMP_TARGET, sombra_hack.TARGET)
                        for i in mei.body_script.component.instances.values()
                    )
                    run.step({sombra: (*face, 0)})
                    for _ in range(55):
                        run.step({sombra: (*face, 2)})
                    assert sombra_hack.active_on(mei.body_script.component)


    def test_cryo_blocks_pending_hack_and_emp_until_exit(self):
        for button in (2, 32):
            with self.subTest(button=button):
                run, victim, sombra, _ = combat_fixtures.MeiCombatTests().duel()
                self.assertTrue(run.match.switch_hero(sombra, heroes()[sombra_hack.HERO]))
                fight = run.match.combat
                x, y, z = victim.mover.position
                fight.walls.place_player(sombra, (x, y, z + 4), ((0, 1, 0),))
                sombra.mover.velocity = (0.0, 0.0, 0.0)
                for _ in range(8):
                    run.step({})
                face = combat_fixtures.aim(fight._eye(sombra), (x, y + 0.9, z))
                target = victim.body_script.component
                caster = sombra.body_script.component
                if button == 32:
                    sombra.body_script.ult_seconds = 0
                    sombra.body_script.set_ult_charge(caster.entity_var(338).value())
                else:
                    for _ in range(14):
                        run.step({sombra: (*face, button)})
                    weapon = next(i for i in caster.instances.values()
                                  if i.graph.index == sombra_hack.WEAPON)
                    tracker = weapon.graph.by_rid(sombra_hack.LOCKED_TRACKER)
                    self.assertTrue(weapon.states[tracker.state].active)
                    self.assertFalse(sombra_hack.active_on(target))

                run.step({victim: (0, 0, 8), sombra: (*face, button)})
                for _ in range(55):
                    self.assertTrue(mei.protected(victim))
                    self.assertFalse(sombra_hack.active_on(target))
                    self.assertFalse(any(i.graph.index in (sombra_hack.TARGET, sombra_hack.EMP_TARGET)
                                         for i in target.instances.values()))
                    run.step({sombra: (*face, button)})
                if button == 32:
                    self.assertEqual(caster.entity_var(342).value(), 0)

                run.step({victim: (0, 0, 0), sombra: (*face, 0)})
                run.step({victim: (0, 0, 2), sombra: (*face, 0)})
                self.assertFalse(mei.protected(victim))
                for _ in range(80):
                    run.step({sombra: (*face, 2)})
                    if sombra_hack.active_on(target):
                        break
                self.assertTrue(sombra_hack.active_on(target))

    def test_freeze_interrupts_hack_until_right_click_is_released(self):
        for command_gap in (False, True):
            with self.subTest(command_gap=command_gap):
                self.check_interrupted_hack(command_gap)

    def check_interrupted_hack(self, command_gap):
        run, victim, sombra, _ = combat_fixtures.MeiCombatTests().duel()
        self.assertTrue(run.match.switch_hero(sombra, heroes()[sombra_hack.HERO]))
        fight = run.match.combat
        x, y, z = victim.mover.position
        fight.walls.place_player(sombra, (x, y, z + 4), ((0, 1, 0),))
        sombra.mover.velocity = (0.0, 0.0, 0.0)
        for _ in range(8):
            run.step({})
        sx, sy, sz = sombra.mover.position
        spray = combat_fixtures.aim(fight._eye(victim), (sx, sy + 0.9, sz))
        hack = combat_fixtures.aim(fight._eye(sombra), (x, y + 0.9, z))
        target = victim.body_script.component
        weapon = next(i for i in sombra.body_script.component.instances.values()
                      if i.graph.index == sombra_hack.WEAPON)
        tracker = weapon.graph.by_rid(sombra_hack.LOCKED_TRACKER)
        for _ in range(82):
            run.step({victim: (*spray, 1)})
        for _ in range(24):
            pending = weapon.states.get(tracker.state)
            was_channeling = pending is not None and pending.active
            run.step({victim: (*spray, 1), sombra: (*hack, 2)})
            if fight.frozen(sombra):
                break
        self.assertTrue(fight.frozen(sombra))
        self.assertTrue(was_channeling)
        self.assertFalse(sombra_hack.active_on(target))

        if command_gap:
            for _ in range(100):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
        for _ in range(165):
            run.step({victim: (*spray, 0), sombra: (*hack, 2)})
            self.assertFalse(sombra_hack.active_on(target))
            self.assertFalse(any(i.graph.index == sombra_hack.TARGET for i in target.instances.values()))
        self.assertFalse(fight.frozen(sombra))
        self.assertFalse(weapon.states[tracker.state].active)

        run.step({sombra: (*hack, 0)})
        for _ in range(80):
            run.step({sombra: (*hack, 2)})
            if sombra_hack.active_on(target):
                break
        self.assertTrue(sombra_hack.active_on(target))
