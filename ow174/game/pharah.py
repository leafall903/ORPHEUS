"""Static combat adapters for Pharah's authored ability graphs."""

import math
from dataclasses import dataclass
from itertools import pairwise

from ow174.game.commands import Command
from ow174.game.mover import FELL
from ow174.game.script import expr, nodes

HERO = 0x02E0000000000008
HOVER_JETS = 0x0025
HOVER_BUTTONS = (11, 29)
JUMP_JET = 0x0027
CONCUSSIVE = (0x007B, 2)
CONCUSSIVE_INPUT = 0x10
CONCUSSIVE_BUTTON = 36
CONCUSSIVE_ENTITY = 0x040000000000010F
PRIMARY = (0x0023, 8)
PRIMARY_LOADOUT = 0x0B9000000000000F
BARRAGE = 0x002B
BARRAGE_BUTTON = 37
BARRAGE_ENTITY = 0x0400000000000052
BARRAGE_LOADOUT = 0x0B9000000000000B


def _owner(world, instance):
    component = instance.component
    match = getattr(world, "match", None)
    if (component.strict or match is None or match.ended or world.hero != HERO
            or instance.graph.index != JUMP_JET or world.body != component.entity or component.owner_dead):
        return None
    return next((player for player in match.players
                 if match._shown(player) and player.body == world.body and player.body_script is not None
                 and player.body_script.component is component), None)


def native(world, cfg, instance):
    if cfg.get("$") != "STU_C8C4C20D" or instance is None:
        return NotImplemented
    owner = _owner(world, instance)
    if owner is None or expr.to_entity(instance.evaluate(cfg.get("m_entity"))) != world.body:
        return NotImplemented
    return expr.f32(owner.mover.yaw * 360.0 / 65536.0)


def jump_jet(instance, node):
    owner = _owner(instance.component.world, instance)
    mods = node.field("m_BC5E91CF") or ()
    if owner is None or len(mods) != 2:
        return False
    reset, impulse = mods
    if (reset.get("$") != "STU_9AF0FA80" or reset.get("m_A9561CA0") != 2
            or impulse.get("$") != "STU_4635C5E2" or impulse.get("m_CFEC5F76") != 1):
        return False
    vertical = instance.evaluate(reset.get("m_9B7A63EA"))
    vector = instance.evaluate(impulse.get("m_9B7A63EA"))
    if type(vertical) not in (int, float) or not isinstance(vector, expr.Vec3):
        return False
    mover = owner.mover
    vx, _, vz = mover.velocity
    mover.velocity = tuple(expr.f32(v) for v in (vx + vector.x, vertical + vector.y, vz + vector.z))
    mover.fall = 0.0
    mover.flags_word |= FELL
    mover.air_ticks = 0
    return True


def hover_instance(script):
    if script is None or script.hero != HERO or script.retired or script.component.owner_dead:
        return None
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == HOVER_JETS), None)


def hover_force(script):
    instance = hover_instance(script)
    if instance is None:
        return None
    return next((state for state in instance.states.values()
                 if state.active and not state.ending and state.node.cls == "STU_A9C4FF83"), None)


def hover_recovering(script):
    instance = hover_instance(script)
    if instance is None:
        return False
    fuel, capacity = instance.vars.get(87), instance.vars.get(971)
    if fuel is None or capacity is None:
        return False
    return expr.to_float(fuel.value()) < expr.to_float(capacity.value())


def hover_thrust(player, seconds):
    """Integrate the active 0025 force before ordinary collision and gravity."""
    hover = hover_force(player.body_script)
    if hover is None:
        return
    force = hover.evaluate(hover.node.field("m_16882D22"), watch=False)
    limit = hover.evaluate(hover.node.field("m_B74245E8"), watch=False)
    if (not isinstance(force, expr.Vec3) or force.x or force.z or not math.isfinite(force.y)
            or force.y <= 0 or type(limit) not in (int, float) or not math.isfinite(limit) or limit < 0):
        return
    mover = player.mover
    vx, vy, vz = mover.velocity
    if vy < limit:
        vy = min(limit, expr.f32(vy + expr.f32(force.y * seconds)))
        mover.velocity = (vx, vy, vz)
    if vy > 0:
        mover.flags_word |= FELL
        mover.fall = 0.0


def silence_hover(component):
    for instance in tuple(component.instances.values()):
        if instance.graph.index != HOVER_JETS:
            continue
        for state in tuple(instance.states.values()):
            if isinstance(state, nodes.LogicalButton) and state.active and state.down:
                state.go_up()
            elif isinstance(state, nodes.Ability):
                state.presses.clear()
                if state.flags & nodes.Ability.RUNNING:
                    state.stop()


@dataclass(frozen=True)
class Concussion:
    radius: float
    horizontal: float
    minimum_up: float
    owner_scale: float


def concussion(shooter, state):
    if shooter.hero.guid != HERO or (state.instance.graph.index, state.node.pos) != CONCUSSIVE:
        return None
    blast = state.node.field("m_D2D11CE9") or {}
    modify = blast.get("m_modifyHealth") or {}
    rings = modify.get("m_knockbackRings") or ()
    if len(rings) != 1:
        return None
    ring, = rings

    def value(row, name):
        return expr.to_float(state.evaluate(row.get(name), watch=False))

    radius = value(ring, "m_radius")
    horizontal = value(modify, "m_E33EFEE1") * value(ring, "m_579D7CFF")
    minimum_up = value(modify, "m_583C208B") if value(ring, "m_10399AE7") > 0 else 0.
    owner_scale = value(blast, "m_2FE97560") if blast.get("m_49F60FB8") else 0.
    values = (radius, horizontal, minimum_up, owner_scale)
    if all(math.isfinite(v) and v >= 0 for v in values) and radius > 0:
        return Concussion(*values)
    return None


def barrage_channel(component):
    from ow174.game.script import graph

    return next((state for instance in component.instances.values()
                 if instance.graph.index == BARRAGE
                 for state in instance.states.values()
                 if state.active and not state.ending and state.node.cls == "STUStatescriptStateStack"
                 and expr.lvalue(state.node.field("m_out_Var")) == (graph.ENTITY, 33)), None)


def interrupt_barrage(component):
    channel = barrage_channel(component)
    if channel is not None:
        channel.end(False)


@dataclass(frozen=True)
class RocketImpulse:
    horizontal: float
    vertical: float
    owner_scale: float
    rings: tuple

    def scales(self, distance):
        first = self.rings[0]
        if distance < first[0]:
            return first[1:]
        for near, far in pairwise(self.rings):
            if distance < far[0]:
                ratio = (distance - near[0]) / max(0.0001, far[0] - near[0])
                return tuple(a + (b - a) * ratio for a, b in zip(near[1:], far[1:], strict=True))
        return 0., 0.


def primary_impulse(state):
    blast = state.node.field("m_D2D11CE9") or {}
    modify = blast.get("m_modifyHealth") or {}

    def value(row, name):
        return expr.to_float(state.evaluate(row.get(name), watch=False))

    rings = tuple(sorted(
        tuple(value(ring, key) for key in ("m_radius", "m_579D7CFF", "m_10399AE7"))
        for ring in (modify.get("m_knockbackRings") or ())
    ))
    if not rings:
        return None
    owner = value(blast, "m_2FE97560") if blast.get("m_49F60FB8") else 0.
    return RocketImpulse(value(modify, "m_E33EFEE1"), value(modify, "m_10399AE7"), owner, rings)


def source_impulse_geometry(target, impact, normal):
    """Use the upright mover anchor and raised nearest point of D6D6C6."""
    x, y, z = target.feet
    half = target.height * .5
    radius = target.radius
    center = (x, y + half, z)
    distance = max(0., math.dist(center, impact) - radius)
    lower, upper = center[1] - half / 3., center[1] + half
    nearest = max(lower, min(upper, impact[1]))
    if math.dist((x, nearest, z), impact) < radius:
        dx, dz = impact[0] - x, impact[2] - z
        length = math.hypot(dx, dz)
        escape = (dx / length, 0., dz / length) if length > 1e-9 else normal
        impact = tuple(a + b * radius for a, b in zip((x, impact[1], z), escape, strict=True))
        nearest = max(lower, min(upper, impact[1]))
    delta = tuple(a - b for a, b in zip((x, nearest + .2, z), impact, strict=True))
    length = math.hypot(*delta)
    direction = tuple(axis / length for axis in delta) if length > 1e-9 else (0., 0., 0.)
    return distance, direction


def accepted_volley(state):
    volley = state.instance.graph.index, state.node.pos
    return volley in (CONCUSSIVE, (BARRAGE, 2))


def pending_release(script):
    if script is None or script.hero != HERO or script.retired or script.component.owner_dead:
        return False
    component = script.component
    if barrage_channel(component) is not None:
        return True
    for instance in component.instances.values():
        if instance.graph.index == CONCUSSIVE[0]:
            # 007B/4 is the accepted windup before volley node 2.
            windup = instance.states.get(1)
            if windup is not None and windup.node.pos == 4 and windup.active:
                return True
            # Keep a real press buffered by the ability while its cooldown expires.
            for state in instance.states.values():
                if (isinstance(state, nodes.Ability) and state.node.pos == 14 and state.active
                        and state.take_press(CONCUSSIVE_BUTTON, retain=True)):
                    return True
    return False


def replay_until(fight, previous, dt):
    from ow174.game import knockback

    flight_end = max(
        (r.last_tick + math.ceil(r.left / dt) + knockback.REPLAY_LIMIT
         for r in fight.rockets if r.last_tick is not None
         and (r.pharah_primary or r.definition in (CONCUSSIVE_ENTITY, BARRAGE_ENTITY))),
        default=previous,
    )
    if any(fight.match._shown(p) and pending_release(p.body_script) for p in fight.match.players):
        # Both the accepted 2.5 s channel and the shorter concussion windup
        # finish within three seconds. Include the projectile and impulse tails.
        flight_end = max(flight_end, previous + math.ceil(8 / dt) + knockback.REPLAY_LIMIT)
    if fight.pharah_airborne:
        flight_end = max(flight_end, previous + math.ceil(8 / dt))
    elif any(fight.match._shown(p) and hover_recovering(p.body_script) for p in fight.match.players):
        flight_end = max(flight_end, previous + math.ceil(4 / dt))
    return flight_end


def replay(fight, tick, dt):
    from ow174.game.projectiles import MAX_LEASE_FRAME

    match = fight.match
    if match.ended or tick > MAX_LEASE_FRAME:
        return
    scheduled = match.tick
    try:
        match.tick = tick
        for player in match.players:
            script = player.body_script
            airborne = player.body in fight.pharah_airborne
            if (not match._shown(player) or script is None or script.frame >= tick or fight.is_dead(player)
                    or not (airborne or pending_release(script) or hover_recovering(script))):
                continue
            mover = player.mover
            command = Command(tick, yaw=mover.yaw, pitch=mover.pitch)
            if airborne:
                mover.last_command = command
                player._step_body_pose(command, dt, tick, replay_accepted=True)
                history = fight.history.get(player.body)
                if history and history[-1][0] >= tick:
                    at, _, height, eye = history[-1]
                    history[-1] = at, tuple(mover.position), height, eye
            else:
                player._advance_body_frame(command, posed=False, replay_accepted=True)
    finally:
        match.tick = scheduled
