"""Ashe's reconstructed dynamite and companion lifecycles, scoped to build 104319.

Resource evidence: entity2533 -> graph1502; nodes17..23 define 50/20 blast,
1.5/5 metre rings, 100 burn over 5 seconds. Node33 defines a 2-second fuse
times an owner modifier; node31/32 and node21 halve the owner's damage.
The static server reads the damage tags when the entity graphs are mounted. The 14FD throw
volley authors projectile motion value 8, used here as downward acceleration.
The throw's 1FFD8312 scalar is used as its world sweep radius. The ordinary
bounce response follows native D77B80 with the 14FD/18 authored coefficients.
Integration, the shot target radius, burn sampling and shot-to-message delivery
remain reconstruction.
This module does not claim native aura/filter/physics or client acceptance.
Companion body/gun/phase movers are loaded from a base-bound extension. Physical
charge uses the authored mover profiles; contacts, launch velocity, settle timing
and target selection are reconstructed. The actual gun runs its authored input,
volley and rate-wait graph. Native controls and full ultimate fidelity remain open.
"""

import math
import os
from dataclasses import dataclass

TICK = 0.016
FUSE = 2.0
RADIUS = 0.12
WORLD_RADIUS = 0.1
GRAVITY = 8.0
BOUNCE_NORMAL = 0.2
BOUNCE_SIDEWAYS = 0.2
BOUNCE_UPHILL = 0.2
BLAST_RADIUS = 5.0
INNER_RADIUS = 1.5
BLAST_MAX, BLAST_MIN = 50.0, 20.0
BURN_SECONDS, BURN_TOTAL = 5.0, 100.0
BURN_STEP = 0.25
BURN_END_FLAGS = (6604, 8440, 530, 531, 478)  # 150F/node2
LOADOUT = 0x0B9000000000020B


def coach_shove_direction(limiter, shooter):
    from ow174.game.combat import aim

    _, up = limiter["m_32B00625"]["m_expression"]["m_D99EF254"]
    _, basis_up = limiter["m_3661BC45"]["m_expression"]["m_D99EF254"]
    limit = math.radians(limiter["m_E26E5E09"]["m_value"])
    x, y, z = aim(shooter.mover.yaw, shooter.mover.pitch)
    yaw = math.atan2(x, z)
    basis = math.atan(basis_up)
    elevation = math.atan2(y + up, math.hypot(x, z))
    elevation = max(basis - limit, min(elevation, basis + limit))
    horizontal = math.cos(elevation)
    return math.sin(yaw) * horizontal, math.sin(elevation), math.cos(yaw) * horizontal


def viper_launch(state, elapsed: int, counter: int) -> tuple[float, int] | None:
    """Capture the cone and reconstructed seed before the volley State can be reused."""
    if state.instance.graph.index != 0x14FC or state.node.pos not in (14, 220):
        return None
    launch = state.node.field("m_5EC1EB38")
    if (not isinstance(launch, dict) or launch.get("$") != "STU_239B571E"
            or state.spread_ramp is None):
        return None
    from ow174.game import weapon_spread
    from ow174.game.script import expr

    low, high = (expr.f32(expr.to_float(state.evaluate(launch.get(name), watch=False)))
                 for name in ("m_7F0FED02", "m_429A6C85"))
    normalized = state.spread_at(elapsed)
    degrees = expr.f32(expr.f32(expr.f32(high - low) * normalized) + low)
    cone = expr.f32(degrees * expr.f32(math.pi / 180))
    # The native key-49 factory initializes the owner word to zero.
    # Static Viper uses that default until the native mutation path is modeled.
    base = weapon_spread.seed_base(0, state.instance.id, state.index, counter)
    return cone, base


def viper_direction(forward, number: int, launch: tuple[float, int]):
    from ow174.game import weapon_spread

    cone, base = launch
    # Use the server command direction as the native transform input until
    # the animated firing pose is available.
    quantized = weapon_spread.quantized_direction(forward)
    if cone == 0:
        return quantized
    return weapon_spread.direction(quantized, cone, base, number)


def set_viper_reload_stunned(component, stunned: bool) -> None:
    # Bridge the reload consequence of Flashbang's 224E stack without
    # starting the rest of the effect graph.
    from ow174.game.script import expr, graph, nodes, runtime

    slot = component.entity_var(33, create=False)
    if slot is None or not slot.links:
        return
    reload = slot.links[-1].state
    if (not isinstance(reload, nodes.Stack) or not reload.active
            or reload.instance.graph.index != 0x14FC or reload.node.pos != 65):
        return
    if not stunned:
        if reload.under:
            # The empty-magazine condition can keep the reload stack linked.
            reload.stack_changed(slot)
        return
    effect = graph.graph(0x224E)
    stack = effect.node(33) if effect is not None else None
    if (reload.top and stack is not None and expr.lvalue(stack.field("m_out_Var")) == (expr.ENTITY, 33)
            and runtime.would_stack_to_top(slot, expr.stack_priority(stack.field("m_priority")))):
        reload.become_under()


def dynamite_fuse_seconds(volley) -> float:
    from ow174.game.script import expr

    # 1502/33 reads the projectile owner's ruleset once at startup.
    scale = volley.instance.context().ruleset(0x4402)
    duration = expr.f32(FUSE * expr.to_float(scale, 1.0))
    return duration if math.isfinite(duration) and duration >= 0 else FUSE


def _dynamite_tags(index, position):
    from ow174.game.combat import health_tags
    from ow174.game.script import graph

    definition = graph.graph(index) if graph.GRAPHS_PATH.is_file() else None
    node = definition.node(position) if definition is not None else None
    modify = node.field("m_modifyHealth") if node is not None else None
    return health_tags(None, modify) if isinstance(modify, dict) else ()


@dataclass
class Grenade:
    shooter: object
    body: int
    team: int
    position: tuple
    velocity: tuple
    born: int
    fuse: int
    shot: object = None
    bounces: int = 0
    absorb_damage: float = 0.0
    matrix_absorbable: bool | None = None


@dataclass
class Burn:
    shooter: object
    source: int
    target: int
    start: int
    until: int
    next_damage: int
    damage: float
    pulses: int = 0
    tags: tuple[int, ...] = ()


@dataclass
class Bob:
    owner: object
    owner_body: int
    bot: object
    born: int
    ready: int
    until: int
    gun: object
    mover: object
    idle_data: object
    charge_until: int
    last_tick: int
    charge_yaw: int
    gun_seconds: float
    charge_instigator: int
    phase: str = "charge"
    stopped_reason: str | None = None
    uppercut_target: int | None = None
    shots: int = 0
    target: int | None = None


@dataclass
class Impulse:
    vector: tuple
    due_tick: int
    apply_resistance: bool = False
    reset_velocity: bool = False
    victim_source: tuple[int, int] | None = None


@dataclass
class NpcMotion:
    bot: object
    mover: object
    last_tick: int


class Impulses:
    """Authored Ashe requests on the server's existing impulse/mover host."""

    def __init__(self, combat):
        self.combat = combat

    def queue(self, owner, vector, *, apply_resistance=False, reset_velocity=False, victim_source=None):
        from ow174.game.combat import _source_entity
        from ow174.game.mover import Mod, intent, statescript_mods
        from ow174.game.script import expr

        fight = self.combat
        entity = _source_entity(owner)
        target = next((target for target in fight._targets()
                       if target.owner is owner and target.entity == entity), None)
        if target is None or fight.vitals_of(target).dead:
            return False
        if reset_velocity or victim_source is not None:
            raise NotImplementedError("BOB victim action must use its actual body host")
        if apply_resistance:
            script = getattr(owner, "body_script", None)
            rows = statescript_mods(script.component) if script is not None else {}
            mods = sorted((Mod(index, op, value, fight.tick, None, priority, key)
                           for key, (index, op, value, priority) in rows.items() if index == 26), key=Mod.order)
            scale = max(0.0, intent(mods, 26, 1.0))
            vector = tuple(axis * scale for axis in vector)
        return fight.knockbacks.apply(target, expr.Vec3(*vector), impact_frame=fight.tick)

    def recoil(self, player, instance):
        from ow174.game.combat import aim
        from ow174.game.script import expr

        config = instance.graph.node(20).field("m_value")
        magnitude, = config["m_expression"]["m_D99EF254"]
        scale = expr.to_float(instance.evaluate(config["m_configVars"][1]), 1.0)
        if not math.isfinite(scale) or scale <= 0:
            return

        limiter = config["m_configVars"][0]
        _, up = limiter["m_32B00625"]["m_expression"]["m_D99EF254"]
        basis = limiter["m_3661BC45"]["m_configVars"][1]
        basis_elevation = basis["m_C0EDD7EC"]["m_value"]
        pitch_limit = limiter["m_E26E5E09"]["m_value"]
        x, y, z = aim(player.mover.yaw, player.mover.pitch)
        # Both authored inputs have rearward view yaw. Only elevation can be clipped.
        yaw = math.atan2(-x, -z)
        elevation = math.atan2(up - y, math.hypot(x, z))
        elevation = max(math.radians(basis_elevation - pitch_limit),
                        min(elevation, math.radians(basis_elevation + pitch_limit)))
        horizontal = math.cos(elevation)
        speed = magnitude * scale
        self.queue(player, (speed * math.sin(yaw) * horizontal, speed * math.sin(elevation),
                            speed * math.cos(yaw) * horizontal))


    def coach_targets(self, shooter, hits, state, *, reflected):
        from ow174.game import immunity
        from ow174.game.combat import _source_entity
        from ow174.game.script import expr

        modify = state.node.field("m_6396149F.m_modifyHealth")

        def number(config):
            return expr.to_float(state.evaluate(config, watch=False))

        if reflected:
            # The static Deflect ray uses the reflector's aim.
            config = state.instance.graph.node(45).field("m_value")
            limiter, scale = config["m_configVars"]
            magnitude, = config["m_expression"]["m_D99EF254"]
            magnitude *= number(scale)
            direction = expr.Vec3(*(axis * magnitude for axis in coach_shove_direction(limiter, shooter)))
        else:
            direction = state.evaluate(modify["m_1376B4DF"], watch=False)
        if not isinstance(direction, expr.Vec3) or not all(map(math.isfinite, direction)):
            return
        cap = number(modify["m_8394CF8C"])
        if not math.isfinite(cap) or cap <= 0:
            return
        upward = number(modify["m_583C208B"])
        count = number(state.node.field("m_numProjectilesPerShot"))
        near, far = modify["m_knockbackRings"]
        inner, outer = (number(ring["m_radius"]) for ring in (near, far))
        near_x, far_x = (number(ring["m_579D7CFF"]) for ring in (near, far))
        near_y, far_y = (number(ring["m_10399AE7"]) for ring in (near, far))
        if count <= 0 or outer <= inner:
            return
        for target, distance, pellets in hits.values():
            if (self.combat.vitals_of(target).dead or immunity.cryo_active(target.owner, target.entity)
                    or immunity.fortify_active(target.owner, target.entity)):
                continue
            if distance >= outer:
                continue
            blend = max(0.0, (distance - inner) / (outer - inner))
            x_weight = near_x + (far_x - near_x) * blend
            y_weight = near_y + (far_y - near_y) * blend
            if (target.entity == _source_entity(shooter)
                    or target.entity != _source_entity(target.owner)):
                continue
            x, y, z = direction
            vertical = max(y * y_weight, upward) if y_weight > 0 else y * y_weight
            vector = (x * x_weight, vertical, z * x_weight)
            length = math.hypot(*vector)
            if length > 0:
                fraction = min(1.0, cap / length) * pellets / count
                self.queue(target.owner, tuple(axis * fraction for axis in vector), apply_resistance=True)



def _bob_uppercut_launch(actor, target, modify):
    """Evaluate 1537/17 and reconstruct its radial request from the server poses."""
    from ow174.game.combat import aim
    from ow174.game.script import expr

    if modify is None:
        return 0.0, 15.0, 0.0

    class ChargePose(expr.Context):
        def owner(self):
            return expr.Entity(actor.bot.entity)

        def position(self, entity):
            return expr.Vec3(*actor.bot.position) if entity == actor.bot.entity else super().position(entity)

        def native(self, config):
            if (config.get("$") == "STU_021D01CD"
                    and expr.to_entity(expr.evaluate(config.get("m_entity"), self)) == actor.bot.entity):
                return expr.Vec3(*aim(actor.charge_yaw, 0))
            return super().native(config)

    context = ChargePose()
    origin = expr.evaluate(modify.get("m_C0139FA6"), context)
    rings = modify.get("m_knockbackRings") or ()
    if not isinstance(origin, expr.Vec3) or len(rings) != 1:
        return None
    ring, = rings

    def number(config, key):
        return expr.to_float(expr.evaluate(config.get(key), context))

    dx, dy, dz = (value - base for value, base in zip(target.feet, origin, strict=True))
    distance = math.hypot(dx, dy, dz)
    if distance > number(ring, "m_radius"):
        return None
    planar = math.hypot(dx, dz)
    horizontal = number(modify, "m_E33EFEE1") * number(ring, "m_579D7CFF")
    vertical = dy / distance if distance else 0.0
    lift = max(number(modify, "m_583C208B"),
               vertical * number(modify, "m_10399AE7") * number(ring, "m_10399AE7"))
    scale = horizontal / planar if planar else 0.0
    return dx * scale, lift, dz * scale


class Companions:
    """Reconstructed B.O.B. factory/targeting around the actual body and gun graph.

    Charge uses actual1537 movement profiles and120 uppercut damage. Contact
    selection, up15 additive launch, one-second settling and targeting are
    reconstructed. The actual153B gun loop has a ten-second ruleset-scaled life.
    Native controls, collision/force/phase semantics and status fidelity remain open.
    """

    def __init__(self, combat):
        self.combat = combat
        self.active = {}
        self.unavailable_reason = None

    def active_for(self, body):
        return body in self.active

    def actor_for(self, entity):
        return next((actor for actor in self.active.values() if actor.bot.entity == entity), None)

    def summon(self, owner, *, charge_seconds=4.0, gun_seconds=10.0, charge_instigator=None):
        from ow174.game import npc
        from ow174.game.mover import MOVERS_PATH, Mover, load_mover_bundle
        from ow174.game.script import graph

        fight = self.combat
        if owner.hero.guid != 0x02E0000000000200 or owner.body in self.active or fight.is_dead(owner):
            return None
        if graph.entity_body(0x04000000000025C3) is None or graph.graph(0x153B) is None:
            self.unavailable_reason = "missing B.O.B. entity/weapon graph extension"
            return None
        extension = os.environ.get("ORPHEUS_MOVER_EXTENSION")
        profiles = load_mover_bundle(MOVERS_PATH, extension)[1] if extension else {}
        if "bob_charge" not in profiles or "bob_idle" not in profiles:
            self.unavailable_reason = "missing B.O.B. phase mover extension"
            return None
        if any(not math.isfinite(value) or value < 0 for value in (charge_seconds, gun_seconds)):
            self.unavailable_reason = "invalid B.O.B. phase duration"
            return None
        definition = npc.Definition(0x02E000000000021D, 0x04000000000025C3, "B.O.B.", 1200)
        # Deployment offset is reconstructed; native hardpoint/placement is open.
        yaw = owner.mover.yaw * 2 * math.pi / 65536
        x, y, z = owner.mover.position
        bot = npc.spawn(fight.match, definition, (x + math.sin(yaw) * 2, y, z + math.cos(yaw) * 2),
                        team=owner.team, yaw=owner.mover.yaw)
        bot.spawner["manual_spawn"] = False
        bot.spawner["combat_companion"] = True
        bot.combat_owner = owner
        bot.pitch = 0
        script = bot.body_script
        gun = (next((item for item in script.component.instances.values()
                     if item.graph.index == 0x153B), None)
               if script is not None else None)
        if gun is None:
            npc.remove_body(fight.match, bot)
            self.unavailable_reason = "B.O.B. weapon component did not initialize"
            return None
        at = fight.tick
        # The charge/idle profile values come from actual1537/node61/node97.
        # Phase arbitration, contact selection and one-second settle are reconstructed.
        charge_until = at + math.ceil(charge_seconds / TICK)
        ready = charge_until + math.ceil(1.0 / TICK)
        mover = Mover(bot.position, owner.mover.yaw, fight._collision(), profiles["bob_charge"])
        actor = Bob(owner, owner.body, bot, at, ready, ready + math.ceil(gun_seconds / TICK), gun,
                    mover, profiles["bob_idle"], charge_until, at, owner.mover.yaw, gun_seconds,
                    charge_instigator=owner.body if charge_instigator is None else charge_instigator)
        self.active[owner.body] = actor
        bot.impulse_mover = mover
        bot.combat_geometry = (mover.data.stand_height, mover.data.stand_radius, 1.55, True)
        script.component.world.match = fight.match
        # 1536/node11's factory output m_B3019338 is entity variable512;
        # the following actual PlayScript action targets that same slot.
        from ow174.game.script import expr

        owner.body_script.component.set(512, expr.Entity(bot.entity))
        self.unavailable_reason = None
        return actor

    def retire(self, body):
        from ow174.game import npc

        actor = self.active.pop(body, None)
        if actor is not None:
            npc.remove_body(self.combat.match, actor.bot)
            script = actor.owner.body_script
            if script is not None and script.entity == body:
                from ow174.game.script import expr

                slot = script.component.entity_var(512, create=False)
                if slot is not None and expr.to_entity(slot.value()) == actor.bot.entity:
                    script.component.set(512, expr.Entity(0))

    def update(self, tick):
        from ow174.game import immunity

        fight = self.combat
        for body, actor in list(self.active.items()):
            if (actor.bot not in fight.match.bots or actor.bot.dead or tick >= actor.until
                    or actor.owner.body != body or actor.owner.gone):
                self.retire(body)
        if not self.active:
            return

        # Complete each retained pose tick before testing contact at the next.
        actors = tuple(self.active.values())
        first = max(min(actor.last_tick for actor in actors) + 1, tick - 624)
        for at in range(first, tick + 1):
            for actor in actors:
                if at > actor.last_tick and not actor.bot.dead:
                    self._pose(actor, at)
                    actor.last_tick = at

        for actor in actors:
            bot = actor.bot
            if bot.dead:
                self.retire(actor.owner_body)
                continue
            if (fight.is_stunned(bot, tick=tick) or _skill_locked(fight, bot.entity, tick)
                    or immunity.heavy_carry_active(bot, bot.entity)):
                actor.target = None
                bot.firing = False
                script = bot.body_script
                if script is not None:
                    script.component.run_frame(tick, [(20, False)])
                    script.component.shots.clear()
                continue
            if tick < actor.ready:
                continue
            actor.phase = "gun"
            script = bot.body_script
            if script is None:
                self.retire(actor.owner_body)
                continue
            origin = (bot.position[0], bot.position[1] + 1.55, bot.position[2])
            selected = None
            for target in fight._enemies(bot):
                point = (target.feet[0], target.feet[1] + min(1.0, target.height / 2), target.feet[2])
                delta = tuple(point[k] - origin[k] for k in range(3))
                distance = math.sqrt(sum(axis * axis for axis in delta))
                if distance <= 1e-9 or distance > 100:
                    continue
                collision = fight._collision()
                direction = tuple(axis / distance for axis in delta)
                wall = collision.cast_ray(origin, direction, distance)
                ice = fight.walls.cast(origin, direction, distance)
                visible_reach = distance - target.radius
                if ((wall is not None and wall.distance < visible_reach)
                        or (ice is not None and ice[0] < visible_reach)):
                    continue
                if selected is None or distance < selected[0]:
                    selected = (distance, target, delta)
            actor.target = selected[1].entity if selected is not None else None
            bot.firing = selected is not None
            held = 20 in script.component.held
            edges = [(20, bot.firing)] if held != bot.firing else []
            # Use the actual authored LogicalButton -> Stack -> one-shot volley
            # -> 1/rate wait loop. Manually starting each volley would double
            # its cadence because its authored onEnd already starts that wait.
            script.component.run_frame(tick, edges)
            state = actor.gun.states.get(11)
            actor.shots = state.counter if state is not None else 0
            if selected is None:
                script.component.shots.clear()
                continue
            _distance, _target, delta = selected
            bot.yaw = round(math.atan2(delta[0], delta[2]) * 65536 / (2 * math.pi))
            bot.pitch = round(-math.atan2(delta[1], math.hypot(delta[0], delta[2])) * 65536 / (2 * math.pi))

    def _stop_charge(self, actor, tick, reason):
        actor.phase = "settle"
        actor.stopped_reason = reason
        actor.ready = tick + math.ceil(1.0 / TICK)
        actor.until = actor.ready + math.ceil(actor.gun_seconds / TICK)
        actor.mover.use(data=actor.idle_data)

    def _pose(self, actor, tick):
        from ow174.game import bob_victim, heavy_charge, immunity, mei_wall
        from ow174.game.mover import statescript_mods
        from ow174.game.combat import health_tags
        from ow174.game.commands import Command
        from ow174.game.script import expr, graph

        fight, bot, mover = self.combat, actor.bot, actor.mover
        if actor.phase == "charge" and tick >= actor.charge_until:
            self._stop_charge(actor, tick, "timeout")
        before = tuple(mover.position)
        mover.use(world=fight._collision())
        carry = _carry_move(fight, bot)
        charging = actor.phase == "charge" and carry is None
        stunned = fight.is_stunned(bot, tick=tick) or _skill_locked(fight, bot.entity, tick)
        rooted = fight.junkrat.rooted(bot.entity, tick)
        if stunned or rooted:
            mover.velocity = (0.0, mover.velocity[1], 0.0)
        script = bot.body_script
        active = statescript_mods(script.component) if script is not None else {}
        mover.track_mods(fight.movement_mods(bot, active, tick=tick), tick)
        command = Command(tick, forward=127 if charging and not stunned and not rooted else 0,
                          yaw=actor.charge_yaw if charging else round(bot.yaw))
        mover.step_pose(command, TICK, tick, move_to=carry, ground_override=fight.walls.ground(mover))
        fight.walls.move_mover(mover, before)
        bot.position, bot.velocity = tuple(mover.position), tuple(mover.velocity)
        bot.held, bot.last_held = mover.throttles, mover.input_throttles
        bot.input_tick, bot.moved_tick, bot.last_tick = mover.input_tick, tick, tick
        if charging and not stunned and not rooted:
            wall_hit = None
            if fight.walls.pieces:
                contact_graph = graph.graph(0x1537)
                radius = contact_graph.node(166).field("m_radius.m_value")
                delta = tuple(b-a for a,b in zip(before, bot.position, strict=True))
                length = math.hypot(*delta)
                if length:
                    direction = tuple(axis/length for axis in delta)
                    hit = fight.walls.cast((before[0], before[1]+1, before[2]), direction, length, radius=radius)
                    if hit is not None:
                        wall_hit = (tuple(a+d*hit[0] for a,d in zip(before,direction,strict=True)),hit[1])
            target = self._contact(actor, before, wall_hit[0] if wall_hit is not None else bot.position)
            if target is not None:
                other = self.actor_for(target.entity)
                if other is not None and other.phase == "charge" and other.born <= tick < other.charge_until:
                    turn = (actor.charge_yaw - other.charge_yaw + 32768) % 65536 - 32768
                    if abs(turn) > 16384:
                        # 1537/16 rejects the opposing charge's slot 596 without
                        # tag 067D. Both sweeps share one reconstructed stop.
                        self._stop_charge(actor, tick, "opposing_charge")
                        self._stop_charge(other, tick, "opposing_charge")
                        return
                self._stop_charge(actor, tick, "enemy")
                # The static capsule stands in for the native Cryo obstruction.
                if immunity.cryo_active(target.owner, target.entity):
                    return
                charge_graph = graph.graph(0x1537)
                modify = charge_graph.node(17).field("m_modifyHealth") if charge_graph is not None else None
                amount, loadout, tags = 120, 0x0B9000000000020C, ()
                if modify is not None:
                    context = expr.Context()
                    amount = expr.to_float(expr.evaluate(modify["m_amount"], context))
                    asset = expr.evaluate(modify["m_loadout"], context)
                    loadout = asset.guid if isinstance(asset, expr.Asset) else loadout
                    tags = health_tags(None, modify)
                # Route the uppercut into the attached pool at reconstructed body contact.
                actor.uppercut_target = target.entity
                landed = fight._damage(bot, target, amount, False, loadout, tick, world_tick=tick, hit_tags=tags)
                if (landed and not fight.vitals_of(target).dead
                        and not immunity.fortify_active(target.owner, target.entity)):
                    from ow174.game.bots import Bot

                    reset, victim_source = False, None
                    if isinstance(target.owner, Bot):
                        reset = bob_victim.start(
                            fight, bot.entity, target.owner, charge_graph, instigator=actor.charge_instigator,
                        )
                    else:
                        # Start the victim graph on its contact pose, one pose before launch.
                        victim_source = bot.entity, actor.charge_instigator
                    launch = _bob_uppercut_launch(actor, target, modify)
                    if launch is not None or reset or victim_source is not None:
                        fight.impulses.queue(
                            target.owner, launch if launch is not None else (0.0, 0.0, 0.0),
                            apply_resistance=modify is not None, reset_velocity=reset,
                            victim_source=victim_source,
                        )
            elif wall_hit is not None:
                point, piece = wall_hit
                fight.walls.place_mover(mover, point, ())
                mover.velocity = (0.0, mover.velocity[1], 0.0)
                bot.position, bot.velocity = tuple(mover.position), mover.velocity
                self._stop_charge(actor, tick, "ice_wall")
                if piece.impact_marked:
                    amount = contact_graph.node(170).field("m_modifyHealth.m_amount.m_value")
                    fight.walls.damage(piece, bot, amount)
            elif (tick > actor.born + 1 and not mover.airborne
                  and math.hypot(bot.position[0] - before[0], bot.position[2] - before[2]) < 0.01):
                self._stop_charge(actor, tick, "wall")

    def _contact(self, actor, start, end):
        """Swept upright capsule overlap; native collision event/filter remains open."""
        from ow174.game import heavy_charge, immunity
        from ow174.game.combat import ray_capsule

        mover = actor.mover
        delta = tuple(end[k] - start[k] for k in range(3))
        length = math.sqrt(sum(value * value for value in delta))
        direction = tuple(value / length for value in delta) if length > 1e-9 else (0, 0, 1)
        selected = None
        radius = mover.data.stand_radius
        # Two parallel upright capsules reduce to a point against the target's
        # vertical segment expanded by the moving capsule segment and both radii.
        for target in self.combat._enemies(actor.bot):
            # 1537/16 excludes a body in Heavy Assault's carry effect from
            # ordinary uppercut admission.
            if immunity.heavy_carry_active(target.owner, target.entity):
                continue
            a, b = target.segment()
            a = (a[0], a[1] - max(radius, mover.data.stand_height - radius), a[2])
            b = (b[0], b[1] - radius, b[2])
            hit = ray_capsule(start, direction, a, b, radius + target.radius)
            if hit is None or hit > length + 1e-6:
                continue
            if _blocks_bob_uppercut(target.owner, actor.charge_yaw):
                continue
            origin = (start[0], start[1] + min(1.0, mover.data.stand_height / 2), start[2])
            point = (target.feet[0], target.feet[1] + min(1.0, target.height / 2), target.feet[2])
            toward = tuple(point[k] - origin[k] for k in range(3))
            distance = math.sqrt(sum(value * value for value in toward))
            wall = (self.combat._collision().cast_ray(origin, tuple(value / distance for value in toward),
                                                     distance) if distance > 1e-9 else None)
            if wall is not None and wall.distance < distance - target.radius:
                continue
            if selected is None or hit < selected[0]:
                selected = (hit, target)
        return selected[1] if selected is not None else None


def dynamite_bounce_velocity(velocity, normal):
    """Ordinary incoming-contact branch at D77CF5..D78956, in Python precision."""
    normal_speed = sum(axis * push for axis, push in zip(velocity, normal, strict=True))
    if normal_speed >= 0:
        return velocity
    tangent = tuple(axis - normal_speed * push for axis, push in zip(velocity, normal, strict=True))
    # Project world up onto the contact plane to separate along-slope and sideways motion.
    up = (-normal[1] * normal[0], 1 - normal[1] ** 2, -normal[1] * normal[2])
    length = math.hypot(*up)
    up = tuple(axis / length for axis in up) if length else (0, 1, 0)
    up_speed = sum(axis * slope for axis, slope in zip(tangent, up, strict=True))
    sideways_scale = BOUNCE_SIDEWAYS if math.hypot(*tangent) - abs(tangent[1]) > 0.004 else 1.0
    uphill_scale = BOUNCE_UPHILL if tangent[1] > 0 else 1.0
    return tuple(
        (axis - up_speed * slope) * sideways_scale + up_speed * slope * uphill_scale
        - normal_speed * push * BOUNCE_NORMAL
        for axis, slope, push in zip(tangent, up, normal, strict=True)
    )


class Dynamite:
    def __init__(self, combat):
        self.combat = combat
        self.grenades = []
        self.burns = {}
        self.last_tick = 0

    def fired(self, shooter, origin, direction, speed, shot=None, *, fuse_seconds, absorb_damage=0.0):
        tick = self.combat.tick
        self.last_tick = max(self.last_tick, tick)
        grenade = Grenade(shooter, shooter.body, shooter.team, tuple(origin),
                          tuple(speed * axis for axis in direction), tick,
                          tick + math.ceil(fuse_seconds / TICK), shot, absorb_damage=absorb_damage)
        definition = 0x0400000000002533
        grenade.matrix_absorbable = (definition in self.combat.matrix_definitions
            if self.combat.matrix_definitions is not None else self.combat.projectile_tags.admitted(definition))
        self.grenades.append(grenade)
        return grenade

    def retire(self, body):
        active = self.grenades[:]
        self.grenades[:] = [item for item in self.grenades if item.body != body]
        self.burns = {key: item for key, item in self.burns.items()
                      if item.source != body and item.target != body}
        for item in active:
            shot = item.shot
            # Deflect changes the combat body while the delivery keeps the launch body.
            if shot is not None and (item.body == body or shot.body == body):
                item.shot = None
                if shot in self.combat.projectiles.shots:
                    self.combat.projectiles.intercepted(shot, self.combat.tick)

    def ray_hit(self, shooter, origin, direction, reach):
        """Closest owned grenade on the ray, without mutating it."""
        from ow174.game.combat import ray_capsule

        best = None
        for item in self.grenades:
            # 1502/28-29 accepts the hit source only if it matches the owner.
            # Its 0.3 s wait starts with the explosion, after that check.
            if item.shooter is not shooter:
                continue
            distance = ray_capsule(origin, direction, item.position, item.position, RADIUS)
            if distance is not None and distance <= reach and (best is None or distance < best[0]):
                best = (distance, item)
        return best

    def update(self, tick):
        """Replay only bounded-lived active effects; long empty gaps cost no per-tick work."""
        if tick <= self.last_tick:
            return
        fight = self.combat
        saved = fight.now, fight.tick
        try:
            at = self.last_tick + 1
            while at <= tick and (self.grenades or self.burns):
                fight.now, fight.tick = saved[0] - (tick - at) * TICK, at
                for item in list(self.grenades):
                    if at <= item.born:
                        continue
                    self._move(item)
                    if at >= item.fuse:
                        self.detonate(item)
                self._burn(at)
                at += 1
        finally:
            fight.now, fight.tick = saved
            self.last_tick = tick

    def _move(self, item):
        from ow174.game import contacts
        from ow174.game.collision import MOVER
        from ow174.game.combat import aim, ray_capsule

        fight = self.combat
        velocity = (item.velocity[0], item.velocity[1] - GRAVITY * TICK, item.velocity[2])
        speed = math.sqrt(sum(axis * axis for axis in velocity))
        if speed < 1e-9:
            return
        direction = tuple(axis / speed for axis in velocity)
        collision = fight._collision()
        reach = speed * TICK
        end = tuple(pos + axis * TICK for pos, axis in zip(item.position, velocity, strict=True))
        hit = (contacts.scene_sweep(collision.engine, item.position, end, ((0, 0, 0),), WORLD_RADIUS, MOVER,
                                    hit_touching=True)
               if collision is not None else None)
        limit = reach if hit is None else reach * hit[0]
        normal = hit[2] if hit is not None else None
        ice = fight.walls.cast(item.position, direction, limit, radius=WORLD_RADIUS)
        if ice is not None and (normal is None or ice[0] < limit):
            from ow174.game.mei_wall import ray

            face = ray(ice[1], item.position, direction, reach)
            if face is not None and face[1] is not None:
                limit, normal = ice[0], face[1]
        guard_hit = fight._deflector(item.shooter, 0x0400000000002533, item.position,
                                     direction, limit, absorption_admitted=item.matrix_absorbable)
        if guard_hit is not None and normal is not None and guard_hit[0] >= limit:
            guard_hit = None  # original terrain tie precedes a projectile guard.
        if guard_hit is not None:
            limit = guard_hit[0]
        enemies = fight._enemies(item.shooter)
        # D79F00 rebounds admitted entity contacts. Use the static hostile
        # capsules as the query and keep the guard and terrain ties ahead of bodies.
        for target in enemies:
            a, b = target.segment()
            distance = ray_capsule(item.position, direction, a, b, target.radius)
            if distance is None or distance >= limit:
                continue
            x, y, z = (pos + axis * distance for pos, axis in zip(item.position, direction, strict=True))
            offset = (x - a[0], y - min(max(y, a[1]), b[1]), z - a[2])
            length = math.hypot(*offset)
            if length > 0 and sum(axis * part for axis, part in zip(velocity, offset, strict=True)) < 0:
                limit = distance
                normal = tuple(part / length for part in offset)
                guard_hit = None
        if guard_hit is not None:
            from ow174.game import absorption
            from ow174.game.combat import HitDef

            _, target, _, absorbed, guard = guard_hit
            defender = target.owner
            if absorbed:
                self.grenades.remove(item)
                if guard is not None and guard.instance.graph.index == absorption.GRASP:
                    fight.absorption.absorbed(guard, HitDef(item.absorb_damage, None), None, item.body)
                if item.shot is not None:
                    fight.projectiles.intercepted(item.shot, fight.tick)
                return
            item.position = tuple(a + b * limit for a, b in zip(item.position, direction, strict=True))
            item.shooter, item.body, item.team = defender, defender.body, defender.team
            item.velocity = tuple(speed * axis for axis in aim(defender.mover.yaw, defender.mover.pitch))
            return
        if normal is None:
            item.position = end
            item.velocity = velocity
            return
        item.position = tuple(pos + axis * limit + push * 0.01
                              for pos, axis, push in zip(item.position, direction, normal, strict=True))
        item.velocity = dynamite_bounce_velocity(velocity, normal)
        item.bounces += 1

    @staticmethod
    def _blast_amount(distance):
        if distance > BLAST_RADIUS:
            return 0.0
        fraction = max(0.0, (distance - INNER_RADIUS) / (BLAST_RADIUS - INNER_RADIUS))
        return BLAST_MAX + (BLAST_MIN - BLAST_MAX) * fraction

    def detonate(self, item):
        from ow174.game.combat import HitDef, Rocket

        if item not in self.grenades:
            return False
        blast_tags = _dynamite_tags(0x1502, 15)
        burn_tags = _dynamite_tags(0x150F, 1)
        self.grenades.remove(item)
        fight = self.combat
        if item.shot is not None:
            fight.projectiles.flown(item.shot, (fight.tick - item.born) * TICK)
        blast = HitDef(BLAST_MAX, LOADOUT, rings=((INNER_RADIUS, 1.0), (BLAST_RADIUS, BLAST_MIN / BLAST_MAX)),
                       self_scale=0.5, tags=blast_tags)
        rocket = Rocket(item.shooter, item.team, item.position, (0, 0, 1), 0, 0, None, blast,
                        source_body=item.body)
        affected = set()
        fight._explode(rocket, item.position, victims_out=affected)
        for target in fight._targets():
            if target.entity not in affected or fight.vitals_of(target).dead or self._burn_ends(target):
                continue
            own = target.owner is item.shooter and target.entity == item.body
            for key in tuple(self.burns):
                if key[1] == target.entity:
                    del self.burns[key]
            self.burns[item.body, target.entity] = Burn(
                item.shooter, item.body, target.entity, fight.tick,
                fight.tick + math.ceil(BURN_SECONDS / TICK),
                fight.tick + math.ceil(BURN_STEP / TICK), BURN_TOTAL * (0.5 if own else 1.0) / 20,
                tags=burn_tags,
            )
        return True

    def _visible(self, origin, target):
        collision = self.combat._collision()
        point = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        delta = tuple(point[k] - origin[k] for k in range(3))
        distance = math.sqrt(sum(axis * axis for axis in delta))
        if distance <= 1e-9:
            return True
        direction = tuple(axis / distance for axis in delta)
        hit = collision.cast_ray(origin, direction, distance) if collision is not None else None
        return ((hit is None or hit.distance >= distance - 1e-6)
                and not self.combat.walls.blocked(origin, point))

    @staticmethod
    def _burn_ends(target):
        from ow174.game.script import expr

        script = getattr(target.owner, "body_script", None)
        if script is None or script.component.entity != target.entity:
            return False
        for identifier in BURN_END_FLAGS:
            value = script.component.entity_var(identifier, create=False)
            if value is not None and expr.truthy(value.value()):
                return True
        return False

    def _burn(self, tick):
        fight = self.combat
        targets = {item.entity: item for item in fight._targets()}
        for key, burn in list(self.burns.items()):
            target = targets.get(burn.target)
            if (target is None or fight.vitals_of(target).dead or self._burn_ends(target)
                    or (target.owner is not burn.shooter and not (fight.match.game_map.free_for_all or target.team != burn.shooter.team))):
                self.burns.pop(key, None)
                continue
            if tick >= burn.next_damage and burn.pulses < 20:
                fight._damage(burn.shooter, target, burn.damage, False, LOADOUT, tick,
                              world_tick=tick, hit_tags=burn.tags, source_body=burn.source)
                burn.pulses += 1
                # Absolute phase avoids accumulating ceil(0.25 / 0.016) rounding error.
                burn.next_damage = burn.start + math.ceil((burn.pulses + 1) * BURN_STEP / TICK)
            if burn.pulses >= 20 or tick >= burn.until or fight.vitals_of(target).dead:
                self.burns.pop(key, None)


def coach_directions(state, direction) -> tuple:
    """Reconstruct the authored fixed-ring volley (613BDDB0), including its centre pellet.

    Ring populations/radii and spread come from build 104319. The interpretation of spread as
    the full cone angle, equal azimuth spacing and ring phase zero are reconstructed, not a
    native random-seed/pattern match. Only Coach Gun1513/state16 and Enforcer13EB/node24
    are enabled; their different pattern modes are not claimed native-equivalent.
    Keep separate rays so armour, occlusion and death are resolved per pellet.
    """
    from ow174.game.combat import _number, _dot, EPSILON

    cfg = state.node.field("m_5EC1EB38") or {}
    supported = ((state.instance.graph.index == 0x1513 and state.index == 16)
                 or (state.instance.graph.index == 0x13EB and state.node.pos == 24))
    if cfg.get("$") != "STU_613BDDB0" or not supported:
        return (direction,)

    def value(cfg, default=0.0):
        return _number(state.evaluate(cfg, watch=False), default) if cfg is not None else default

    count = int(value(state.node.field("m_numProjectilesPerShot"), 1))
    if not 0 <= count <= 256:
        raise ValueError("fixed volley pellet count outside supported bounds")
    spread = value(cfg.get("m_spread"))
    if not math.isfinite(spread) or not 0 <= spread < 180:
        raise ValueError("invalid fixed volley cone")
    rings = []
    for ring in cfg.get("m_0D30E40C") or []:
        radius = value(ring.get("m_6425FFB9"))
        population = int(value(ring.get("m_B19BD35D")))
        if not math.isfinite(radius) or not 0 <= radius <= 1 or not 0 <= population <= 256:
            raise ValueError("invalid fixed volley ring")
        rings.append((radius, population))
    if sum(population for _, population in rings) != count:
        raise ValueError("fixed volley ring population does not match projectile count")
    # A stable orthonormal basis, including a vertical aim.
    horizontal = math.hypot(direction[0], direction[2])
    right = ((direction[2] / horizontal, 0.0, -direction[0] / horizontal)
             if horizontal > EPSILON else (1, 0, 0))
    up = (
        direction[1] * right[2] - direction[2] * right[1],
        direction[2] * right[0] - direction[0] * right[2],
        direction[0] * right[1] - direction[1] * right[0],
    )
    cone = math.tan(math.radians(spread / 2))
    found = []
    for radius, population in rings:
        for index in range(population):
            angle = index * 2 * math.pi / population
            x, y = cone * radius * math.cos(angle), cone * radius * math.sin(angle)
            ray = tuple(direction[k] + x * right[k] + y * up[k] for k in range(3))
            length = math.sqrt(_dot(ray, ray))
            found.append(tuple(component / length for component in ray))
    return tuple(found)


def _skill_locked(fight, entity, tick):
    return any(lock.until > tick * TICK for lock in fight.sombra_controls.locks.get(entity, ()))


def _carry_move(fight, bot):
    for source, application in getattr(fight, "heavy_charge_pins", {}).items():
        if application.component.entity != bot.entity:
            continue
        carrier = next((other for other in fight.match.bots if other.entity == source and not other.dead), None)
        if carrier is not None:
            from ow174.game.combat import aim
            direction = aim(round(carrier.yaw), 0)
            return ((carrier.position[0]+direction[0], carrier.position[1], carrier.position[2]+direction[2]), 20.0)
    return None


def _blocks_bob_uppercut(owner, charge_yaw):
    # Original 1537/16 rejection of an opposing Heavy charge without tag067D.
    from ow174.game import heavy_charge
    if getattr(owner, "body", None) != heavy_charge.BODY:
        return False
    script = owner.body_script
    if script is None or script.component.fault is not None or script.component.owner_dead:
        return False
    flag = script.component.entity_var(596, create=False)
    turn = (charge_yaw - round(owner.yaw) + 32768) % 65536 - 32768
    return flag is not None and flag.value() is True and abs(turn) > 16384
