"""Career stats from played matches: counted per hero, kept in the profile, shown by the career profile."""

import threading
import unittest
from types import SimpleNamespace

from test_game_combat import Run

from ow174.accounts.profile import Profile
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content import Content
from ow174.content.career import ALL_HEROES, HERO_STAT, MAIN_HERO_HOURS, TIME_PLAYED_STAT, count_match
from ow174.content.identity import Identity
from ow174.game import combat, stats
from ow174.game.content import SOLDIER, heroes
from ow174.jam.codec import Schemas
from ow174.lobby.server import LobbyServer

ELIMINATIONS, DEATHS, HERO_DAMAGE = (
    HERO_STAT[stat] for stat in (stats.ELIMINATIONS, stats.DEATHS, stats.HERO_DAMAGE)
)


class CountingTests(unittest.TestCase):
    def test_a_match_splits_the_board_numbers_and_the_time_by_hero(self):
        run = Run()
        player = run.players[0]
        other = next(hero for guid, hero in heroes().items() if guid != SOLDIER)
        player.stats.add(stats.ELIMINATIONS, 2)
        player.stats.add(stats.HERO_DAMAGE, 350)
        for _ in range(100):
            run.step({})
        self.assertTrue(run.match.switch_hero(player, other))
        self.assertEqual(player.career, {SOLDIER: {
            stats.ELIMINATIONS: 2.0, stats.HERO_DAMAGE: 350.0, stats.TIME_PLAYED: 100 * combat.TICK,
        }})
        player.stats.add(stats.DEATHS)
        for _ in range(50):
            run.step({})
        run.match.leave(player)
        self.assertEqual(player.career[other.guid], {stats.DEATHS: 1.0, stats.TIME_PLAYED: 50 * combat.TICK})
        self.assertEqual(player.career[SOLDIER][stats.ELIMINATIONS], 2.0)

    def test_a_player_who_never_picked_has_no_career(self):
        run = Run()
        player = run.players[0]
        player.has_body = False
        run.match.leave(player)
        self.assertEqual(player.career, {})


class ProfileTests(unittest.TestCase):
    def test_matches_add_up_per_hero_and_unknown_stats_are_left_out(self):
        profile = Profile()
        played = {SOLDIER: {stats.ELIMINATIONS: 3.0, stats.TIME_PLAYED: 60.0, stats.HEALING: 99.0}}
        self.assertTrue(count_match(profile, played))
        self.assertTrue(count_match(profile, played))
        self.assertEqual(profile.career, {f"0x{SOLDIER:016X}": {
            f"0x{stats.ELIMINATIONS:016X}": 6.0, f"0x{stats.TIME_PLAYED:016X}": 120.0,
        }})

    def test_a_match_with_nothing_counted_changes_nothing(self):
        profile = Profile()
        self.assertFalse(count_match(profile, {SOLDIER: {stats.HEALING: 5.0}, 0x02E0000000000002: {}}))
        self.assertEqual(profile.career, {})

    def test_the_career_survives_a_save(self):
        profile = Profile()
        count_match(profile, {SOLDIER: {stats.DEATHS: 1.0}})
        self.assertEqual(Profile.from_dict(profile.to_dict()).career, profile.career)


class ReplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = Schemas()
        cls.content = Content(cls.schemas, RetailTemplates(), ItemDB())

    def all_modes(self, profile: Profile) -> dict[int, dict[int, float]]:
        category = next(item for item in self.content.career.stats(profile) if item["+0x18"] == 0)
        return {
            entry["+0x18"]: {stat["+0x0"]: stat["+0x8"] for stat in entry["+0x0"]}
            for entry in category["+0x0"]
        }

    def test_a_profile_without_a_match_keeps_the_showcase(self):
        shown = self.all_modes(Profile(player_name="Mei"))
        self.assertIn(MAIN_HERO_HOURS * 3600, [stats_.get(TIME_PLAYED_STAT) for stats_ in shown.values()])
        self.assertNotIn(ALL_HEROES, shown)

    def test_played_matches_replace_the_showcase(self):
        profile = Profile(player_name="Mei")
        reaper = 0x02E0000000000002
        count_match(profile, {
            SOLDIER: {
                stats.TIME_PLAYED: 600.0, stats.ELIMINATIONS: 7.0, stats.DEATHS: 2.0,
                stats.HERO_DAMAGE: 2100.0,
            },
            reaper: {stats.TIME_PLAYED: 120.0, stats.ELIMINATIONS: 1.0},
        })
        shown = self.all_modes(profile)
        self.assertEqual(
            shown[SOLDIER], {TIME_PLAYED_STAT: 600.0, ELIMINATIONS: 7.0, DEATHS: 2.0, HERO_DAMAGE: 2100.0},
        )
        self.assertEqual(shown[reaper], {TIME_PLAYED_STAT: 120.0, ELIMINATIONS: 1.0})
        self.assertEqual(shown[ALL_HEROES], {
            stats.TIME_PLAYED: 720.0, stats.ELIMINATIONS: 8.0, stats.DEATHS: 2.0, stats.HERO_DAMAGE: 2100.0,
        })
        self.assertEqual(set(shown), {SOLDIER, reaper, ALL_HEROES})

    def test_the_stat_catalogue_lists_every_stat_sent(self):
        # The client shows a stat only when 27202's catalogue (+0x90) lists it for the hero.
        listed = self.content.career.mode_rules(Profile())["+0x90"]
        catalogue = {entry["+0x0"]: set(entry["+0x8"]) for entry in listed}
        self.assertLessEqual(set(HERO_STAT.values()), catalogue[SOLDIER])
        self.assertLessEqual(set(HERO_STAT), catalogue[ALL_HEROES])

    def test_the_profile_with_a_career_encodes(self):
        profile = Profile()
        count_match(profile, {SOLDIER: {stats.TIME_PLAYED: 60.0, stats.ELIMINATIONS: 1.0}})
        identity = Identity.for_account(0x10000003)
        (crc, msg_id, value), _summary = self.content.career.profile(profile, identity, {"+0x0": 0x10000003})
        self.schemas.encode(crc, msg_id, value)


class LobbyTests(unittest.TestCase):
    def leave(self, card: int) -> tuple[Profile, list]:
        saved = []
        account = SimpleNamespace(profile=Profile(), save=lambda: saved.append(True))
        lobby = SimpleNamespace(
            session_of=lambda account_lo: None,
            state_lock=threading.RLock(),
            match_lobbies={},
            accounts=SimpleNamespace(by_id={1: account}.get),
            retry_matchmaking=lambda card: None,
        )
        match = SimpleNamespace(id=(1, 1), ranked=False, card=card)
        player = SimpleNamespace(account_lo=1, match=match, career={SOLDIER: {stats.ELIMINATIONS: 4.0}})
        LobbyServer.game_left(lobby, player)
        return account.profile, saved

    def test_a_queue_cards_match_is_saved_to_the_profile(self):
        profile, saved = self.leave(card=0x0630000000000034)
        self.assertEqual(profile.career, {f"0x{SOLDIER:016X}": {f"0x{stats.ELIMINATIONS:016X}": 4.0}})
        self.assertEqual(saved, [True])

    def test_the_practice_range_counts_nothing(self):
        profile, saved = self.leave(card=0)
        self.assertEqual(profile.career, {})
        self.assertEqual(saved, [])


if __name__ == "__main__":
    unittest.main()
