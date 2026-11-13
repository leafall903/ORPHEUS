"""Real Whip input selects original vector and enters the shared receiver once."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_brigitte as fixtures
from test_game_combat import aim

from ow174.game import bots, brigitte_force, defenses
from ow174.game.content import heroes
from ow174.game.knockback import Knockbacks
from ow174.game.mover import IN_AIR
from ow174.game.script import expr


class WhipForceTests(unittest.TestCase):
    def evidence(self):
        return json.loads((Path(__file__).resolve().parents[1] /
                           "fixtures/behavior/brigitte-whip-force-v2.json").read_text())

    def test_reinhardt_live_response_is_read_and_applied_once(self):
        case = fixtures.BrigitteTests()
        run, brig = case.fixture()
        enemy = run.players[1]
        run.match.switch_hero(enemy, heroes()[0x02E0000000000007])
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(defenses.knockback_scale(enemy), .7)
        x, y, z = brig.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 8], None
        host = run.match.combat.knockbacks
        with (
            patch.object(host, "apply", wraps=host.apply) as submit,
            patch.object(defenses, "knockback_scale", wraps=defenses.knockback_scale) as response,
        ):
            case.whip(run, brig, 45)
        self.assertEqual(response.call_args_list, [unittest.mock.call(enemy)])
        self.assertEqual(submit.call_count, 1)
        impulse = submit.call_args.args[1]
        for actual, expected in zip(impulse, (0, 2.1, 17.5), strict=True):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_ground_npc_uses_real_bot_flags_and_the_same_receiver(self):
        case = fixtures.BrigitteTests()
        run, brig = case.fixture(("Brigitte",))
        x, y, z = brig.mover.position
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": "0x02E000000000016B", "team": 1 - brig.team, "position": (x, y, z + 8),
            "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Whip physical NPC", "body": "0x0400000000001403", "health": 200}, {})
        run.match.bots.append(bot)
        fight = run.match.combat
        events = []
        original = Knockbacks.apply

        def apply(receiver, target, impulse, **kwargs):
            if target.owner is bot:
                flags = run.match.script_movement_flags(bot.entity, brig.body)
                self.assertEqual(flags, bot.movement_flags)
                self.assertFalse(flags & IN_AIR)
                events.append(impulse)
            return original(receiver, target, impulse, **kwargs)

        with patch.object(Knockbacks, "apply", apply):
            case.whip(run, brig, 35)
        self.assertEqual(events, [expr.Vec3(0, 3, 25)])
        self.assertEqual(fight.knockbacks.serial, 1)
        self.assertEqual(fight.vitals_of(case.target(run, bot)).total, 130)

    def test_actual_contact_uses_explicit_vector_and_one_canonical_submission(self):
        for elevated in (False, True):
            with self.subTest(elevated=elevated):
                run, brig = fixtures.BrigitteTests().fixture()
                enemy = run.players[1]
                x, y, z = brig.mover.position
                enemy.mover.position, enemy.mover.body = [x, y + (6 if elevated else 0), z + 8], None
                if elevated:
                    enemy.mover.velocity = (0, 3, 0)
                fight = run.match.combat
                yaw, pitch = aim(fight._eye(brig), (x, y + (7 if elevated else 1.6), z + 8))
                events = []
                original = Knockbacks.apply

                def apply(receiver, target, impulse, *, enemy=enemy, brig=brig, run=run,
                          events=events, original=original, **kwargs):
                    if target.owner is enemy:
                        instance = next(i for i in brig.body_script.component.instances.values()
                                        if i.graph.index == 0x1259)
                        current = tuple(enemy.mover.velocity)
                        flags = run.match.script_movement_flags(target.entity, brig.body)
                        direction = tuple(instance.vars[433].value())
                        events.append((impulse, kwargs, current, flags, direction))
                    return original(receiver, target, impulse, **kwargs)

                with patch.object(Knockbacks, "apply", apply):
                    for offset in range(1, 45):
                        run.step({brig: (yaw, pitch, 8 if offset == 1 else 0)})
                self.assertEqual(len(events), 1)
                impulse, timing, current, flags, direction = events[0]
                self.assertEqual(bool(flags & IN_AIR), elevated)
                evidence = self.evidence()["elevated" if elevated else "ground"]
                expected = evidence["pre_profile_delta"]
                self.assertEqual(bool(flags & self.evidence()["air_mask"]), elevated)
                if elevated:
                    self.assertAlmostEqual(direction[1], .5608997344970703)
                    self.assertAlmostEqual(current[1], -2.59765625)
                for actual, wanted in zip(impulse, expected, strict=True):
                    self.assertAlmostEqual(actual, wanted, places=4)
                self.assertEqual(timing, {"impact_tick": evidence["impact_tick"],
                                          "source_entity": brig.body})
                self.assertEqual(fight.knockbacks.serial, 1)
                self.assertEqual(fight.vitals_of(fixtures.BrigitteTests().target(run, enemy)).total, 130)
                self.assertFalse(fight.brigitte.whips)

    def test_minimum_is_not_a_fixed_upward_impulse(self):
        spec = brigitte_force.WhipSpec(expr.Vec3(0, -4, 20), 25, 0, 3,
                                      brigitte_force.FLOAT_MAX, ((999, 1, 1),))
        self.assertEqual(spec.impulse((0, -5, 0)), expr.Vec3(0, 8, 20))
        self.assertEqual(spec.impulse((0, 10, 0)), expr.Vec3(0, -4, 20))


if __name__ == "__main__":
    unittest.main()
