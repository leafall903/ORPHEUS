"""A match: its players, their entities, and what each player's client is told.

How a player gets into the world:
- When the map is loaded the client sends 21601 (and later 21602). 0.3 s later the server sends
  20302 {the player entity}: "this is your entity" (0x7FF7896E6C10 writes world +0x298). It has to
  come before the player's create, or the client does not add its command components (59, 63).
- At least 0.6 s after loading and after acknowledgement of the ownership message and its predecessors,
  one frame creates the map's server-owned placeables (the loading screen only
  ends once they all exist), the player entity (000E.003, flags 0x3C) and the game mode entity
  (16B2.003).
- Once its create is acknowledged, the player's entity gets its controller's statescript (heroselect.py)
  for the HUD and the hero select screen. That frame puts the entity's statescript in owner mode,
  so small owner frames keep telling the client which of its commands the server has (CmFD), else it
  replays old key presses.
- The hero's body comes with the pick, as in retail (OW2 traffic): the body with its movement state
  and an update of the player entity whose component 44 possesses it. A switch replaces the body
  under a new id in the same frame (the client ignores a destroyed id for 30 s).
- The client then moves the body itself; it never tells the server where it is. The server moves
  a copy from the commands, and that copy is what the other players see.
- In the PvP modes the first spawn starts "assemble your team": 30 s shown on the hero select
  screen, which stays open after a pick and closes when the time is up.
- Every client also gets the other players' entities, as in retail (their team lists, B_team_list
  research): each one's card (20308: name, icon, level), his entity with his id, and a plain frame
  with the team list entry 288D and his pick. 288D leaves enemies out of the list on the client.
  The entity also names his portrait frame (component 74), which makes the client load it: the list
  draws a frame only when it is loaded. On a competitive card the match is a ranked one (20300) and
  the entity carries his rank (component 29), which the list shows in place of level and frame.
"""

import logging
import math
from collections import deque
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, replace

from ow174.game import (
    bodytrace,
    bots,
    combat,
    correction,
    cryo,
    earthshatter,
    echo_movement,
    heroselect,
    ice_wall,
    moira,
    movelog,
    notices,
    orisa_charge,
    pools,
    sigma_barrier,
    stats,
    world,
    wraith,
)
from ow174.game.collision import (
    MOVER,
    TEAM0,
    TEAM1,
    CollisionProvider,
    LoadState,
    World,
    default_collision_provider,
)
from ow174.game.commands import CROUCH, JUMP, Command, CommandQueue, InputError
from ow174.game.content import (
    JUNKENSTEIN_MAP,
    JUNKENSTEIN_MODES,
    JUNKENSTEIN_THREE_THEY_WERE,
    PRACTICE_RANGE,
    SOLDIER,
    GameMap,
    Hero,
    heroes,
    spawn_point,
)
from ow174.game.dynamic_spheres import DynamicSpheres
from ow174.game.environment import ExecutionEnvironment, SystemEnvironment
from ow174.game.genji import GENJI, Mobility
from ow174.game.mover import MoverData, clamp_linked_target_command, statescript_flight, statescript_mover_data
from ow174.game.mover import CROUCHED, GRAVITY_SCALE, Mover, defined_mover_data, mover_data, statescript_mods
from ow174.game.projectiles import MAX_LEASE_FRAME
from ow174.game.script.player_controller import ControllerVM
from ow174.game.script import expr, graph, nodes, runtime, scene_host, spray_placement
from ow174.game.script.bot import BotScript
from ow174.game.script.driver import MAX_GAP, WHEEL_ACTION_CATEGORIES, WHEEL_ACTION_PREFIX, BodyScript, CommandEpochError
from ow174.game.script.runtime import ScriptBudgetError
from ow174.game.statescript import Stream, owner_ack, owner_full_frame
from ow174.game.world import (
    GAME_MODE_ENTITY,
    NO_INPUT,
    OP_CREATE,
    OP_DESTROY,
    OP_UPDATE,
    PLAYER_PATH,
    WITH_PARTICIPANT,
    WITH_SCALE,
    WITH_TRANSFORM,
    EntityUpdate,
    Movement,
)
from ow174.jam.values import id16

@dataclass(frozen=True)
class RetainedFlight:
    through: int
    instance: int
    expires_ms: int
    profile: tuple[float, MoverData | None] | None


log = logging.getLogger("ow174.game")

PLAYER_DEFINITION = 0x040000000000000E  # 000E.003, the player (controller) entity
GAME_MODE_DEFINITION = 0x04000000000016B2
PLAYER_FLAGS = WITH_PARTICIPANT | PLAYER_PATH | WITH_TRANSFORM | WITH_SCALE
BODY_FLAGS = WITH_TRANSFORM | WITH_SCALE

# Component 26, the filter bits: 0x800000 << team picks the team's physics proxy (team 0 or 1). The
# client's own session builder gives its player 0x10040001 | that bit (0x7FF78973950E), but
# 0x10000000 is the spectator bit (GetEntityIsSpectator, 0x7FF789B2F000): a player with it
# gets the spectator's HUD panel, and a body with only it has no collision and no enemy
# outline. So the player gets 0x00040001 | the team bit, a body only the team bit.
TEAM_BIT = 0x800000
# The client knows five teams, bits 23-27 of the filter (GetTeam 0x7FF7894E9570); team 4 is the
# free-for-all one, whose members are all enemies (0x7FF789B2C690). Bit 28 is the spectator.
FREE_FOR_ALL_TEAM = 4
PLAYER_FILTER = 0x00040001
# The body statescript's health (its HUD number): None = the hero's own pools added up (pools.py).
HEALTH: float | None = None
# The heroes whose body statescript the server runs (ow174/game/script): their weapons and
# abilities work and the client keeps its predictions. None = every hero.
BODY_SCRIPT_HEROES: set[int] | None = None
MAX_BODY_MESSAGES = 256  # bound the match's deferred cross-body work per tick

# ClientInGame 20308 {4 flags, card}: a player's card for the client's player list, keyed by the id in
# his entity's component 29 (0x7FF7896E88B0). With the first and fourth flag on it also posts "<name>
# joined the game." (or left, or started spectating) in the chat.
PLAYER_CARD = 20308
NEW_PLAYERS_PER_TICK = 3  # a card is about 90 bytes, and a datagram's payload at most 1230
# The team list draws a portrait frame only when the frame's asset is loaded (0x7FF78A64BA30), and the
# client loads it when a player entity's component 74 names it (0x7FF7894C9D90). A team list entry is
# filled again whenever a card arrives (UI-world event 0x47 of the upsert), so each card goes once more a
# little later, when the frames have loaded.
CARD_AGAIN_TICKS = 125  # 2 s of 16 ms ticks
# Beside the 20308 fields, the lobby's card may carry the player's rank on a competitive card:
# (skill rating, Top 500 place, tier). It goes in component 29, and makes the match a ranked one.
CARD_RANK = "rank"
# On a role queue card it also carries the role the matchmaker gave the player. Component 74's
# m_roleQueueRole then makes hero select refuse heroes of the other roles (CanPlayHero 0x7FF789CB0F92,
# while the global 0x7FF78BD4FB90 is on, which it is from the start).
CARD_ROLE = "role"
ROLE_NUMBERS = {"Damage": 1, "Tank": 2, "Support": 3}  # a hero's role -> m_roleQueueRole
# 20300 +0x78, the instance type (world system 0x38 +0x18). With 9 the team list shows each
# player's rank (component 29) in place of his level and frame (0x7FF789A15CC9, 0x7FF789A1D8E0).
UNRANKED, RANKED = 1, 9

RETAIL_QUANTA = 16000  # microseconds per command frame; 20300 must name the client's own value
TOURNAMENT_QUANTA = 7000  # tank_TournamentModeTickRate, which tournament mode turns on
# Ticks between the datagrams to each client: retail sent one every third tick, 48 ms (OW2 traffic). The
# statescript frames that go in them are built on those ticks only; bodies and scripts still run every tick.
SEND_EVERY = 3


def sends(tick: int) -> bool:
    """Whether this tick sends the clients a datagram."""
    return tick % SEND_EVERY == 0


# Seconds after the client reports its map loaded.
SEND_OWN_ENTITY = 0.3
SEND_GAME = 0.4
SEND_WORLD = 0.6
SEND_CONTROLLER = 0.7  # a frame after the entity exists
OWNER_ACK_EVERY = 0.1  # seconds between owner frames that only move CmFD on

# "Assemble your team" in the PvP modes: their mode graphs (0F4A, 0EC2) set the game mode entity's
# v1303 to 30 and count it down; 288B shows it on the hero select screen as long as it is open.
ASSEMBLE_SECONDS = 30
COUNTDOWN = 1303
COUNTDOWN_EVERY = 1.0  # the screen shows whole seconds
STATS_EVERY = 0.5  # seconds between updates of a player's own board numbers (component 64, stats.py)

# The bots' creates span several datagrams. Give them this many ticks before adding movement records,
# and wait for each create's ACK separately.
BOT_RECORD_DELAY = 8


def _yaw_units(degrees: float) -> int:
    return round(degrees * 65536 / 360)


def _rotation(yaw_units: int) -> tuple[float, float, float, float]:
    half = yaw_units * math.pi / 65536
    return (0.0, math.sin(half), 0.0, math.cos(half))


class ShownController:
    """Another player's controller entity as one client has it."""

    def __init__(self, entity: int, created: int) -> None:
        self.script = Stream(entity)  # its statescript on that client: plain (H=0) full frames
        self.create_acked = False
        self.pick: int | None = None  # the hero its last frame carried
        self.card_again: int | None = created + CARD_AGAIN_TICKS  # when its card goes once more
        self.possession: tuple | None = None  # the (body, hero, skin theme) its last 44 and 75 named

    def arrived(self, last: int) -> None:
        self.create_acked = True


class Player:
    def __init__(
        self,
        match: "Match",
        slot: int,
        account_lo: int,
        name: str,
        hero: Hero,
        team: int,
        quanta: int,
        card: dict | None = None,
    ):
        self.match = match
        self.slot = slot
        self.account_lo = account_lo
        self.name = name
        # The player's card (20308) as the lobby shows it: account id, icon, portrait frame, level and
        # BattleTag. Without one from the lobby, only the ids and the name.
        card = dict(card or {"+0x0": id16(account_lo), "+0x10": id16(account_lo), "+0x40": name})
        # (skill rating, Top 500 place, tier) on a competitive card, else None
        self.rank: tuple[int, int, int] | None = card.pop(CARD_RANK, None)
        self.role: int = card.pop(CARD_ROLE, 0)  # ROLE_NUMBERS; 0 = any hero
        self.card = card
        self.card_sent = False  # the player's client has its own card
        self.card_again: int | None = None  # when its own card goes to it once more
        self.hero = hero
        self.team = team
        self.quanta = quanta
        self.entity = 0xA0000000 | (slot + 1) << 8
        self.body = self.entity | 1
        self.has_body = False  # not before the first pick
        self.skin = (0, False)  # the body's (skin theme, golden weapon)
        self.client = None  # the connected client (server.Client), None before it joins or after it left
        self.gone = False  # left the match, or its game never connected
        self.loaded_at: float | None = None
        self.steps_done = 0
        self.spawned = False
        self.overload_cooldown_until = 0.0
        self.turret_cooldown_until = 0.0
        # The n-th player of the team takes the team's n-th spawn point; in a free-for-all everyone is
        # on a team of his own, so the n-th player of the match.
        if match.game_map.free_for_all:
            number = len(match.players)
        else:
            number = sum(1 for other in match.players if other.team == team)
        self.spawn = spawn_point(match.game_map, team, number)  # (position, yaw in degrees)
        self.mover = self.new_mover()
        self.commands = CommandQueue()
        self.impulse_correction: tuple[int, int] | None = None
        self.body_script: BodyScript | None = None  # the body's statescript, run from the commands
        self.controlled_mech: bots.Bot | None = None
        self.body_script_failed = False  # a hero reselect can replace this body's failed script
        self.retained_ult_charge: float | None = None
        self.genji_mobility = Mobility()
        # Other players' bodies this player's client has: body -> False in the frame of its create.
        self.seen: dict[int, bool] = {}
        self.shown: dict[int, ShownController] = {}  # other players' controller entities on this client
        self.script = Stream(self.entity)  # the player entity's statescript: the controller
        self.mode_script = Stream(GAME_MODE_ENTITY)  # the game mode entity's variables: the countdown
        self.next_countdown = 0.0
        self.select_open = True
        self.controller_vm: ControllerVM | None = None
        self.controller_effect_key = None
        self.controller_hero: int | None = None  # the hero (v940) the last controller frame named
        # The numbers his Tab board shows (stats.py); the combat code adds to them. His client gets them
        # in component 64 when they change.
        self.stats = stats.PlayerStats()
        self.stats_sent: list | None = None
        self.next_stats = 0.0

    @property
    def team_bit(self) -> int:
        """The filter bit of the player's team; in a free-for-all the free-for-all team's, since each
        player's own team number would run past the five teams into the spectator bit."""
        if self.match.game_map.free_for_all:
            return TEAM_BIT << FREE_FOR_ALL_TEAM
        return TEAM_BIT << self.team

    @property
    def controller_filter_bits(self) -> int:
        return PLAYER_FILTER | self.team_bit

    @property
    def player_id(self) -> tuple[int, int]:
        """The 16-byte id of the player's entity (component 29) as (low, high): his card's key. The
        lobby's account id, which a client also holds for itself (20500): its own entity is then the
        local player in the team list."""
        account = self.card["+0x0"]
        return account["+0x0"], account["+0x8"]

    def movement(self) -> Movement:
        """The body's movement state for other players' clients, as the client's own mover writes it: their
        animation plays idle, run, crouch or a jump from it. A movement key counts as held while the last
        command with throttles is less than 0.1 s old (0x7FF789B20A70), so a standing body carries the
        tick of that command. Ticks are the server's, like the records' frames."""
        mover = self.mover
        tick = mover.input_tick
        # The record's frame is the match's tick: that frame must go as the default, or the client would
        # not read +34/+35.
        if mover.throttles != (0, 0) or (tick is not None and tick >= self.match.tick):
            input_frame = None  # moving now: the state's own frame
        else:
            input_frame = NO_INPUT if tick is None else tick
        return Movement(
            mover.position,
            mover.yaw,
            mover.pitch,
            tuple(a + b for a, b in zip(mover.velocity,
                  self.match.combat.knockbacks.whole_hog_velocity(self.body), strict=True)),
            mover.flags | (combat.DEAD if self.match.combat.is_dead(self) else 0),
            throttles=mover.throttles,
            input_frame=input_frame,
            input_throttles=mover.input_throttles,
            air_ticks=mover.air_ticks,
            gravity=GRAVITY_SCALE,
        )

    def pose(self) -> Movement:
        """The own body's state in its create and first record: only the pose and the crouch, as
        always. The client moves its own body itself."""
        mover = self.mover
        flags = CROUCHED if mover.crouched else 0
        return Movement(mover.position, mover.yaw, mover.pitch, mover.velocity, flags)

    def take_commands(self, commands: list[Command], *, not_after: int | None = None) -> int | None:
        """Simulate the encodable command prefix and report the first frame needing a new link."""
        collision = self.match.collision_world
        if collision is None:
            return None
        seconds = self.quanta / 1e6
        script = self.body_script
        horizon = None
        if script is not None and script.hero == 0x02E0000000000009 and script.world_frame_anchor is not None:
            anchor_tick, anchor_frame = script.world_frame_anchor
            elapsed = max(0, self.match.tick - anchor_tick) * (RETAIL_QUANTA // 1000)
            horizon = min(MAX_LEASE_FRAME, anchor_frame + elapsed // script.frame_ms)
        if not_after is not None:
            if type(not_after) is not int or not 0 <= not_after <= 0xFFFFFFFF:
                raise InputError("command horizon must be uint32")
            horizon = not_after if horizon is None else min(horizon, not_after)
        fresh, boundary = self.commands.before_limit(
            commands, MAX_LEASE_FRAME, anchor=script.frame if script is not None else None, horizon=horizon,
        )
        if script is not None and script.world_frame_anchor is not None:
            fresh = [item for item in fresh if item.frame > script.frame]
        self.match.combat.hooks.observe_input(self, fresh)
        if fresh:
            data = echo_movement.effective_data(self)
            self.mover.use(data if data != self.mover.data else None, collision)
        if movelog.ENABLED:
            movelog.commands(self, fresh, received=commands)
        for command in fresh:
            command = self.match.combat.hooks.input(self, command)
            if self.has_body:
                if self.match.combat.junkrat.tire.command(self, command, self.match.tick):
                    neutral = Command(command.frame, yaw=self.mover.yaw, pitch=self.mover.pitch)
                    if not self.match.combat.is_dead(self):
                        if self.mover.airborne or (not self.mover.driven and any(self.mover.velocity)):
                            self._advance_body_gap(self.mover.first_step_frame(neutral.frame))
                            self.mover.step(neutral, seconds, self.match.tick,
                                            simulate_pose=self._step_tire_operator_pose)
                            continue
                        self.mover.last_command = neutral
                    self._advance_body_frame(neutral, posed=False)
                    continue
                if self.controlled_mech is not None:
                    from ow174.game import body_entities, npc

                    try:
                        if body_entities.drive_mech(self, command, seconds):
                            continue
                    except Exception:
                        log.exception("[game] %s: %s's controlled mech failed", self.match.label(), self.name)
                        mech = self.controlled_mech
                        body_entities.release_control(self)
                        if mech is not None:
                            npc.remove_body(self.match, mech)
                        continue
                dead = self.match.combat.is_dead(self)
                first_pose = command.frame if dead else self.mover.first_step_frame(command.frame)
                script = self.body_script
                retained = (sigma_barrier.barrier_gap_buttons(script, command.buttons)
                            if not dead and script is not None and command.frame > script.frame + 1 else 0)
                self._advance_body_gap(first_pose, retained_buttons=retained, incoming_frame=command.frame)
                if not dead and self.controller_vm is not None:
                    angles = self.controller_vm.linked_target_angles()
                    if angles is not None:
                        command = clamp_linked_target_command(command, angles)
                aim_lock = getattr(self.match.scene_host, 'player_aim_lock', None)
                if not dead and aim_lock is not None and (locked := aim_lock(self)) is not None:
                    yaw, pitch = locked
                    command = replace(command, yaw=yaw, pitch=pitch, click_yaw=None, click_pitch=None)
                hook_victim = getattr(self.match.scene_host, 'hook_victim', None)
                hooked = hook_victim is not None and hook_victim(self) is not None
                if not dead and (self.body_script is not None or hooked):
                    self.mover.step(
                        command, seconds, self.match.tick,
                        simulate_pose=(lambda posed, seconds, tick, endpoint=command.frame:
                            self._step_body_pose(posed, seconds, tick,
                                healing_tick=tick - (endpoint - posed.frame)))
                            if self.hero.guid == 0x02E0000000000040 else self._step_body_pose,
                        retained_buttons=retained,
                    )
                else:
                    if not dead:  # a dead body lies where it fell
                        self.mover.step(command, seconds, self.match.tick, simulate_pose=self._move_pose)
                    self._advance_body_frame(command)
                self.match.combat.pulse_bombs.observe_command_pose(self, command.frame, first_pose == command.frame)
                self.match.combat.observe_dragon_pose(self, command.frame, first_pose == command.frame)
                script = self.body_script
                if script is not None and script.hero == SOLDIER and script.frame == command.frame:
                    from ow174.game import defenses, ultimate

                    if (defenses.soldier_input_recovery_active(self) or defenses.soldier_visor_running(self)
                            or defenses.soldier_field_windup(self) or ultimate.practice_passive_clock(script)):
                        script.world_frame_anchor = (self.match.tick, script.frame)
        if movelog.ENABLED:
            movelog.snapshots(self)
        return boundary

    def _step_tire_operator_pose(self, command: Command, seconds: float, tick: int) -> None:
        neutral = Command(command.frame, yaw=self.mover.yaw, pitch=self.mover.pitch)
        self.mover.step_pose(neutral, seconds, tick)
        self._advance_body_frame(neutral, posed=False)

    def _advance_body_gap(self, first_pose: int, *, retained_buttons: int = 0, world_idle: bool = False, incoming_frame: int | None = None) -> None:
        """Release old input and advance timers across frames without a movement pose."""
        script = self.body_script
        if script is None or script.frame + 1 >= first_pose:
            return
        def advance(frame):
            healing_tick = (self.match.combat.tick - (incoming_frame - frame)
                            if script.hero == 0x02E0000000000040 and incoming_frame is not None else None)
            self._advance_body_frame(Command(frame, buttons=retained_buttons), posed=False,
                                     defer_effects=world_idle, healing_tick=healing_tick)
        first_missing = script.frame + 1
        advance(first_missing)
        if first_pose - first_missing > MAX_GAP:
            from ow174.game import tracer_recall

            checkpoint = cryo.exit_frame(self)
            if checkpoint is None:
                checkpoint = tracer_recall.exit_frame(self)
            if checkpoint is not None and first_missing < checkpoint < first_pose - MAX_GAP:
                advance(checkpoint)
        from ow174.game import lucio

        frame, end = script.frame, first_pose - 1
        while self.body_script is script and frame < end:
            frame = lucio.effect_gap_frame(script, frame, end)
            advance(frame)

    def _step_body_pose(self, command: Command, seconds: float, tick: int, *, replay_accepted: bool = False, healing_tick: int | None = None) -> None:
        script = self.body_script
        if script is not None and script.world_frame_anchor is not None and command.frame <= script.frame:
            return
        fight = self.match.combat
        fight.knockbacks.prepare_victim(self, command.frame)
        command = fight.punches.command_input(self, command)
        step_hook_victim = getattr(self.match.scene_host, 'step_hook_victim', None)
        if step_hook_victim is not None and step_hook_victim(self, command, seconds, tick):
            return
        stunned = self.match.combat.is_stunned(self, tick=tick)
        received = self.mover.last_command is command
        admitted, input_suppressed = fight.suppress_input(self, command, tick=tick, active=stunned)
        if received and admitted is not command:
            self.mover.last_command = admitted
        command = admitted
        stunned = stunned or input_suppressed
        frost = fight.frost_progress(self, tick=tick)
        command = fight.sombra_controls.input(self, command)
        command = fight.clamp_sleep_command(self, command)
        pulled = self.body in fight.zarya_gravitons.pulled
        if fight.recalls.locked(self):
            from ow174.game import tracer

            tracer.hold_pose(self, command)
            self._advance_body_frame(command, replay_accepted=replay_accepted, healing_tick=healing_tick)
            return
        if fight.junkrat.rooted(self.body, tick):
            # Restrain movement after the existing status and Recall admission.
            self.mover.velocity = (0.0, 0.0, 0.0)
            movement = replace(command, forward=0, right=0, buttons=command.buttons & ~JUMP)
            self.mover.step_pose(movement, seconds, tick)
            self._advance_body_frame(command, replay_accepted=replay_accepted, healing_tick=healing_tick)
            return
        if (fight.hooks.controlled(self.body) and fight.hooks.drive_allowed(self.body, self)
                and not self.mover.external_impulse
                and not combat.reinhardt_charge.pinned(fight, self.body)):
            # The sole world pull has already moved this same capsule. Input
            # advances its body VM without integrating another movement pose.
            self._advance_body_frame(command, replay_accepted=replay_accepted, healing_tick=healing_tick)
            return
        if stunned or fight.recalls.locked(self):
            command = replace(command, forward=0, right=0, buttons=0, action=0)
            impact = self.match.combat.punch_impacts.get(self.body)
            if not pulled and not self.mover.external_impulse and (impact is None or tick >= impact.deadline):
                self.mover.velocity = (0.0, self.mover.velocity[1], 0.0)
        elif pulled:
            blocked = JUMP | CROUCH
            if self.hero.guid == moira.MOIRA:
                blocked |= 8  # Fade input during the pull
            command = replace(command, forward=0, right=0, buttons=command.buttons & ~blocked)
        elif (self.body_script is not None
              and self.body_script.world.wheel_replaces_horizontal_velocity(self.body_script.component)):
            # The mover models static supports. Selector 1 preserves vertical velocity.
            self.mover.velocity = (0.0, self.mover.velocity[1], 0.0)
        if not stunned and not pulled and fight.air_steering_locked(self, command.frame):
            command = replace(command, forward=0, right=0)
        from ow174.game import mccree, wall_ride

        mccree.apply_velocity(self, command.frame, controlled=stunned)
        script = self.body_script
        forced = self.match.combat.orisa.movement(self)
        if forced is not None:
            self.mover.step_pose(command, seconds, tick, move_to=forced)
            self._advance_body_frame(command, replay_accepted=replay_accepted, healing_tick=healing_tick)
            return
        if (not stunned and not pulled and not fight.is_asleep(self, tick=tick)
                and self.body not in fight.sombra_controls.locks):
            from ow174.game.mover import pharah_thrust

            pharah_thrust(self, seconds)
        requests = []
        if script is not None and not stunned and not pulled and not self.mover.external_impulse:
            requests = [
                (state, movement)
                for instance in script.component.instances.values()
                for state in instance.states.values()
                if isinstance(state, nodes.MoveTo) and (movement := state.move_request()) is not None
            ]
        # Echo consumes its own authored flight mode through the same swept collision path.
        echo_request = echo_movement.request(self, command)
        data = echo_movement.effective_data(self)
        if data != self.mover.data:
            self.mover.use(data)
        if echo_request is not None and not pulled:
            self.mover.step_pose(command, seconds, tick, move_to=echo_request)
            self._advance_body_frame(command, replay_accepted=replay_accepted, healing_tick=healing_tick)
            return
        # Arbitration between simultaneous world-target states is unresolved.
        slam = None if stunned or pulled else fight.slams.movement(self)
        if slam is not None:
            requests = [request for request in requests if request[0] is not slam[0]]
            requests.append(slam)
        if self.hero.guid == GENJI:
            self.genji_mobility.before_pose(self, command, seconds, driven=bool(requests))
        movement_end = None
        punch = None if stunned or pulled else fight.punches.movement(self, command)
        retained = getattr(self, "valkyrie_pose", None)
        flight_profile = retained.profile if retained is not None and command.frame <= retained.through else None
        charge_start = tuple(self.mover.position) if self.hero.guid == 0x02E0000000000007 else None
        drive = None
        if charge_start is not None and not pulled and not stunned:
            from ow174.game import reinhardt_charge
            drive = reinhardt_charge.drive(fight, self, command, seconds)
        collision = ice_wall.movement_world(fight, tick)
        self.mover.use(world=collision)
        from ow174.game import ball, hanzo_movement

        climb = hanzo_movement.climb_request(
            self, command, seconds,
            superseded=bool(requests) or self.mover.external_impulse or stunned or pulled or punch is not None,
        )
        if pulled:
            from ow174.game import sigma_flux

            if sigma_flux.has_effect(self):
                self.mover.driven = False
                self.mover.step_pose(command, seconds, tick, horizontal_drive=(0.0, 0.0))
            else:
                self.mover.step_pose(command, seconds, tick, world_driven=True)
        elif punch is not None:
            start = tuple(self.mover.position)
            outcome = self._move_pose(command, seconds, tick, move_to=punch, flight_profile=flight_profile)
            destination, speed = punch
            distance = math.dist(start, destination)
            progress = distance - math.dist(self.mover.position, destination)
            # Delivered collision host: a skin-creeping capsule is still blocked.
            if outcome is None and progress < min(distance, speed * seconds) / 2:
                outcome = False
            movement_end = fight.punches.movement_result(self, outcome)
        elif climb is not None:
            start_y = self.mover.position[1]
            outcome = self._move_pose(command, seconds, tick, move_to=climb, flight_profile=flight_profile)
            hanzo_movement.climbed(self, start_y, outcome, command.frame)
        elif drive is not None:
            self.mover.step_pose(command, seconds, tick, horizontal_drive=drive)
        elif len(requests) == 1:
            state, (target, speed) = requests[0]
            outcome = self._move_pose(
                command, seconds, tick, move_to=((target.x, target.y, target.z), speed),
                flight_profile=flight_profile,
            )
            if outcome is not None:
                movement_end = state, outcome
        elif (self.hero.guid == 0x02E00000000001CA and not stunned
              and not self.mover.external_impulse
              and (ball_motion := ball.motion(self, seconds, command)) is not None):
            self._move_pose(command, seconds, tick, move_to=ball_motion)
        else:
            movement_limit = None
            if script is not None and not stunned and not self.mover.external_impulse:
                movement_limit = self.match.combat.brigitte.bash_movement_limit(self)
            fall_limit = echo_movement.glide_limit(self, command)
            from ow174.game import sigma_flux

            flux_flight = sigma_flux.flux_flight_data(self) if not stunned else None
            speed_scale = max(0.3, 1.0 - frost)
            if self.hero.guid == 0x02E0000000000079:
                controller = wall_ride.controller(script)
                state = controller.states.get(wall_ride.STATE) if controller is not None else None
                wall_control = None
                if not stunned and not self.mover.external_impulse and state is not None and state.active:
                    setting = script.component.entity_var(wall_ride.BACKWARDS, create=False)
                    backwards = bool(setting.value()) if setting is not None else False
                    wall_control = (state.start // script.frame_ms + 1, backwards)
                self.mover.step_pose(command, seconds, tick, speed_scale=speed_scale,
                                     movement_limit=movement_limit, wall_ride=wall_control)
            elif self.hero.guid == 0x02E0000000000007 and stunned:
                self.mover.step_pose(command, seconds, tick, suppress_throttle=True)
            elif flux_flight is not None and movement_limit is None:
                self._move_pose(command, seconds, tick, flight=flux_flight)
            elif fall_limit is not None:
                self.mover.step_pose(
                    command, seconds, tick, movement_limit=movement_limit,
                    fall_limit=fall_limit, speed_scale=speed_scale,
                )
            elif self.hero.guid == 0x02E0000000000004:
                self.mover.step_pose(command, seconds, tick, movement_limit=movement_limit,
                                     speed_scale=speed_scale, flight_profile=flight_profile)
            elif movement_limit is None:
                mccree.step_rialto_pose(self, command, seconds, tick, controlled=stunned,
                                       speed_scale=speed_scale)
            else:
                self.mover.step_pose(command, seconds, tick, movement_limit=movement_limit,
                                     speed_scale=speed_scale)
        if self.body in self.match.combat.punch_impacts:
            self.match.combat.punch_wall_contact(self, command, tick)
        self._advance_body_frame(
            command, posed=not input_suppressed, movement_end=movement_end,
            charge_start=charge_start, seconds=seconds, replay_accepted=replay_accepted, healing_tick=healing_tick,
        )
        if not self.mover.airborne and not self.mover.landed:
            fight.translocators.recall_poses.discard(self.body)

    def _move_pose(self, command, seconds, tick, *, move_to=None, flight_profile=None, flight=None):
        fight = self.match.combat
        command = fight.clamp_sleep_command(self, command)
        walls = fight.walls
        if not walls.pieces:
            return self.mover.step_pose(command, seconds, tick, move_to=move_to, flight_profile=flight_profile,
                                        flight=flight)
        before = tuple(self.mover.position)
        outcome = self.mover.step_pose(
            command, seconds, tick, move_to=move_to, ground_override=walls.ground(self.mover),
            flight_profile=flight_profile, flight=flight,
        )
        blocked = walls.move_mover(self.mover, before)
        if move_to is not None and blocked:
            self.mover.velocity = (0.0, 0.0, 0.0)
            self.mover.driven = False
            self.mover.history[-1] = self.mover.snapshot(command.frame)
            return False
        return outcome

    def _advance_body_frame(
        self, command: Command, *, posed: bool = True, movement_end: tuple[runtime.State, bool] | None = None,
        charge_start: tuple[float, float, float] | None = None, seconds: float = 0.0,
        defer_effects: bool = False, replay_accepted: bool = False, healing_tick: int | None = None,
    ) -> None:
        """Resolve body input and consume combat reports from this simulation step."""
        command = self.match.combat.punches.command_input(self, command)
        script = self.body_script
        if script is not None and command.frame <= script.frame and script.hero == moira.MOIRA:
            # A late movement pose cannot resample an ability on an already evaluated body frame.
            return
        if self.body_script is not None:
            from ow174.game import bob_victim
            bob_victim.sample(self, self.body_script.component)
            command = self.match.combat.meteors.prepare_command(self, command)
            if posed:
                self.match.combat.slams.observe(self)
                self.match.combat.prepare(self, command)
                if charge_start is not None:
                    if self.hero.guid == 0x02E0000000000007:
                        self.match.combat.charge_contact(self, command, charge_start, seconds)
                    else:
                        self.match.combat.punch_contact(self, command, charge_start)
            if posed and self.match.combat.echo_duplicates.command(self, command):
                script = self.body_script
                self.mover.track_mods(statescript_mods(script.component) if script else {}, command.frame)
                return
            grasp = (
                moira.grasp_command(self.match.combat, self, command)
                if posed and script.hero == moira.MOIRA and command.buttons & 2 else nullcontext()
            )
            from ow174.game.symmetra_portals import ABILITY, INTERACT

            pressed = command.buttons & ~script.buttons if posed else 0
            portals = self.match.combat.portals
            if pressed & ABILITY and portals.pairs:
                portals.dismiss(self)
            previous_frame = script.frame
            from ow174.game import sigma_flux

            sigma_flux.sync_flux_airborne(script.component, self.mover)
            previous_fortify = combat._active_fortify(self) if posed else None
            previous_cast = previous_fortify.counter if previous_fortify is not None else None
            with grasp:
                self.match.body_command(self, command, movement_end=movement_end, posed=posed, healing_tick=healing_tick)
                if pressed & INTERACT and self.body_script is not None and portals.pairs:
                    portals.interact(self)
                if posed and self.body_script is script and script.frame > previous_frame:
                    fortify = combat._active_fortify(self)
                    if fortify is not None:
                        if fortify.counter != previous_cast:
                            script.orisa_fortify_pending = True
                        script.orisa_pose_anchor = (self.match.tick, script.frame)
                wraith.advance(self)
                sigma_flux.release_phased_flux_target(self)
                self.match.combat.command(self, command if posed and not replay_accepted else None,
                                          defer_effects=defer_effects,
                                          replay_aim=command if replay_accepted else None)
                if self.body_script is script and script.hero == 0x02E00000000001CA:
                    from ow174.game import ball
                    ball.settle_grapple_interrupt(self.match.combat, self, command.frame)
        script = self.body_script
        if script is not None and (pending := getattr(script.world, "pending_ult_credits", ())):
            from ow174.game.script import charge

            charge.set_pending_credits(script.world, tuple(credit for credit in pending
                                                          if credit[0] > script.component.now))
        self.mover.track_mods(statescript_mods(script.component) if script else {}, command.frame, posed=posed)
        self.mover.script_data = statescript_mover_data(script.component) if script else None
        self.mover.flight_scale = statescript_flight(script.component) if script else None
        retained = getattr(self, "valkyrie_pose", None)
        if retained is not None and command.frame >= retained.through:
            self.clear_valkyrie_pose()

    def finish_valkyrie_pose(self, frame: int, instance: int, expires_ms: int) -> None:
        """Keep the expired movement and weapon inputs for older command frames."""
        mover = self.mover
        script = self.body_script
        if script.frame < frame:
            profile = (mover.flight_scale, mover.script_data) if mover.flight_scale is not None else None
            self.valkyrie_pose = RetainedFlight(frame, instance, expires_ms, profile)
            if profile is not None:
                active = statescript_mods(script.component)
                for key, mod in mover.mods.items():
                    if key[0] == instance and key not in active and mod.end is None:
                        mod.end = frame + 1
        self.mover.track_mods(statescript_mods(script.component), script.frame, posed=False)
        self.mover.script_data = statescript_mover_data(script.component)
        self.mover.flight_scale = statescript_flight(script.component)

    def clear_valkyrie_pose(self) -> None:
        retained = vars(self).pop("valkyrie_pose", None)
        if retained is not None:
            for key, mod in tuple(self.mover.mods.items()):
                if key[0] == retained.instance and mod.end == retained.through + 1:
                    del self.mover.mods[key]

    def new_body(self, entity: int) -> None:
        """Set up the allocated body at the spawn point."""
        vars(self).pop("valkyrie_pose", None)
        self.body = entity
        self.mover = self.new_mover()
        self.impulse_correction = None
        self.genji_mobility = Mobility()

    def new_mover(self) -> Mover:
        """The body's mover at the spawn point, with the hero's values and the match's pinned collision.
        It cannot run before collision is ready. It uses what stops its own team's physics
        proxy (some props stop one team only, as on Blizzard World), both in a free-for-all."""
        game_map = self.match.game_map
        world = self.match.collision_world
        category = MOVER
        if not game_map.free_for_all and self.team in (0, 1):
            category = (TEAM0, TEAM1)[self.team]
        return Mover(
            self.spawn[0], _yaw_units(self.spawn[1]), world, mover_data(self.hero.body), category=category
        )

    def describe(self) -> str:
        return f"{self.name} ({self.hero.name}, team {self.team + 1})"


def no_skin(account_lo: int, hero: int) -> tuple[int, bool]:
    return 0, False


def no_emote_aliases(account_lo: int, hero: int, *, body_definition: int | None = None) -> tuple[int, ...]:
    return ()


def no_wheel_cosmetics(account_lo: int, hero: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    return (), ()


def card_message(player: Player) -> dict:
    """20308 with the player's card. The client keeps the card unless it has one with a newer +0x30
    for the same id (0x7FF789529C90), such as a friend's from the lobby. No chat text."""
    return {"+0x78": False, "+0x80": player.card}


class Match:
    def __init__(
        self,
        game_map: GameMap,
        on_leave=None,
        skin_of=no_skin,
        card: int = 0,
        *,
        environment: ExecutionEnvironment | None = None,
        collision_provider: CollisionProvider | None = None,
        scene_host_factory=None,
        emote_aliases_of=no_emote_aliases,
        emote_blend_trees_of=None,
        wheel_cosmetics_of=no_wheel_cosmetics,
    ) -> None:
        self.environment = SystemEnvironment() if environment is None else environment
        self.id = (
            self.environment.randbits(63, callsite="match.id.low"),
            self.environment.randbits(63, callsite="match.id.high"),
        )
        self.game_map = game_map
        self.card = card  # the queue card it was made for; 0 = none (the Practice Range)
        # (account, hero GUID) -> (skin theme, golden weapon) the player has equipped, from the lobby.
        # The client's hero select equips a skin through the lobby (24500), even in a match.
        self.skin_of = skin_of
        self.emote_aliases_of = emote_aliases_of
        self.emote_blend_trees_of = emote_blend_trees_of
        self.wheel_cosmetics_of = wheel_cosmetics_of
        self.controller = heroselect.controller_of(game_map.mode_guid)
        self.tick = 0  # the server's frame number, set every tick
        self._npc_planning_turn = 0
        self.assemble_ends: float | None = None  # PvP: when "assemble your team" ends
        self.assembled = False
        self.players: list[Player] = []
        self._body_messages: list[tuple] = []
        self.body_components = {}
        self.started = self.environment.wall_time(callsite="match.started")
        self.on_leave = on_leave  # called with each player who leaves
        self.ended = False
        self.bots = bots.make(game_map.map_guid)  # the map's training bots (bots.py)
        self._pending_bot_scripts = deque(bot for bot in self.bots if graph.entity_body(bot.body) is not None)
        self.bots_shown: dict[int, int] = {}  # player slot -> the tick the bots' creates went to its queue
        self.notices = notices.Notices(self)  # joined / left / hero switch lines in the clients' chat
        self.dynamic_spheres = DynamicSpheres()
        self.combat = combat.Combat(self)  # hits, health, deaths, respawns (combat.py)
        self.collision_provider = (
            default_collision_provider() if collision_provider is None else collision_provider
        )
        self.collision_state = LoadState("pending")
        self.collision_world: World | None = None
        self._collision_failed = False
        self.scene_host_factory = scene_host_factory
        self.scene_host = None
        self.scene_host_started = False
        self.scene_host_error = None
        self._refresh_collision()

    def _refresh_collision(self) -> bool:
        """Observe asynchronous completion at a match boundary, then keep the World for this match."""
        if self.collision_state.status == "pending":
            game_map = self.game_map
            self.collision_state = self.collision_provider.poll(
                game_map.map_guid, game_map.name, game_map.mode_guid
            )
            if self.collision_state.status == "ready":
                self.collision_world = self.collision_state.world
                self.dynamic_spheres.register_initial(self.collision_world.initial_dynamic_spheres)
                for player in self.players:
                    player.mover.use(world=self.collision_world)
        return self.collision_world is not None

    def _collision_failure(self, now: float) -> None:
        if self._collision_failed:
            return
        self._collision_failed = True
        reason = f"collision unavailable: {self.collision_state.error}"
        log.error("[game] %s: %s", self.label(), reason)
        for player in self.players:
            if not player.gone:
                if player.client is not None:
                    player.client.go_home(now)
                self.leave(player, reason)
        self.ended = True

    def add_player(
        self,
        account_lo: int,
        name: str,
        hero_guid: int,
        team: int,
        tournament: bool,
        card: dict | None = None,
    ) -> Player:
        fixed = self.controller.hero if isinstance(self.controller, heroselect.FixedController) else None
        hero = heroes().get(fixed or hero_guid) or heroes()[SOLDIER]
        quanta = TOURNAMENT_QUANTA if tournament else RETAIL_QUANTA
        player = Player(self, len(self.players), account_lo, name, hero, team, quanta, card)
        if graph.entity_body(PLAYER_DEFINITION) is not None:
            player.controller_vm = ControllerVM(
                player.entity, player.hero.guid, self.game_map.mode_guid, self.tick)
        self.players.append(player)
        return player

    def _advance_scene(self, now: float, tick: int) -> bool:
        if self.scene_host_factory is None:
            return True
        if self.scene_host_error is not None:
            return False
        try:
            if not self.scene_host_started:
                self.scene_host_started = True
                host = self.scene_host_factory(self)
                if isinstance(host, scene_host.SceneHost):
                    self.scene_host = host
                if (self.scene_host is None or host.world.map_guid != self.game_map.map_guid
                        or host.world.mode != self.game_map.mode_guid
                        or host.frame_ms != RETAIL_QUANTA // 1000):
                    raise ValueError('scene host does not belong to this map, mode or clock')
                created = {entry.placement.flat for entry in host.world.entities.values()
                           if entry.placement is not None
                           and entry.id == (0x80000000 | entry.placement.flat)}
                if created != set(self.game_map.placeables):
                    raise ValueError('scene script world differs from actual match placeable creates')
                if self.game_map.map_guid == JUNKENSTEIN_MAP and self.game_map.mode_guid in JUNKENSTEIN_MODES:
                    # The ready switch waits for the admitted roster. Arcade can
                    # start a local Junkenstein match with fewer than four players.
                    host.world.team_member_count = lambda team: sum(
                        player.team == team and not player.gone for player in self.players
                    )
                    host.world.configure_junkenstein_card(self.card)
                    if self.card == JUNKENSTEIN_THREE_THEY_WERE:
                        # The blue team's Damage Dealt rule (2539) in 011C.0C0.
                        self.combat.defender_damage_scale = 1.2
                    if host.world.session_entity_roster is None:
                        host.world.session_entity_roster = lambda: (
                            player.entity for player in self.players if not player.gone
                        )
                    if host.world.player_participant_roster is None:
                        host.world.player_participant_roster = lambda: (
                            (player.entity, PLAYER_FILTER | player.team_bit, 0)
                            for player in self.players if not player.gone
                        )
            if self.scene_host is None or not self.scene_host.advance(tick):
                raise ValueError(str(None if self.scene_host is None else self.scene_host.error))
            return True
        except Exception as error:
            log.exception('[game] %s: scene script host failed', self.label())
            self.scene_host_error = str(error)
            for player in self.players:
                if not player.gone:
                    if player.client is not None:
                        player.client.go_home(now)
                    self.leave(player, 'scene script host failed')
            if self.scene_host is not None:
                self.scene_host.close()
            self.ended = True
            return False

    def label(self) -> str:
        return f"match {self.id[0] & 0xFFFF:04X}"

    # --- messages ------------------------------------------------------------------------------

    def game_struct(self, viewer: Player) -> dict:
        """The 1064-byte game struct of 20300 and 20301: the mode, the players and their teams. A team's
        +0x60 is its number and +0x61 its size, from which the hero select screen's team list takes its
        empty slots (0x7FF789CADB40; size minus the players it lists). Players who left are not in it:
        a player who joins later takes their place."""
        playing = [player for player in self.players if not player.gone]
        records = [{"+0x50": player.name} for player in playing]
        sizes = self.game_map.team_sizes
        teams = []
        if self.game_map.free_for_all:  # the mode's one team, "FFA", holds everyone
            slots = [{"+0x0": player.entity} for player in playing]
            teams.append({"+0x18": slots, "+0x60": FREE_FOR_ALL_TEAM, "+0x61": sizes[0]})
        for team in () if self.game_map.free_for_all else (0, 1):
            slots = [{"+0x0": player.entity} for player in playing if player.team == team]
            size = sizes[team] if team < len(sizes) else 0
            teams.append({"+0x18": slots, "+0x60": team, "+0x61": size})
        return {
            "+0x0": {
                "+0x0": {
                    "+0x0": {"+0x0": records},
                    "+0xB0": self.game_map.mode_guid,
                    "+0xC0": teams,
                    "+0xD8": [{"+0x0": viewer.entity}],
                },
                "+0x274": 16,
            }
        }

    @property
    def ranked(self) -> bool:
        """Whether the match is played on a competitive card: the lobby gave its players a rank."""
        return any(player.rank is not None for player in self.players)

    def instance_message(self, viewer: Player) -> dict:
        """20300: load this map with this mode. +0x500 must equal the client's command frame length
        (0x7FF7896E94B0), else error 262445; +0x519 must match a byte that is 0 (error 262455). +0x78 is
        the instance type: ranked on a competitive card, so the team list shows the players' ranks."""
        return {
            "+0x78": RANKED if self.ranked else UNRANKED,
            "+0x80": self.game_struct(viewer),
            "+0x4A8": self.game_map.map_guid,
            "+0x500": viewer.quanta,
            "+0x508": {"+0x0": 1, "+0x8": 1},
        }

    # --- entities ------------------------------------------------------------------------------

    def placeable_ids(self) -> list[int]:
        return [0x80000000 | index for index in self.game_map.placeables]

    def spawn_updates(self, viewer: Player) -> list[EntityUpdate]:
        """The frame that ends the loading screen: the placeables, the viewer's player (and body, if it
        has one), the game mode entity, the training bots, and the bodies of the players already in the
        world."""
        updates = [
            EntityUpdate(entity, OP_CREATE, lambda origin, index=index: world.create_placeable(origin, index))
            for entity, index in zip(self.placeable_ids(), self.game_map.placeables, strict=True)
        ]
        if viewer.has_body:
            updates += self.own_body_updates(viewer)
        updates.append(
            EntityUpdate(viewer.entity, OP_CREATE, lambda origin: self._player_create(origin, viewer))
        )
        updates.append(EntityUpdate(GAME_MODE_ENTITY, OP_CREATE, self._game_mode_create))
        updates.extend(self._bot_create_update(bot) for bot in self.bots)
        if self.scene_host is not None:
            updates.extend(self.scene_host.spawned_entity_updates())
        updates.extend(self.combat.deployable_create(actor) for actor in self.combat.deployables.values())
        if self.bots:
            self.bots_shown[viewer.slot] = self.tick
        for other in self.players:
            if other is not viewer and self._shown(other):
                updates.append(self.remote_body_create(viewer, other))
        return updates

    def _prepare_bot_script(self, bot: bots.Bot) -> None:
        record = graph.entity_body(bot.body)
        if record is None:
            return
        try:
            from ow174.game import npc_context

            firing = (bot.firing and self.game_map.map_guid == PRACTICE_RANGE.map_guid
                      and self.game_map.mode_guid == PRACTICE_RANGE.mode_guid)
            components = npc_context.manual_components(bot)
            context = {} if components is None else {"controller_components": components}
            manual_rialto_spawn = (self.game_map.map_guid == 0x080000000000079F
                                   and bot.spawner.get("manual_spawn") is True)
            controller_entity = None
            association = None
            if (record.get("controller") is None and bot.spawner.get("manual_spawn") is True
                    and not bot.spawner.get("autonomous_targeting")
                    and not bot.spawner.get("acquire_targets") and getattr(bot, "pursuit", None) is None):
                association = graph.npc_controller(bot.body)
            manual_definition = graph.guid(association.get("definition")) if association is not None else None
            if record.get("controller") is not None or manual_definition is not None:
                controller_entity = self.combat.next_bot
                if (not bots.BOT_ENTITY <= controller_entity < 0xD0000000
                        or controller_entity == bot.entity
                        or any(other.entity == controller_entity for other in self.bots)
                        or controller_entity in self.combat.deployables):
                    raise ValueError('NPC controller allocator exhausted or collided')
                self.combat.next_bot += 1
            bot.body_script = BotScript(
                bot.body, bot.hero, bot.entity, self.tick, self.game_map.mode_guid, bot.health,
                firing_position=bot.position if firing else None,
                manual_nameplate=manual_rialto_spawn,
                nameplate_name=bot.name if manual_rialto_spawn else None,
                manual_rialto_spawn=manual_rialto_spawn,
                **context,
                controller_entity=controller_entity, match=self,
                manual_controller_definition=manual_definition, body_position=bot.position,
                difficulty_index=bot.spawner.get("difficulty_index"),
            )
            component = getattr(bot.body_script, "component", None)
            if isinstance(component, runtime.Component):
                component.world.match = self
        except Exception:
            log.exception("[game] %s: could not initialize %s's statescript", self.label(), bot.name)

    def _bot_create_update(self, bot: bots.Bot) -> EntityUpdate:
        # A later viewer cannot depend on the globally cleared health dirty bits.
        # Freeze its initial pools here so retransmitting the create keeps the same baseline.
        vitals = self.combat.vitals.get(bot.entity)
        components = world.health(bot.health, bot.health) if vitals is None else vitals.component(vitals.most)
        components[26] = [TEAM_BIT << bot.team]
        definition, position, rotation = bot.body, bot.position, _rotation(bot.yaw)
        return EntityUpdate(
            bot.entity, OP_CREATE,
            lambda origin: self._bot_create(origin, definition, components, position, rotation),
        )

    @staticmethod
    def _bot_create(origin: int, definition: int, components: dict, position, rotation) -> world.RecordWriter:
        """A training bot's body, as a player's body on another client: placed by its transform, moved
        by movement records from the next frame on. Without a player body's 123 (the hero): the bots'
        bodies have no such component, and the client fails the whole frame on one it cannot place
        (assert 0xD62712AE, "Failed to deserialize component update ... 143E ... /123"; 143E has 1, 3, 4,
        22, 24, 25, 26, 28, 31, 35, 39, 43, 47, 49, 51, 69, 76, 78, 83, 86, 90, 97, 115, 119, 122, 127)."""
        return world.create(
            origin, definition, components, flags=BODY_FLAGS, position=position, rotation=rotation,
        )

    def publish_bot(self, bot: bots.Bot) -> None:
        """Queue the new body's baseline for viewers already in the world."""
        bot.records_from = self.tick + BOT_RECORD_DELAY
        for viewer in self.players:
            if viewer.client is not None and viewer.spawned:
                viewer.client.queue_entities([self._bot_create_update(bot)])
                self.bots_shown.setdefault(viewer.slot, self.tick)

    @staticmethod
    def _shown(player: Player) -> bool:
        """Whether other players' clients get this player's body."""
        return player.spawned and player.has_body and player.client is not None

    def own_body_updates(self, viewer: Player, *, start_frame: int | None = None, ult_fraction: float = 0.0, charge_ratio=None) -> list[EntityUpdate]:
        """The viewer's own body: its movement state rides in the create (at frame 0xFFFFFFFE) and in a
        movement record at the packet frame."""
        state = deepcopy(viewer.pose())
        create = EntityUpdate(viewer.body, OP_CREATE, movement=state)
        self.start_body_script(viewer, create, start_frame=start_frame, ult_fraction=ult_fraction, charge_ratio=charge_ratio)
        # The new script must exist before its projectile lease is captured. The create's
        # stream remains the native driver's arrival gate; only the payload is frozen here.
        create.build = self._body_builder(viewer, state, own=True)
        return [create]

    def _body_create(
        self, origin: int, owner: Player, state: Movement | None, own: bool = False
    ) -> world.RecordWriter:
        return self._body_builder(owner, state, own)(origin)

    def _body_builder(self, owner: Player, state: Movement | None, own: bool = False):
        """Freeze a player's current health pools, team and hero, with the projectile lease for its owner."""
        vitals = self.combat.vitals.get(owner.body)
        if vitals is None:
            health, armour, shields = pools.active_pools(owner.hero.guid, owner.hero.body)
            components = world.health(health, health, armour, shields)
        else:
            components = vitals.component(vitals.most)
        components[26] = [owner.team_bit]
        components[123] = [owner.hero.guid]
        if own:
            lease = self.combat.projectiles.lease(owner)
            if lease:
                components[90] = [lease]
        definition, skin = owner.hero.body, tuple(owner.skin)
        position, rotation = tuple(owner.mover.position), _rotation(owner.mover.yaw)
        components, state = deepcopy(components), deepcopy(state)

        def build(origin: int) -> world.RecordWriter:
            return world.create(
                origin,
                definition,
                components,
                flags=BODY_FLAGS,
                position=position,
                rotation=rotation,
                movement=state,
                skin=skin,
            )

        return build

    def _player_create(self, origin: int, player: Player, own: bool = True) -> world.RecordWriter:
        """A player's entity, on the player's own client or another's. The same flags for both: the
        client adds the command components (59, 63) only to the entity 20302 named (0x7FF78955EE90). It
        carries the team in the filter bits (26, which the client copies into the component GetTeam and
        IsEnemy read), the player's info (29) and the hero select participant (74) with the portrait
        frame, which makes every client load the frame the team list draws (0x7FF7894C9D90), and the
        role queue role; on the player's own client also the body the player possesses (44, 75) and his
        Tab board numbers (64, owner-only). Other clients get his 44 and 75 once his body is on them
        (_show_players)."""
        frame = player.card.get("+0x28") or None
        components = {
            26: [player.controller_filter_bits],
            29: self._player_info(player),
            74: [frame, None, player.slot, None, None, player.role or None, 1, 1, None],
        }
        if own and player.has_body:
            components.update(self._possession(player))
        if own:
            # Packet budget probes may serialize a candidate without sending it.
            components[stats.GMP_STATS] = self._board(player)
        return world.create(origin, PLAYER_DEFINITION, components, flags=PLAYER_FLAGS)

    @staticmethod
    def _player_info(player: Player) -> list:
        """Component 29 (MPlayerComponentData): the player's id, his BattleTag (m_battleTag: the client
        names the entity by it, as in the hero switch line of the chat), his level (m_playerLevel, the HUD
        panel's number) and on a competitive card his rank (m_rankedLevel, m_heroicRank,
        m_rankedLevelTier), which the team list shows (0x7FF789A16620)."""
        rating, place, tier = player.rank or (None, None, None)
        tag = player.card.get("+0x40") or player.name or None
        return [player.player_id, tag, player.card.get("+0x38"), None, None, None, None, rating, place, tier]

    def controller_record(self, entity: int):
        """Declared relations for this server's existing player/controller model.

        Native NPC controller allocation and component membership stay unknown.
        Player membership mirrors emitted44/75; the body->controller binding is
        this server's reconstruction, separate from native function validation.
        """
        from ow174.game.script.controller_queries import Record

        for player in self.players:
            if player.gone:
                continue
            if player.entity == entity:
                return Record(entity, frozenset((44, 75))) if player.has_body else NotImplemented
            if player.has_body and player.body == entity:
                return Record(entity, frozenset((43,)), player.entity)
        if any(bot.entity == entity for bot in self.bots):
            return NotImplemented
        if entity == world.GAME_MODE_ENTITY:
            return Record(entity, frozenset((114,)))
        return None


    @staticmethod
    def _possession(player: Player) -> dict:
        """Components 44 (the body the player possesses, views and controls) and 75 (its hero, skin
        theme and the last valid hero)."""
        theme = player.skin[0] or None
        return {44: [player.body] * 3, 75: [player.hero.guid, theme, None, player.hero.guid]}

    def _board(self, player: Player) -> list:
        """Component 64's board fields for the player's own client (stats.py): the mode's player stats
        with their medals among his teammates (all players in a free-for-all) and his hero's stats."""
        team = [
            other
            for other in self.players
            if not other.gone and (self.game_map.free_for_all or other.team == player.team)
        ]
        if player not in team:
            team.append(player)
        mode = self.game_map.mode_guid
        medals = stats.medals([other.stats for other in team], mode)[team.index(player)]
        hero = player.hero.guid if player.has_body else None
        return stats.component(player.stats, mode, hero, medals)

    def _send_board(self, player: Player, now: float) -> None:
        """The board numbers again when they changed, at most every STATS_EVERY seconds."""
        if not player.client.controller_ready or now < player.next_stats:
            return
        values = self._board(player)
        if values == player.stats_sent:
            return
        player.stats_sent = values
        player.next_stats = now + STATS_EVERY
        update = {stats.GMP_STATS: values}
        player.client.queue_entities(
            [EntityUpdate(player.entity, OP_UPDATE, lambda origin: world.update(origin, update))]
        )

    def _game_mode_create(self, origin: int) -> world.RecordWriter:
        return world.create(origin, GAME_MODE_DEFINITION, {114: [None, self.game_map.mode_guid]})

    def remote_body_create(self, viewer: Player, other: Player) -> EntityUpdate:
        """Another player's body: placed by its transform, then moved by movement records from the next
        frame on (a movement state in the create would sit at frame 0xFFFFFFFE, and the client refuses
        every later record that is not newer)."""
        viewer.seen[other.body] = False
        update = EntityUpdate(other.body, OP_CREATE, self._body_builder(other, None))
        self.combat.observers.created(viewer, other, update)  # its statescript once it arrived (observers.py)
        return update

    def ensure_remote_body(self, viewer: Player, other: Player) -> None:
        """Queue the current visible body's create once, including before the next match update."""
        if (viewer is not other and viewer.spawned and viewer.client is not None
                and self._shown(other) and other.body not in viewer.seen):
            viewer.client.queue_entities([self.remote_body_create(viewer, other)])

    def remote_movements(self, viewer: Player) -> list[EntityUpdate]:
        """A movement record for every other player's body in every frame, standing or not, as the
        retail server sent them (OW2's live traffic); the first in the frame after the body's create. And
        the viewer's own body's state, which its client checks its prediction against (correction.py)."""
        updates = []
        for other in self.players:
            if other is viewer or other.body not in viewer.seen:
                continue
            if not viewer.seen[other.body]:
                continue
            updates.append(EntityUpdate(other.body, movement=other.movement()))
        shown = self.bots_shown.get(viewer.slot)
        client = viewer.client
        if client is not None and self.scene_host is not None:
            updates.extend(self.scene_host.spawned_entity_movements(viewer))
        if client is not None and shown is not None and self.tick - shown >= BOT_RECORD_DELAY:
            for bot in self.bots:
                if not client.bot_create_acked(bot.entity) or not bot.record_due(self.tick, SEND_EVERY):
                    continue
                movement = bot.movement(self.tick)
                update = EntityUpdate(bot.entity, movement=movement)
                if self.game_map.map_guid == 0x080000000000079F and bot.spawner.get("manual_spawn") is True:
                    update.npc_idle = (
                        not bot.firing and not bot.walking() and not movement.flags
                        and movement.velocity == (0, 0, 0) and movement.throttles == (0, 0)
                        and movement.input_frame is not None and movement.spring_speed == 0
                    )
                updates.append(update)
        own = correction.own_record(viewer, self.tick)
        if own is not None:
            updates.append(own)
        return updates

    @staticmethod
    def sent_updates(viewer: Player, updates) -> None:
        """Commit only records selected for a successfully sent datagram, never budget candidates."""
        script = viewer.body_script
        for update in updates:
            if update.op == OP_CREATE and update.entity in viewer.seen:
                viewer.seen[update.entity] = True
            correction.sent(viewer, update)
            if (
                script is not None and not script.retired and update.entity == viewer.body
                and update.stream is script.stream and update.chunk is not None and not update.resend
            ):
                script.report_sent(update.chunk_last)

    def destroy_for_others(self, player: Player, body: int) -> None:
        for other in self.players:
            if other is not player and body in other.seen:
                del other.seen[body]
                if other.client is not None:
                    shown = other.shown.get(player.entity)
                    if shown is not None and shown.possession is not None and shown.possession[0] == body:
                        shown.possession = None
                        other.client.cancel_possession(player.entity)
                    other.client.queue_entities([EntityUpdate(body, OP_DESTROY)])

    # --- the other players, for the hero select team list ---------------------------------------

    @staticmethod
    def _in_world(player: Player) -> bool:
        """Whether the other clients have this player's entity: from his spawn until he leaves."""
        return player.spawned and player.client is not None

    def _show_players(self, viewer: Player) -> None:
        """Keep the viewer's client up to date on the players in the world: their cards (its own one
        too), each other player's entity, created with his id, and after its acknowledgement a frame with
        the team list entry and his pick, sent again when the pick changes. A player who left goes.
        Every player, teammate or enemy, as retail does: 288D keeps enemies out of the list. A few new
        players per tick, so that their cards fit in one datagram with the rest. Each card goes once
        more CARD_AGAIN_TICKS later, which redraws the team list after the portrait frames loaded."""
        client = viewer.client
        if not viewer.card_sent:
            viewer.card_sent = True
            viewer.card_again = self.tick + CARD_AGAIN_TICKS
            client.queue_reliable(PLAYER_CARD, card_message(viewer))
        elif viewer.card_again is not None and self.tick >= viewer.card_again:
            viewer.card_again = None
            client.queue_reliable(PLAYER_CARD, card_message(viewer))
        cards = 0  # other players' cards this tick, new ones and ones sent again
        for other in self.players:
            if other is viewer:
                continue
            shown = viewer.shown.get(other.entity)
            if not self._in_world(other):
                if shown is not None:
                    del viewer.shown[other.entity]
                    client.queue_entities([EntityUpdate(other.entity, OP_DESTROY)])
                continue
            if shown is None:
                if cards == NEW_PLAYERS_PER_TICK:
                    continue
                cards += 1
                shown = viewer.shown[other.entity] = ShownController(other.entity, self.tick)
                client.queue_reliable(PLAYER_CARD, self.notices.card(viewer, other))
                create = EntityUpdate(
                    other.entity, OP_CREATE,
                    lambda origin, other=other: self._player_create(origin, other, own=False),
                )
                create.stream = shown
                client.queue_entities([create])
                continue
            again = shown.card_again
            if again is not None and self.tick >= again and cards < NEW_PLAYERS_PER_TICK:
                cards += 1
                shown.card_again = None
                client.queue_reliable(PLAYER_CARD, card_message(other))
            pick = other.hero.guid if other.has_body else None
            if shown.create_acked and (not shown.script.last or pick != shown.pick):
                shown.pick = pick
                client.queue_entities([shown.script.full_frame(heroselect.team_entry_frame(pick))])
            self._show_possession(viewer, other, shown)

    def _show_possession(self, viewer: Player, other: Player, shown: ShownController) -> None:
        """Another player's 44 (his body) and 75 (his hero) on the viewer's client, once that client has
        both his entity and his body, and again after a switch. The Tab board shows the hero of every
        row from 75 (0x7FF789A16620, read when 74 is there), and 44 makes the client set the body's
        possessor (43, the comp 44 hook 0x7FF789719850), by which it names the body (0x7FF789CB3F80) and
        finds his On Fire flag (0x7FF789BE2370). The hook's re-possess (0x7FF789AB2CF0) resets the input
        and the mover only for a possessor with component 59, which only the local player has; a ch3
        record of the body likewise feeds a possessor's 59 only (0x7FF7896DAA95)."""
        if (
            not shown.create_acked or not other.has_body
            or other.body not in viewer.seen or not viewer.client.bot_create_acked(other.body)
        ):
            return
        possession = (other.body, other.hero.guid, other.skin[0])
        if possession == shown.possession:
            return
        shown.possession = possession
        components = self._possession(other)
        update = EntityUpdate(other.entity, OP_UPDATE, lambda origin: world.update(origin, components))
        viewer.client.queue_possession(update, other.body)

    # --- lifetime ------------------------------------------------------------------------------

    def _advance_bots(self, tick: int) -> None:
        if not self.bots:
            return
        from ow174.game import npc_navigation

        if self._pending_bot_scripts:
            # Keep graph setup within the loading window and off the world-create burst.
            self._prepare_bot_script(self._pending_bot_scripts.popleft())
        manual = [bot for bot in self.bots if bot.spawner.get('manual_spawn') is True]
        budgets = npc_navigation.planning_budgets(self, manual, self._npc_planning_turn)
        if budgets:
            self._npc_planning_turn += 1  # server ticks can skip under load
        pillars = ice_wall.pillar_actors(self.combat)
        for bot in self.bots:
            if self.combat.companions.actor_for(bot.entity) is not None:
                continue  # The original phase driver owns this same NPC/mover.
            if self.combat.junkrat.rooted(bot.entity, tick):
                npc_navigation.stop(bot)
                bot._pause(tick)
                bot.last_tick = tick
                continue
            if self.combat.orisa.controls(bot.entity, tick):
                continue
            hooks = getattr(self.combat, "hooks", None)
            if hooks is not None and hooks.controlled(bot.entity):
                continue  # the retained hook consumer owns this tick's shared body mover.
            if bot.entity in self.combat.knockbacks.whole_hog_motion:
                continue  # Sole selected source host advances this same NPC mover once.
            npc_navigation.refresh_obstacles(bot, self.combat.walls.pieces)
            if self.combat.walls.step_npc(bot, tick, RETAIL_QUANTA / 1e6):
                continue
            if (bot.spawner.get('manual_spawn') is True and getattr(bot, "pursuit", None) is None
                    and not bot.route and not self.combat.knockbacks.affecting(bot.entity)
                    and not self.combat.knockbacks.has_pending(bot.entity)):
                npc_navigation.step(self, bot, tick, RETAIL_QUANTA / 1e6,
                                    plan_budget=budgets.get(bot.entity, 0))
                continue
            script = bot.body_script
            mods = statescript_mods(script.component) if script is not None and not bot.dead else {}
            pull_mods = self.combat.orisa.npc_movement_mods(bot.entity, tick)
            if pull_mods:
                mods.update(pull_mods)
            mods = self.combat.movement_mods(bot, mods, tick=tick)
            self.combat.knockbacks.step_npc(
                bot, tick, RETAIL_QUANTA / 1e6, self.collision_world,
                paused_until=self.combat.movement_pause_until(bot), movement_mods=mods, pillars=pillars,
            )

    def update(self, now: float, tick: int, *, frames: bool | None = None) -> None:
        """Run the spawn steps of players who loaded, keep their statescript acked, walk the training
        bots, and show new players and bodies to the others."""
        if self.ended:
            return
        if frames is None:
            frames = sends(tick)
        self.tick = tick
        for player in self.players:
            host = player.controller_vm
            if host is not None and not player.gone and not host.retired:
                try:
                    host.advance(tick, player.hero.guid)
                except Exception:
                    log.exception("[game] %s: %s's controller VM failed", self.label(), player.name)
                    host.retire()
        ready = self._refresh_collision()
        if self.collision_state.status == "unavailable":
            self._collision_failure(now)
            return
        if ready and not self._advance_scene(now, tick):
            return
        if ready:
            first = tick
            frozen_sentries = self.combat.frozen_sentry_replay(tick)
            if frozen_sentries:
                # The delivered lock proof admits only one freeze plus the
                # normal retained window. Reuse this same route/nav driver.
                first = self.combat.tick + 1
            elif (self.combat.tick and tick > self.combat.tick + 1
                    and self.combat.lucio_auras.has_moving_npc_recipients()):
                first = max(self.combat.tick + 1, tick - combat.COMBAT_CATCH_UP + 1)
            for at in range(first, tick + 1):
                self.tick = at
                self._advance_bots(at)
                self.combat.update(now - (tick - at) * combat.TICK, at,
                                   frames=frames and at == tick)
            self.tick = tick
        if self.assemble_ends is not None and not self.assembled and now >= self.assemble_ends:
            self._end_assemble(now)
        for player in self.players:
            client = player.client
            if client is None:
                continue
            if player.loaded_at is not None:
                self._spawn_steps(player, client, now)
            if frames and player.steps_done >= 4 and client.controller_ready:
                dynamic, bound_vars, key = self._controller_effect_data(player)
                if key != player.controller_effect_key:
                    self.send_controller(player, effect_data=(dynamic, bound_vars, key))
            if player.script.data_last and now >= player.script.next_ack:
                self._owner_ack(player, player.script, now)
            if player.body_script is not None and player.spawned and frames:
                self._body_frames(player, client)
            if self.assembling() and player.steps_done >= 4 and now >= player.next_countdown:
                self._send_countdown(player, now)
            if player.steps_done >= 4:
                self._send_board(player, now)
                if (player.hero.guid if player.has_body else None) != player.controller_hero:
                    self.send_controller(player)  # v940 follows the hero (a switch that kept the screen)
        self.notices.update(now)
        for viewer in self.players:
            if not viewer.spawned or viewer.client is None:
                continue
            self._show_players(viewer)
            for other in self.players:
                self.ensure_remote_body(viewer, other)

    def _spawn_steps(self, player: Player, client, now: float) -> None:
        waited = now - player.loaded_at
        scene_ready = self.scene_host is not None and self.scene_host.client_ready
        if player.steps_done == 0 and waited >= SEND_OWN_ENTITY:
            client.queue_ownership()
            player.steps_done = 1
        if player.steps_done == 1 and waited >= SEND_GAME:
            client.queue_reliable(20301, {"+0x78": self.game_struct(player)})
            player.steps_done = 2
        if (
            player.steps_done == 2
            and waited >= SEND_WORLD
            and client.ownership_delivered()
            and self.collision_world is not None
            and not self._pending_bot_scripts
            and (self.scene_host_factory is None or scene_ready)
        ):
            client.queue_entities(self.spawn_updates(player))
            player.steps_done = 3
            player.spawned = True
            self.notices.entered_world(player)
            log.info("[game] %s: %s spawned", self.label(), player.describe())
            if not scene_ready and self.controller.graph == heroselect.PVP.graph and self.assemble_ends is None:
                self.assemble_ends = now + ASSEMBLE_SECONDS
                log.info("[game] %s: assemble your team, %d s", self.label(), ASSEMBLE_SECONDS)
        if player.steps_done == 3 and waited >= SEND_CONTROLLER:
            if scene_ready and not player.mode_script.delivered:
                return
            if isinstance(self.controller, heroselect.FixedController) and not client.controller_ready:
                return
            player.steps_done = 4
            if isinstance(self.controller, heroselect.FixedController):
                if not self.switch_hero(player, player.hero):
                    player.steps_done = 3
                    return
                player.select_open = False
            self.send_controller(player)
            self.combat.spawned(player)
        elif player.steps_done == 4 and not player.script.data_last:
            self.send_controller(player)  # the initial frame may be waiting for the create ACK

    # --- assembling heroes ---------------------------------------------------------------------

    def assembling(self) -> bool:
        return self.assemble_ends is not None and not self.assembled

    def _send_countdown(self, player: Player, now: float) -> None:
        """The seconds left, as the game mode entity's v1303. The client shows them rounded up."""
        player.next_countdown = now + COUNTDOWN_EVERY
        seconds = max(0.0, self.assemble_ends - now)
        frame = combat.mode_frame(
            {COUNTDOWN: float(seconds)}, self.game_map.mode_guid, self.game_map.map_guid,
        )
        player.client.queue_mode_frame(frame)

    def _end_assemble(self, now: float) -> None:
        """The countdown is over: the screens of the players who have a hero close, and the skin
        selector goes."""
        self.assembled = True
        log.info("[game] %s: heroes assembled", self.label())
        for player in self.players:
            if player.client is None or player.steps_done < 4:
                continue
            self._send_countdown(player, now)
            if player.has_body:
                player.select_open = False
            self.send_controller(player)

    # --- the player's statescript --------------------------------------------------------------

    def _newest_frame(self, player: Player) -> int:
        """The newest command frame we have from the client, or our tick before the first."""
        frame = player.commands.last_frame
        return self.tick if frame is None else frame

    @staticmethod
    def _controller_effect_data(player):
        host = player.controller_vm
        dynamic, bound_vars = host.owner_effect_wires() if host is not None else ((), {})
        if not dynamic:
            return dynamic, bound_vars, None
        content = owner_full_frame(1, [], {}, dynamic=dynamic, bound_vars=bound_vars)
        return dynamic, bound_vars, (content.value, content.count)

    def send_controller(self, player: Player, *, effect_data=None) -> None:
        """A full frame of the controller's statescript, with hero select open or closed. The skin
        selector shows in the PvP modes while heroes are assembled, in the Practice Range whenever
        the screen is open."""
        if player.client is None or not player.client.controller_ready:
            return
        cmfd = player.script.next_cmfd(self._newest_frame(player))
        practice = self.controller is heroselect.PRACTICE
        skins = player.select_open and (practice or self.assembling())
        switch = self.notices.presence(player)  # the last hero switch for the chat line (notices.py)
        hero = player.controller_hero = player.hero.guid if player.has_body else None
        instances, variables = heroselect.controller_instances(
            self.controller, player.select_open, skins, practice, presence=switch, hero=hero,
            body=player.body if player.has_body else None,
        )
        if effect_data is None:
            effect_data = self._controller_effect_data(player)
        dynamic, bound_vars, key = effect_data
        frame = owner_full_frame(cmfd, instances, variables, dynamic=dynamic, bound_vars=bound_vars)
        update = player.script.full_frame(frame)
        # A switch notice is a separate event, even when the menu is already closed.
        update.controller_close = player.has_body and not player.select_open and not switch
        player.client.queue_entities([update])
        player.controller_effect_key = key
        state = "open" if player.select_open else "closed"
        log.info("[game] %s: %s's controller sent (hero select %s)", self.label(), player.name, state)

    def _owner_ack(self, player: Player, stream: Stream, now: float) -> None:
        """A delta owner frame that only moves CmFD on, so the client replays fewer of its own commands.
        Only once the frames with data have arrived: a newer chunk whose range covers a queued one makes
        the client drop that one."""
        stream.next_ack = now + OWNER_ACK_EVERY
        if stream.data_in_flight or (player.commands.last_frame or 0) <= stream.cmfd:
            return
        cmfd = stream.next_cmfd(self._newest_frame(player))
        player.client.queue_entities([stream.ack_delta(owner_ack(cmfd))])

    # --- the body's statescript (ow174/game/script) ------------------------------------------------

    def script_player_body(self, entity: int, owner: int) -> Player | None:
        """Resolve a live player body, including the owner during its own create."""
        return next((player for player in self.players
                     if player.body == entity and player.has_body and player.client is not None
                     and not player.gone and (player.spawned or entity == owner)), None)

    def script_movement_flags(self, entity, owner):
        """Read delivered physical flags with existing movement-owner priority."""
        for player in self.players:
            if (player.body == entity and player.has_body and player.client is not None
                    and (player.spawned or entity == owner)):
                return player.mover.flags
        for bot in self.bots:
            if bot.entity != entity:
                continue
            if bot.control_mover is not None:
                return NotImplemented  # the controlled DVa contract is separate
            mover = self.combat.zarya_gravitons.npc_movers.get(entity)
            pull = self.combat.orisa.pulls.get(entity)
            if mover is None and pull is not None:
                mover = pull.mover
            if mover is None:
                mover = self.combat.knockbacks.active.get(entity)
            return mover.flags if mover is not None else bot.movement_flags
        return None

    def script_body_position(self, entity: int, owner: int) -> expr.Vec3 | None:
        """The current match pose of a body, including the owner during its own create."""
        player = self.script_player_body(entity, owner)
        if player is not None:
            return expr.Vec3(*player.mover.position)
        for bot in self.bots:
            if bot.entity == entity:
                return expr.Vec3(*bot.position)
        actor = self.combat.barriers.world.actors.get(entity)
        if actor is not None:
            return expr.Vec3(*actor.point)
        position = self.combat.orisa.position(entity)
        if position is not None:
            return expr.Vec3(*position)
        return None

    def resolve_body_spray(self, entity: int, length: float) -> dict | None:
        collision = self.collision_world
        player = next((player for player in self.players if player.body == entity and player.has_body), None)
        if collision is None or player is None or self.combat.is_dead(player):
            return None
        mover = player.mover
        x, y, z = mover.position
        origin = (x, y + combat.eye_height(player.hero.body, mover.crouched), z)
        direction = combat.aim(mover.yaw, mover.pitch)
        spray_geometry = collision.ray_geometry or collision
        hit = spray_geometry.cast_ray(origin, direction, length, spray_placement.SURFACE_CATEGORY)
        if hit is None:
            return None
        return spray_placement.from_hit(direction, hit)

    def _message_player(self, entity: int) -> Player | None:
        shown = (player for player in self.players if player.body == entity and self._shown(player))
        return next(shown, None)

    def _message_actor(self, entity: int):
        player = self._message_player(entity)
        if player is not None:
            return player
        # Only explicitly match-bound, owned bodies participate. Ordinary NPC
        # scripts keep their existing unsupported message-delivery boundary.
        return next((bot for bot in self.bots if bot.entity == entity and bot.owner
                     and bot.body_script is not None
                     and bot.body_script.component.world.match is self), None)

    def register_body_component(self, component):
        previous = self.body_components.get(component.entity)
        if previous is not None and previous is not component:
            raise ValueError("body already has a registered statescript component")
        if component.world.match is not self:
            raise ValueError("body component belongs to another match")
        self.body_components[component.entity] = component

    def unregister_body_component(self, component):
        if self.body_components.get(component.entity) is component:
            self.body_components.pop(component.entity)

    def _message_script_live(self, script) -> bool:
        actor = self._message_actor(script.component.entity)
        return (actor is not None and actor.body_script is script
                and not getattr(script, "retired", False) and script.component.fault is None)

    def queue_body_message(self, target: int, message: int, params: dict, sender: int,
                           *, valkyrie_frame: int | None = None) -> bool:
        """Capture the source and recipient scripts before either entity ID can be reused."""
        source, recipient = self._message_actor(sender), self._message_actor(target)
        if source is None or recipient is None:
            return False
        source_script, script = source.body_script, recipient.body_script
        if (source_script is None or script is None
                or not self._message_script_live(source_script) or not self._message_script_live(script)):
            return False
        if len(self._body_messages) >= MAX_BODY_MESSAGES:
            expr.warn_once("body-message-limit", "match body message queue reached %d", MAX_BODY_MESSAGES)
            return False
        row = (source_script, recipient, script, message, dict(params))
        self._body_messages.append(row if valkyrie_frame is None else (*row, valkyrie_frame))
        return True

    def deliver_body_messages(self) -> None:
        """Drain one match batch without advancing recipients' command frames."""
        if not self._body_messages:
            return
        pending, self._body_messages = self._body_messages, []
        # Messages emitted by a receiver enter the next batch, outside its VM call stack.
        for row in pending:
            source, player, script, message, params = row[:5]
            valkyrie_frame = row[5] if len(row) == 6 else None
            if not self._message_script_live(source):
                continue
            if player.body_script is not script or not self._message_script_live(script):
                continue
            try:
                component = script.component
                from ow174.game import body_entities

                if (message == body_entities.RETURN_TO_PILOT and isinstance(player, Player)
                        and not body_entities.accepts_initial_return(self, player, source)):
                    continue
                # An earlier message can start or stop a later message's entry state.
                from ow174.game.script import charge, regeneration

                if message == charge.BOOSTED and script.hero == regeneration.MERCY:
                    charge.deliver_boost(script, params, source.entity, self.combat.tick, valkyrie_frame)
                elif runtime.send_message(component, message, params, source.component.entity):
                    component.run_now()
                if (message == body_entities.RETURN_TO_PILOT and isinstance(player, Player)
                        and player.controlled_mech is not None and player.controlled_mech.body_script is source):
                    body_entities.complete_return(player)
            except Exception:
                log.exception("[game] %s: %s's body message handling failed", self.label(), player.name)
                if isinstance(player, Player):
                    self.stop_body_script(player, failed=True)
                else:
                    from ow174.game import npc

                    npc.remove_body(self, player)

    def start_body_script(
        self, player: Player, create: EntityUpdate, *, start_frame: int | None = None, ult_fraction: float = 0.0,
        charge_ratio=None,
    ) -> None:
        """The statescript of the player's new body; the old body's stops. It runs from
        the newest command frame on, and its full frame goes out once the client acknowledged the create."""
        self.stop_body_script(player)
        if BODY_SCRIPT_HEROES is not None and player.hero.guid not in BODY_SCRIPT_HEROES:
            return
        try:
            spray_unlocks, voice_effects = self.wheel_cosmetics_of(player.account_lo, player.hero.guid)
            canonical = heroes().get(player.hero.guid)
            emote_args = {}
            if canonical is not None and player.hero.body != canonical.body:
                emote_args["body_definition"] = player.hero.body
            blends = () if self.emote_blend_trees_of is None else self.emote_blend_trees_of(
                player.account_lo, player.hero.guid, **emote_args,
            )
            script = BodyScript(
                player.hero.guid,
                player.body,
                self._newest_frame(player) if start_frame is None else start_frame,
                quanta=player.quanta,
                mode=self.game_map.mode_guid,
                match=self,
                body_definition=player.hero.body,
                emote_aliases=self.emote_aliases_of(player.account_lo, player.hero.guid, **emote_args),
                emote_blend_trees=blends,
                spray_unlocks=spray_unlocks,
                voice_effects=voice_effects,
            )
            mount_intro = getattr(self.scene_host, "mount_player_intro_receiver", None)
            if mount_intro is not None:
                mount_intro(script)
            script.restore_genji_ult(ult_fraction, player.entity)
            from ow174.game import ultimate

            ultimate.start_practice_timer(script, GAME_MODE_ENTITY)
        except Exception as error:
            from ow174.game import body_entities

            body_entities.retire_source(self, player.entity, player.body)
            player.body_script_failed = isinstance(error, ScriptBudgetError)
            log.exception("[game] %s: no body statescript for %s", self.label(), player.describe())
            return
        from ow174.game import lucio

        if script.hero == lucio.HERO:
            lucio.restore_charge(script, charge_ratio, player.entity)
        else:
            orisa_charge.restore(script, charge_ratio, player.entity)
        if player.retained_ult_charge is not None:
            if script.hero == 0x02E0000000000007:
                cost = expr.to_float(script.component.entity_var(338).value())
                script.set_ult_charge(min(cost, player.retained_ult_charge))
            player.retained_ult_charge = None
        self.combat.orisa.superchargers.bind_owner(player, script)
        script.created(create)
        player.body_script = script
        self.combat.junkrat.charge.attach(player)
        if player.hero.guid == 0x02E000000000007A:
            from ow174.game import body_entities

            body_entities.begin_initial_control(player)
        bodytrace.started(player, script)

    @staticmethod
    def stop_body_script(player: Player, *, failed: bool = False) -> None:
        if player.controlled_mech is not None:
            from ow174.game import body_entities

            body_entities.release_control(player)
        script = player.body_script
        if script is not None:
            player.match.combat.junkrat.charge.detach(player, body=script.entity)
            bodytrace.stopped(player, script)
            initial = script.stream.initial_owner
            if initial is not None and player.client is not None:
                player.client.cancel_entity_record(initial)
            script.retire()
            player.body_script = None
        player.body_script_failed = failed

    def body_command(
        self, player: Player, command: Command, *, movement_end: tuple[runtime.State, bool] | None = None,
        posed: bool = True, healing_tick: int | None = None,
    ) -> None:
        """One new command frame for the body's statescript. A failure turns it off for this body (logged)
        instead of breaking the tick."""
        script = player.body_script
        before_hash = bodytrace.before(script)
        try:
            from ow174.game import hanzo_movement

            hanzo_movement.sync(player)
            if posed and script.hero == 0x02E0000000000079:
                from ow174.game import wall_ride

                controller = wall_ride.controller(script)
                contact = player.mover.wall_contact
                if controller is not None and (contact is not None or 14980 in controller.vars):
                    wall_ride.feedback(controller, contact)
            if self.collision_world is not None and script.hero == 0x02E00000000001CA:
                from ow174.game import ball
                ball.feedback(player)
            if posed and self.collision_world is not None:
                earthshatter.prepare(self.combat, player)
            if movement_end is not None:
                state, arrived = movement_end
                delay = command.frame * script.frame_ms - script.component.now
                state.request(runtime.FINISH if arrived else runtime.ABORT, max(0, delay))
            if script.hero == moira.MOIRA and self._shown(player) and not self.assembling():
                moira.start_charge(player, script)
            controls = getattr(self.combat, "sombra_controls", None)
            if posed and controls is not None:
                controls.preselect(player, command)
            script.command(
                command.frame, command.buttons, command.action, command.forward, command.right,
                grounded=not player.mover.airborne,
                airborne=player.mover.airborne if posed else None,
                healing_tick=healing_tick if script.hero == 0x02E0000000000040 else None,
            )
        except Exception as error:
            bodytrace.failed(script, error)
            log.exception("[game] %s: %s's body statescript failed", self.label(), player.name)
            # Command epochs also involve the controller stream and movement history.
            self.stop_body_script(player, failed=not isinstance(error, CommandEpochError))
        else:
            bodytrace.command(player, script, command, before_hash)

    def _body_frames(self, player: Player, client) -> None:
        """This tick's frames of the body's statescript: the full frame, then owner frames."""
        try:
            script = player.body_script
            from ow174.game import defenses, ultimate

            anchor = script.world_frame_anchor
            if (anchor is not None and script.frame > anchor[1] and script.stream.cmfd >= anchor[1]
                    and ultimate.practice_passive_clock(script) and not defenses.soldier_visor_running(player)
                    and not script._unsent_health_changed(client.entities)):
                return
            # A newer unsent projection stays non-correcting until the first
            # owner frame goes out. Its command frame starts the settle interval.
            replace_initial = (
                script.first_owner_frame is not None and not script.report_count
                and bool(script.stream.pending)
            )
            kill_feed = None
            if self.combat.is_dead(player):
                kill_feed = self.combat.kill_feed_allowed(player, player.body)
            client.queue_entities(script.frames(
                kill_feed=kill_feed, replace_initial=replace_initial, queued=client.entities,
            ))
        except Exception:
            log.exception("[game] %s: %s's body statescript failed", self.label(), player.name)
            self.stop_body_script(player, failed=True)

    def communication_action(self, player: Player, value: dict) -> None:
        """Deliver a 21608 wheel action on the sender's current body."""
        action = value["+0x78"]
        if action & ~0xFFFFFFFF != WHEEL_ACTION_PREFIX:
            return
        if not any((action & 0xFFFFFFFF) in actions for actions in WHEEL_ACTION_CATEGORIES.values()):
            return
        params = {0x8FC: expr.Asset(action), 0x3E1: value["+0x80"]}
        if action == (WHEEL_ACTION_PREFIX | 0x170):
            marker = (value.get("+0x84") or {}).get("+0x0", 0)
            if marker:
                params[0x644] = expr.Entity(marker)
        self.queue_body_message(player.body, 0x02400000000002A3, params, player.body)

    def _wheel_use_report(self, player: Player, value: dict, parameters: bytes) -> None:
        # 904C20 uses the default message header, then one 053B Asset row.
        prefix = b"\x00\x00\x00\x00\x01\xC3\x3B\x05\x40\x0A\x04"
        if len(parameters) != 27 or parameters[:11] != prefix or parameters[19:] != bytes(8):
            return
        script = player.body_script
        if (script is None or script.retired or script.component.fault is not None
                or not player.has_body or script.entity != player.body
                or (value.get("+0x78") or {}).get("+0x0") != player.body):
            return
        instance = script.component.instances.get(value.get("+0xB0"))
        if instance is None or instance.stopped:
            return
        reported = int.from_bytes(parameters[11:19], "little")
        if not reported:
            return
        if instance.graph.index == 0x02CF:
            placement = instance.state(1)
            pulse = instance.state(14)
            current = (placement is not None and placement.active and pulse is not None and pulse.active
                       and placement.payload().get("unlock") == reported)
            accepted = current or script.matches_sent_spray(instance, reported)
        elif instance.graph.index == 0x0686:
            pulse = instance.state(13)
            query = instance.graph.node(67).field("m_value")
            accepted = (pulse is not None and pulse.active
                        and (instance.evaluate(query) == expr.Asset(reported)
                             or script.matches_sent_voice_effect(instance, reported)))
        else:
            return
        if accepted:
            runtime.send_instance_message(instance, 0x0240000000001313, {0x53B: expr.Asset(reported)}, player.body)

    def game_message(self, player: Player, value: dict, now: float) -> None:
        """21616: a game message a statescript action sends to the server, such as a hero pick."""
        message = value.get("+0xA8", 0)
        parameters = bytes(value.get("+0x80") or b"")
        log.info(
            "[game] %s: %s sent game message %X (instance %s) %s",
            self.label(),
            player.name,
            message & 0xFFFFFFFF,
            value.get("+0xB0"),
            parameters.hex(" "),
        )
        if message == 0x0240000000001313:
            self._wheel_use_report(player, value, parameters)
            return
        if isinstance(self.controller, heroselect.FixedController):
            return  # Tutorial has no hero picker or reopen request.
        if message == heroselect.PICK_HERO:
            hero = heroes().get(heroselect.picked_hero(parameters) or 0)
            if hero is None:
                log.warning("[game] %s: no hero found in the pick", self.label())
                return
            if player.role and ROLE_NUMBERS.get(hero.role) != player.role:
                label, name = self.label(), player.name
                log.warning("[game] %s: %s picked %s, not of the queued role", label, name, hero.name)
                return
            skin = self.skin_of(player.account_lo, hero.guid)
            needs_body = not player.has_body or player.body_script_failed
            if ((needs_body or hero is not player.hero or skin != player.skin)
                    and not self.switch_hero(player, hero)):
                return
            if self.assembling():
                return  # the screen stays open until the heroes are assembled, as in retail
            player.select_open = False
            self.send_controller(player)
        elif message == heroselect.OPEN_OR_CLOSE:
            wanted = heroselect.wanted_open(parameters)
            player.select_open = not player.select_open if wanted is None else wanted
            if not player.has_body:
                player.select_open = True  # no hero yet: nothing to go back to
            self.send_controller(player)

    def skin_changed(self, player: Player, hero_guid: int) -> bool:
        """A skin equipped in the lobby for the hero the player plays: a new body with it, as a pick of
        the same hero with another skin gets (switch_hero). Other heroes' skins wait for their pick."""
        if not player.has_body or player.hero.guid != hero_guid:
            return False
        if self.skin_of(player.account_lo, hero_guid) == player.skin:
            return False
        return self.switch_hero(player, player.hero)

    def map_loaded(self, player: Player, now: float) -> None:
        if player.loaded_at is None:
            player.loaded_at = now
            log.info("[game] %s: %s loaded the map", self.label(), player.name)

    def _next_body_id(self, player: Player) -> int | None:
        """Walk the player's ID ring, excluding bodies any connected peer still retains."""
        clients = [viewer.client for viewer in self.players if viewer.client is not None]
        suffix = player.body & 0xFF
        for offset in range(254):
            entity = player.entity | ((suffix + offset) % 255 + 1)
            if all(client.body_id_available(entity) for client in clients):
                return entity
        return None

    def switch_hero(
        self, player: Player, hero: Hero, *, preserve_pose: bool = False, start_frame: int | None = None,
        preserve_ultimate: bool = False,
    ) -> bool:
        """The body of a picked hero. The first one just comes. A switch creates the new body under a
        new id at the spawn point, moves the player's components 44 and 75 to it and destroys the old
        body. These records can share a datagram in that order. When the packet budget splits them,
        the later records wait for acknowledgement of their prerequisites."""
        record = graph.bodies().get(hero.body) or graph.entity_body(hero.body)
        if record is not None and record.get("hero") not in (None, f"0x{hero.guid:016X}"):
            return False
        canonical = next((body for body, row in graph.bodies().items()
                          if int(row["hero"], 16) == hero.guid), None)
        if canonical is not None and canonical != hero.body:
            if record is None or defined_mover_data(hero.body) is None:
                return False
            try:
                pools.active_pools(hero.guid, hero.body)
            except ValueError:
                return False
        pose = None
        if preserve_pose and player.has_body:
            mover = player.mover
            pose = (tuple(mover.position), mover.yaw, mover.pitch,
                    mover.velocity, mover.flags_word, mover.air_ticks)
        old = player.body if player.has_body else None
        from ow174.game import hero_torbjorn, lucio, torbjorn_charge

        if old is not None:
            self.combat.junkrat.charge.prepare_replacement(player, hero)
        old_hero = player.hero.guid if old is not None else None
        ult_fraction = 0.0
        overload_cooldown = turret_cooldown = 0.0
        retained_torb_charge = passive_torb_due = None
        charge_ratio = orisa_charge.replacement_ratio(player, hero.guid) if old is not None else None
        if old is not None:
            entity = self._next_body_id(player)
            if entity is None:
                return False
            if old_hero != hero.guid or hero.guid != 0x02E0000000000007:
                player.retained_ult_charge = None
            elif (script := player.body_script) is not None and script.entity == old and not script.retired:
                slot = script.component.entity_var(342, create=False)
                if slot is not None:
                    player.retained_ult_charge = max(0.0, expr.to_float(slot.value()))
            # Reconstructed source policy for the authored 14F2 receiver.
            # The old script stays live until the replacement ID is secured.
            if old_hero == hero.guid == GENJI and player.body_script is not None:
                ult_fraction = player.body_script.ult_fraction()
            if old_hero == hero.guid == lucio.HERO:
                charge_ratio = lucio.replacement_charge_ratio(player.body_script, hero.guid, entity)
            if old_hero == hero.guid == hero_torbjorn.HERO and player.body_script is not None:
                if torbjorn_charge.enabled(player.body_script):
                    retained_torb_charge = expr.to_float(player.body_script.component.entity_var(342).value())
                    passive_torb_due = torbjorn_charge.passive_timer_due(player.body_script)
            if old_hero == hero_torbjorn.HERO:
                overload_cooldown = hero_torbjorn.overload_cooldown_remaining(player)
                turret_cooldown = hero_torbjorn.turret_cooldown_remaining(player)
                if hero.guid != old_hero:
                    player.overload_cooldown_until = self.combat.now + overload_cooldown
                    player.turret_cooldown_until = self.combat.now + turret_cooldown
            elif hero.guid == hero_torbjorn.HERO:
                overload_cooldown = max(0.0, player.overload_cooldown_until - self.combat.now)
                turret_cooldown = max(0.0, player.turret_cooldown_until - self.combat.now)
            if player.hero.guid == 0x02E000000000012E:
                self.combat.sombra_charge.prepare_retirement(
                    player, preserve_ultimate and player.hero.guid == hero.guid)
            self.combat.retire_player_body(player, next_hero=hero.guid)
            if old_hero == 0x02E0000000000007:
                self.stop_body_script(player)
        player.hero = hero
        if old is not None:
            player.new_body(entity)
            self.combat.record_replacement(entity)
        if pose is not None:
            mover = player.mover
            mover.use(mover_data(hero.body))
            mover.position = list(pose[0])
            mover.yaw, mover.pitch, mover.velocity, mover.flags_word, mover.air_ticks = pose[1:]
        player.skin = self.skin_of(player.account_lo, hero.guid)
        player.has_body = True
        if player.client is not None and player.spawned:
            components = self._possession(player)
            player.client.queue_entities(self.own_body_updates(player, start_frame=start_frame, ult_fraction=ult_fraction,
                                                                   charge_ratio=charge_ratio))
            if hero.guid == hero_torbjorn.HERO and overload_cooldown > 0:
                hero_torbjorn.set_overload_cooldown(player, overload_cooldown)
            if hero.guid == hero_torbjorn.HERO and turret_cooldown > 0:
                hero_torbjorn.set_turret_cooldown(player, turret_cooldown)
            if retained_torb_charge is not None and player.body_script is not None:
                script = player.body_script
                cost = expr.to_float(script.component.entity_var(338).value())
                script.set_ult_charge(min(cost, max(0.0, retained_torb_charge)))
                if passive_torb_due is not None:
                    script.world.passive_charge_due = passive_torb_due
            player.client.queue_possession(
                EntityUpdate(player.entity, OP_UPDATE, lambda origin: world.update(origin, components)),
                player.body,
            )
            if old is not None:
                player.client.queue_entities([EntityUpdate(old, OP_DESTROY)])
        if old is not None:
            self.destroy_for_others(player, old)
        self.notices.hero_switched(player, old_hero)
        theme, golden = player.skin
        look = f", skin theme {theme:X}{' + golden weapon' if golden else ''}" if theme else ""
        log.info("[game] %s: %s plays %s%s", self.label(), player.name, hero.name, look)
        return True

    def leave(self, player: Player, reason: str = "left") -> None:
        """The player's game left the match, or never came: the lobby takes the player back (the match
        chat goes), and the match ends with its last player."""
        if player.gone:
            return
        self.combat.junkrat.charge.forget(player)
        self.notices.left(player)
        player.gone = True
        player.retained_ult_charge = None
        player.client = None
        player.spawned = False
        self.stop_body_script(player)
        if player.controller_vm is not None:
            player.controller_vm.retire()
        if player.has_body:
            self.combat.retire_player_body(player)
            self.destroy_for_others(player, player.body)
        log.info("[game] %s: %s %s", self.label(), player.name, reason)
        if self.on_leave is not None:
            self.on_leave(player)
        if all(other.gone for other in self.players):
            if self.scene_host is not None:
                self.scene_host.close()
            self.ended = True

    @property
    def waiting_for_players(self) -> bool:
        """Before hero select counts down: the players are still joining."""
        return self.assemble_ends is None

    def free_places(self) -> list[int]:
        """Places left per team: the mode's team sizes less the players still in the match (one entry
        for free for all)."""
        playing = [player for player in self.players if not player.gone]
        sizes = self.game_map.team_sizes
        if self.game_map.free_for_all:
            return [sizes[0] - len(playing)]
        return [size - sum(1 for player in playing if player.team == team) for team, size in enumerate(sizes)]

    def roles_taken(self) -> list[dict[int, int]]:
        """Per team, how many players still in the match play each role queue role."""
        taken: list[dict[int, int]] = [{} for _ in self.game_map.team_sizes]
        for player in self.players:
            if not player.gone and player.role and player.team < len(taken):
                taken[player.team][player.role] = taken[player.team].get(player.role, 0) + 1
        return taken

    def snapshot(self) -> dict:
        result = {
            "id": self.label(),
            "map": self.game_map.name,
            "started": self.started,
            "collision": {"state": self.collision_state.status, "error": self.collision_state.error},
            "players": [
                {
                    "name": player.name,
                    "hero": player.hero.name,
                    "team": player.team + 1,
                    "state": "playing" if player.spawned else ("loading" if player.client else "waiting"),
                }
                for player in self.players
            ],
        }
        if self.scene_host_factory is not None:
            result['scene_script'] = {
                'started': self.scene_host_started, 'error': self.scene_host_error,
                'host': None if self.scene_host is None else self.scene_host.snapshot(),
            }
        return result


def pong(ping: dict, received_ns: int, *, environment: ExecutionEnvironment | None = None) -> dict:
    """20306 for a 21610 ping: its index and stamps, plus when we got it and when we answer."""
    environment = SystemEnvironment() if environment is None else environment
    stamps = [*ping.get("+0x80", []), received_ns, environment.wall_time_ns(callsite="match.pong.sent_ns")]
    return {"+0x78": ping.get("+0x78", 0), "+0x80": stamps}
