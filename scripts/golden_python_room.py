"""Observe one prepared Soldier room against the frozen Python reference.

The caller supplies decoded commands and drives one production room tick per call.
The production owner delivery path sends to memory and acknowledges committed groups.
"""

import inspect
import platform
import random
from unittest.mock import patch

from golden_python_delivery import MemorySink, OwnerDelivery, bits
from ow174.game import bots, collision, content, world
from ow174.game.commands import Command
from ow174.game.script import expr
from ow174.game.script.driver import OWNER_HEALTH
from ow174.game.script.nodes import WeaponVolley
from ow174.game.script.runtime import Component
from ow174.game.server import GameServer
from tests.runtime.test_game_server import pick
from tests.runtime.test_server_journal import FixedSource


class RoomClock(FixedSource):
    """Keep the fixture's deterministic entropy and advance all clocks with the schedule."""

    elapsed_us = 0

    def wall_time(self, *, callsite):
        return super().wall_time(callsite=callsite) + self.elapsed_us / 1_000_000

    def monotonic(self, *, callsite):
        return super().monotonic(callsite=callsite) + self.elapsed_us / 1_000_000

    def wall_time_ns(self, *, callsite):
        return super().wall_time_ns(callsite=callsite) + self.elapsed_us * 1000


def rifle_ammo(rifle):
    """Read stored ammo or its known pure volley binding without evaluating a VM graph."""
    var = rifle.vars.get(53)
    if var is None:
        return None
    active = [link for link in var.links if link.state.active]
    if not active:
        return var.base if type(var.base) is int else None
    if len(active) == 1 and type(active[0].state) is WeaponVolley and active[0].slot == 0:
        volley = active[0].state
        return volley.ammo_at(volley.now - volley.start - volley.offset)
    return None


def rifle_reloading(rifle):
    """The authored rifle tests !#51 and writes only booleans during reload."""
    var = rifle.vars.get(51)
    if var is None or any(link.state.active for link in var.links):
        return None
    # An existing Null is the initial stored value; the graph's boolean
    # expression treats it as false before the first reload begins.
    return expr.truthy(var.base) if var.base is None or type(var.base) is bool else None


def select_stock_body(match, player, now):
    """Observe the actual VM seed at construction without drawing from its random stream."""
    initialize = Component.__init__
    parameters = inspect.signature(initialize)
    seeds = {}

    def observe(component, *args, **kwargs):
        arguments = parameters.bind(component, *args, **kwargs)
        arguments.apply_defaults()
        initialize(component, *args, **kwargs)
        seeds[component] = arguments.arguments["seed"]

    with patch.object(Component, "__init__", observe):
        match.game_message(player, pick(player.entity, player.hero.guid), now)
        if player.body_script is None:
            raise ValueError("stock Soldier failed to initialize")
    component = player.body_script.component
    if (component not in seeds or type(seeds[component]) is not int
            or type(component.rng) is not random.Random or platform.python_implementation() != "CPython"):
        raise ValueError("unrecognized stock player VM random stream")
    return seeds[component]


class PythonRoom:
    """A single stock Soldier and an identified Practice Range spawner, after selection."""

    def __init__(self, bot_slot):
        scene = content.PRACTICE_RANGE
        terrain = collision.world_for(scene.map_guid, scene.name, build_missing=False, mode=scene.mode_guid)
        if terrain is None:
            raise ValueError("prepared Practice Range collision is unavailable")
        provider = collision.StaticCollisionProvider(terrain)
        self.clock = RoomClock()
        self.seed = self.clock.counter
        host = GameServer(environment=self.clock, collision_provider_factory=lambda: provider)
        host.sock = MemorySink()
        tickets = host.create_match(scene, [(1, "player", content.SOLDIER, 0, False)])
        if not tickets:
            raise ValueError("stock Practice Range room could not be created")
        self.host = host
        self.match, = host.matches
        self.player, = self.match.players
        self.start_tick = host.tick
        self.delivery = OwnerDelivery.join(host, tickets[0], self.match.started)
        if not 0 <= bot_slot < len(self.match.bots) or self.match.bots[bot_slot].team == self.player.team:
            raise ValueError("select a hostile Practice Range bot slot")
        self.spawner = self.match.bots[bot_slot].spawner
        self.bot = self.match.bots[bot_slot]
        self.roster_size = len(self.match.bots)
        self.spawner_identity = {
            "team": self.spawner["team"],
            "hero": f"{int(self.spawner['hero'], 16):016x}",
            "position_bits": [bits(value) for value in self.spawner["position"]],
            "yaw": bots.yaw_of(self.spawner["rotation"]),
        }
        self.bot_generation = 1
        self.identities = {self.bot.entity: "bot/0/body/1"}
        self.startup_acks = {}
        self.bootstrap_effects = []
        self._load()
        self.setup_delivery_ticks = self.match.tick - self.start_tick
        self.setup_elapsed_us = self.clock.elapsed_us
        self.player_script = self.player.body_script
        self.player_entity = self.player.body
        self.player_generation = 1
        self.ambiguous_player_body = False
        self.owner_effects = None

    def _load(self):
        m, player = self.match, self.player
        self.vm_seed = None
        map_loaded = False

        def confirmed(records, possession, script):
            for update in records:
                if update.entity == self.bot.entity and update.op == world.OP_CREATE:
                    if self.delivery.bot_create_acked(self.bot.entity):
                        self.startup_acks.setdefault("bot_create", m.tick)
                if player.has_body and update.entity == player.body and update.op == world.OP_CREATE:
                    if player.body_script.create_arrived:
                        self.startup_acks.setdefault("owner_create", m.tick)
                if (possession and update is possession[1] and possession[0] == player.body
                        and player.entity not in self.delivery._pending_possessions):
                    self.startup_acks.setdefault("owner_possession", m.tick)
                for name, item in zip(("owner_full_tree", "owner_first_delta"), script):
                    if update is item and player.body_script.stream.base_last >= update.chunk_last:
                        self.startup_acks.setdefault(name, m.tick)

        for _ in range(256):
            now = self._tick_time()
            previous = self.delivery.seq
            self.host._tick(now)
            instance_sent = not map_loaded and any(
                index == 0 for index, _ in self.delivery.in_flight.get(previous, ((), ()))[0]
            )
            effects, gaps = self.delivery.received_frame(previous, now, self.identities, confirmed)
            if (player.steps_done == 4 and self.delivery.controller_ready
                    and player.script.delivered and self.vm_seed is None):
                self.vm_seed = select_stock_body(m, player, now)
                self.component_start_frame = player.body_script.frame
                self.identities[player.body] = "player/0/body/1"
            if instance_sent:
                m.map_loaded(player, now)
                map_loaded = True
            if gaps:
                raise ValueError(f"unobserved owner delivery during loading: {sorted(gaps)}")
            self.bootstrap_effects.extend(effects)
            if self.startup_acks.keys() >= {"bot_create", "owner_create", "owner_possession"}:
                if player.commands.last_frame is not None:
                    raise ValueError("gameplay input processed during loading")
                return
        raise ValueError(f"room readiness timed out; observed receipts: {sorted(self.startup_acks)}")

    def _tick_time(self):
        self.clock.elapsed_us = (self.host.tick - self.start_tick) * self.player.quanta
        return self.match.started + self.clock.elapsed_us / 1_000_000

    def _remember(self, entity, actor):
        if self.identities.setdefault(entity, actor) != actor:
            self.identities[entity] = None

    def step(self, command):
        """Take at most one semantic submission before one production room update."""
        now = self._tick_time()
        before = self.player.commands.last_frame
        boundary = None
        if command is not None:
            if not isinstance(command, Command):
                raise ValueError("submit a decoded reference Command")
            if not self.player.spawned or self.player.gone or not self.player.has_body:
                raise ValueError("command target body is absent")
            boundary = self.player.take_commands([command])
        after = self.player.commands.last_frame
        accepted = command is not None and boundary is None and before != after and after == command.frame
        previous = self.delivery.seq
        self.host._tick(now)
        state, missing = self.observe()
        self.owner_effects, delivery_gaps = self.delivery.received_frame(previous, now, self.identities)
        return accepted, (state, sorted(set(missing) | delivery_gaps))

    def observe(self):
        """Return available comparator state rows and explicit observation gaps."""
        rows = []
        missing = set()
        p = self.player
        body = p.body_script
        if not p.gone and p.has_body and body is None:
            missing.add("player.body.vm")
        if p.has_body and not p.gone and body is not None:
            if p.body != self.player_entity or self.player_script is None:
                self.player_generation += 1
                self.ambiguous_player_body = False
            elif self.player_script is not None and body is not self.player_script:
                self.ambiguous_player_body = True
            if self.ambiguous_player_body:
                missing.add("player.body.lifecycle")
            self.player_entity, self.player_script = p.body, body
            key = f"player/0/body/{self.player_generation}"
            self._remember(p.body, key)
            rows.extend([
                ["player/0", "body", key],
                [key, "accepted_frame", p.commands.last_frame],
                [key, "aim", [p.mover.yaw, p.mover.pitch]],
                [key, "alive", not self.match.combat.is_dead(p)],
                [key, "position_bits", [bits(n) for n in p.mover.position]],
            ])
            vital = self.match.combat.vitals.get(p.body)
            shared_health = body.component.vars.get(OWNER_HEALTH)
            if vital is not None:
                rows.append([key, "health_bits", bits(vital.total)])
            elif (shared_health is not None and not shared_health.links
                  and type(shared_health.base) in (int, float)):
                rows.append([key, "health_bits", bits(shared_health.base)])
            else:
                missing.add("player.health")
            rifles = [
                instance for instance in body.component.instances.values()
                if instance.graph.index == 0x0254 and instance.parent is None
            ]
            ammo = rifle_ammo(rifles[0]) if len(rifles) == 1 else None
            reloading = rifle_reloading(rifles[0]) if len(rifles) == 1 else None
            if ammo is None:
                missing.add("player.ammo")
            else:
                rows.append([key, "ammo", ammo])
            if reloading is None:
                missing.add("player.reloading")
            else:
                rows.append([key, "reloading", reloading])
        elif p.gone or not p.has_body:
            rows.append(["player/0", "body", None])
            self.player_script = None
        matches = [bot for bot in self.match.bots if bot.spawner is self.spawner]
        if len(matches) > 1:
            missing.add("bot.body.lifecycle")
        elif not matches:
            rows.append(["bot/0", "body", None])
            self.bot = None
        else:
            bot = matches[0]
            if bot is not self.bot:
                self.bot_generation += 1
                self.bot = bot
            key = f"bot/0/body/{self.bot_generation}"
            self._remember(bot.entity, key)
            vital = self.match.combat.vitals.get(bot.entity)
            rows.extend([
                ["bot/0", "body", key],
                [key, "alive", not bot.dead],
                [key, "position_bits", [bits(n) for n in bot.position]],
            ])
            if vital is not None:
                rows.append([key, "health_bits", bits(vital.total)])
            elif not bot.dead:
                # Combat.body_vitals lazily initializes the bot's sole pool from this field.
                rows.append([key, "health_bits", bits(bot.health)])
            else:
                missing.add("bot.health")
        return sorted(rows), sorted(missing)
