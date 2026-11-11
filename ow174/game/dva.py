"""Scoped DVa026A placement and unpossessed mech factory; Matrix remains open.

The original expression supplies a forward offset, down direction, reach,
radius, height and slope limit. Centre-ray landing, capsule clearance, living
body rejection and local position/forward bindings are reconstructed policies.
This is not the native43DB06B1 search/filter/physics service.
"""

import math

from ow174.game import contacts
from ow174.game.script import body_owner, entity_queries, expr, runtime

HERO, PILOT, MECH = 0x02E000000000007A, 0x040000000000040A, 0x0400000000000407


def actor_frame(bot, world_now, default_tick):
    """Reconstruct the created VM's clock from elapsed authoritative world time."""
    clock = bot.spawner.get('dva_clock')
    if clock is None:
        return default_tick
    elapsed = max(0, round(world_now*1000)-clock['world_ms'])
    return clock['frame']+elapsed//clock['frame_ms']


def actor_world_frame(bot, script_time, default_frame):
    """Quantize a timed unpossessed body's projectile birth into world ticks."""
    clock = bot.spawner.get('dva_clock')
    if clock is None:
        return default_frame
    from ow174.game.match import RETAIL_QUANTA

    quantum = RETAIL_QUANTA//1000
    elapsed = max(0, script_time-clock['frame']*clock['frame_ms'])
    return clock['world_tick']+(elapsed+quantum-1)//quantum


def retire_source(match, player):
    from ow174.game import npc

    for bot in tuple(match.bots):
        if (not bot.dva_shell and bot.spawner.get('dva_source_body') == player.body
                and bot.spawner.get('dva_controller') == player.entity):
            npc.remove_body(match, bot)


class QueryRegistry:
    """Declared DVa body-world bindings for the existing native query backend.

    Match membership and controller44 come from actual host objects. Absence of
    a dependency hub is explicit; the VM's existing native polling supplies
    updates. Foreign member reads also poll, without cross-component Var links.
    This is a reconstructed host namespace, not a generic engine registry.
    """

    def __init__(self, world):
        self.world = world
        self.context = entity_queries.Context(
            world.body, True, True, True, self.lookup, None, self.watch_possession)
        self.owner_context = body_owner.Context(
            world.body, True, True, self.lookup_owner, None, self.watch_owner)

    def lookup_owner(self, entity):
        match = self.world.match
        if match is None or match.ended:
            return None
        for player in match.players:
            if player.gone:
                continue
            if player.has_body and player.body == entity:
                return body_owner.Target(entity, player.entity)
            if player.entity == entity:
                return body_owner.Target(entity, None)
        for bot in match.bots:
            if bot.entity == entity:
                # Declared factory ownership policy; does not make the mech possessed.
                return body_owner.Target(entity, bot.spawner.get('dva_controller'))
        return None

    def watch_owner(self, watcher, target):
        self.poll(watcher)

    def lookup(self, entity):
        match = self.world.match
        if match is None or match.ended:
            return None
        if entity == runtime.GAME_MODE_ENTITY:
            return entity_queries.Target(entity, None)
        for player in match.players:
            if player.gone:
                continue
            if entity == player.entity:
                return entity_queries.Target(entity, entity_queries.Possession(
                    player.body if player.has_body else 0))
            if player.has_body and entity == player.body:
                return entity_queries.Target(entity, None)
        if any(bot.entity == entity for bot in match.bots):
            return entity_queries.Target(entity, None)
        return None

    def watch_possession(self, watcher, target):
        # InstanceContext.native already polls before dispatching the backend.
        self.poll(watcher)

    @staticmethod
    def poll(watcher):
        if watcher is not None and not watcher.polled:
            watcher.polled = True
            watcher.ticks()

    def script(self, entity):
        match = self.world.match
        if match is None or match.ended or entity == runtime.GAME_MODE_ENTITY:
            return None
        script = next((p.body_script for p in match.players
                       if not p.gone and p.has_body and p.body == entity), None)
        if script is None:
            script = next((b.body_script for b in match.bots if b.entity == entity), None)
        return script if (script is not None and not getattr(script, 'retired', False)
                          and script.component.fault is None) else None

    def member(self, entity, var, watcher):
        match = self.world.match
        if match is None or match.ended or entity == runtime.GAME_MODE_ENTITY:
            return NotImplemented
        self.poll(watcher)
        script = self.script(entity)
        if script is None:
            return None  # declared registry absence, not a borrowed owner variable
        slot = script.component.entity_var(var, create=False)
        return slot.value() if slot is not None else None


def cast_gate_delay(world, state):
    """Null-config026A/64 only; native engine-clock traversal remains unverified.

    Original null-config conversion/output/queue segments enqueue zero offset
    under explicit clock bindings. The scoped completion here is reconstructed,
    not a general native54 delay, output or cancellation implementation.
    """
    if world.hero != HERO or state.instance.graph.index != 0x026A or state.node.pos != 64:
        return NotImplemented
    fields = ('m_ED97A97C', 'm_7C8AD485', 'm_C7A42E2A', 'm_F8CE386A', 'm_C46403A5')
    if any(state.node.field(name) is not None for name in fields):
        return NotImplemented
    extra = state.node.field('m_E5790891') or {}
    return 0 if not extra.get('links') and not extra.get('m_FF3DAF1E') else NotImplemented


def create_actor(world, instance, node):
    """026A/15 creates an independently allocated0407 body before possession.

    Reuse the ordinary unpossessed-body host and its creates/combat pools. The
    factory/controller binding, yaw placement and lifecycle are reconstructions;
    no possession, seat animation, eject, native factory flags or Matrix claim.
    """
    if world.hero != HERO or instance.graph.index != 0x026A or node.pos != 15:
        return NotImplemented
    from ow174.game import npc, pools
    from ow174.game.match import FREE_FOR_ALL_TEAM
    from ow174.game.mover import loaded_mover_table, mover_data
    from ow174.game.script import graph

    match = world.match
    player = next((p for p in match.players if p.body == world.body and not p.gone), None) if match else None
    if player is None or match.ended or match.combat.is_dead(player):
        return None
    context = PlacementContext(instance, player)
    definition = expr.evaluate(node.field('m_entityDefinition'), context)
    controller = node.field('m_D749D7D8') or {}
    if (not isinstance(definition, expr.Asset) or definition.guid != MECH
            or controller.get('$') != 'STU_F7262B36'
            or expr.to_entity(expr.evaluate(controller.get('m_801B298F'), context)) != player.body):
        return None
    if any(expr.evaluate(node.field(field), context) is not True
           for field in ('m_CA734729', 'm_E5312022')):
        return None
    record = graph.entity_body(MECH)
    values = pools.record_pools(record)
    mover_data(MECH)  # load, then reject the generic fallback rather than accepting its dimensions
    if record is None or values is None or MECH not in loaded_mover_table():
        expr.warn_once('dva-mech-inputs', 'DVa factory requires prepared0407 body graphs and mover data')
        return None
    if any(graph.graph(item['graph']) is None for item in record['graphs']):
        return None
    point = expr.evaluate(node.field('m_position'), context)
    forward = expr.evaluate(node.field('m_AD8E614B'), context)
    if (not isinstance(point, expr.Vec3) or not isinstance(forward, expr.Vec3)
            or not all(math.isfinite(v) for v in (*point, *forward))):
        return None
    yaw = round(math.atan2(forward.x, forward.z)*65536/(2*math.pi))
    team = FREE_FOR_ALL_TEAM if match.game_map.free_for_all else player.team
    try:
        actor = npc.spawn(match, npc.Definition(HERO, MECH, 'DVa mech', *values), tuple(point),
                          team=team, yaw=(yaw+32768) % 65536-32768, manual=False,
                          script_clock=(instance.component.frame, instance.component.frame_ms),
                          dva_owner=(player.body, player.entity))
    except npc.BodyScriptUnavailable:
        return None
    return expr.Entity(actor.entity)


class PlacementContext(runtime.InstanceContext):
    def __init__(self, instance, player):
        super().__init__(instance, None)
        self.player = player

    def native(self, cfg):
        kind = cfg.get('$')
        if kind == 'STU_70C754CD':
            return expr.evaluate(cfg.get('m_11EFE48E'), self)
        if kind in ('STU_0BA27A07', 'STU_2EDEE064', 'STU_021D01CD'):
            entity = expr.to_entity(expr.evaluate(cfg.get('m_entity'), self))
            if entity == self.player.body:
                if kind == 'STU_0BA27A07':
                    return expr.Vec3(*self.player.mover.position)
                from ow174.game.combat import aim

                return expr.Vec3(*aim(self.player.mover.yaw, self.player.mover.pitch))
        return super().native(cfg)


def placement_probe(world, state):
    if world.hero != HERO or state.instance.graph.index != 0x026A or state.node.pos != 38:
        return NotImplemented
    match = world.match
    player = next((p for p in match.players if p.body == world.body and not p.gone), None) if match else None
    if player is None or match.ended or match.combat.is_dead(player) or match.collision_world is None:
        return False, None, None, 'owner-unavailable'
    context = PlacementContext(state.instance, player)

    def value(field):
        return expr.evaluate(state.node.field(field), context)

    origin, direction = value('m_75198C98'), value('m_75198C98.m_35961153')
    reach, radius, height = (expr.to_float(value(f)) for f in (
        'm_75198C98.m_castLength', 'm_BA42208A', 'm_C72356CD'))
    slope = expr.to_float(value('m_5EF7CDF0'))
    if (not isinstance(origin, expr.Vec3) or not isinstance(direction, expr.Vec3)
            or not all(math.isfinite(v) for v in (*origin, *direction, reach, radius, height, slope))
            or reach <= 0 or radius <= 0 or height < 2*radius or not 0 <= slope < 90):
        return False, None, None, 'invalid-query'
    magnitude = math.sqrt(sum(v*v for v in direction))
    if magnitude <= 1e-9:
        return False, None, None, 'invalid-direction'
    unit = tuple(v/magnitude for v in direction)
    scene, category = match.collision_world, player.mover.category
    hit = scene.cast_ray(tuple(origin), unit, reach, category)
    if hit is None:
        return False, None, None, 'no-ground'
    normal = tuple(hit.normal)
    if sum(a*b for a, b in zip(normal, unit, strict=True)) > 0:
        normal = tuple(-v for v in normal)
    if normal[1] < math.cos(math.radians(slope)):
        return False, None, None, 'slope'
    point = tuple(hit.point)
    body = contacts.CharacterBody(radius, height-radius, world=scene.engine, category=category)
    body.teleport(point)
    if not body.fits(radius, height-radius):
        return False, None, None, 'blocked-volume'
    ignores = {expr.to_entity(expr.evaluate(cfg, context))
               for cfg in state.node.field('m_ignoreEntities') or ()}
    ignores.add(player.body)
    a = (point[0], point[1]+radius, point[2])
    b = (point[0], point[1]+height-radius, point[2])
    for target in match.combat._targets():
        if target.entity in ignores or match.combat.vitals_of(target).dead:
            continue
        ta, tb = target.segment()
        manifold = contacts.capsule_capsule(a, b, radius, ta, tb, target.radius)
        if any(p.separation < 0 for p in manifold.points):
            return False, None, None, 'blocked-body'
    return True, expr.Vec3(*point), expr.Vec3(*normal), 'clear'


def entry_pose(world, instance, cfg, kind):
    """Scoped model-root and forward reconstruction for 026A/85 and /200."""
    selector = cfg.get("m_entity")
    if kind not in ("STU_71A87E92", "STU_021D01CD") or not isinstance(selector, dict):
        return NotImplemented
    if expr.dynamic(selector) != (runtime.INSTANCE, 38) or world.match is None:
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(selector))
    bot = next((actor for actor in world.match.bots
                if actor.entity == entity and actor.body == MECH and not actor.dead
                and actor.spawner.get("dva_source_body") == world.body), None)
    if bot is None:
        return expr.EVALUATION_FAILED
    if kind == "STU_71A87E92":
        return expr.Vec3(*bot.position)
    from ow174.game.combat import aim

    return expr.Vec3(*aim(round(bot.yaw), 0))


RETURN_TO_PILOT = 0x024000000000016C
SELF_DESTRUCT_COMPLETE = 0x0240000000000592


def controlled_mech(player):
    """The bound 0407, including a defeated body with a pending return."""
    if player.hero.guid != HERO:
        return None
    return next((bot for bot in player.match.bots
                 if bot.dva_mover is not None and not bot.dva_shell and bot.body_script is not None
                 and bot.spawner.get("dva_source_body") == player.body
                 and bot.spawner.get("dva_controller") == player.entity), None)


def begin_control(instance):
    """Bind the exact authored var38 entity after successful /200 entry."""
    from ow174.game.mover import Mover, mover_data

    world = instance.component.world
    match = world.match
    if match is None or match.ended:
        return
    player = next((p for p in match.players
                   if p.body == world.body and p.body_script is not None
                   and p.body_script.component is instance.component and not p.gone), None)
    scope = instance.states.get(15)
    if player is None or match.combat.is_dead(player) or scope is None or not scope.active or scope.ending:
        return
    selected = expr.to_entity(instance.evaluate(instance.graph.node(20).field("m_C98661CF")))
    bot = next((b for b in match.bots if b.entity == selected and b.body == MECH
                and b.spawner.get("dva_source_body") == player.body
                and b.spawner.get("dva_controller") == player.entity
                and not b.dead and b.body_script is not None), None)
    if bot is None or controlled_mech(player) is not None:
        return
    bot.dva_mover = Mover(bot.position, round(bot.yaw), match.collision_world,
                          mover_data(MECH), category=player.mover.category)
    bot.dva_mover.velocity = bot.velocity
    bot.dva_buttons = 0


def drive_mech(player, bot, command, seconds):
    """Run the player's posed commands on the already published owned body."""
    from dataclasses import replace

    from ow174.game.commands import Command
    from ow174.game.mover import statescript_mods
    from ow174.game.script.driver import button_edges

    match, script, mover = player.match, bot.body_script, bot.dva_mover
    if bot.dead:
        player._advance_body_frame(Command(command.frame), posed=False)
        return
    mover.use(world=match.collision_world)

    def step(pose, duration, tick):
        stunned = match.combat.is_stunned(bot, tick=tick)
        if stunned:
            pose = replace(pose, buttons=0, action=0, forward=0, right=0)
            mover.velocity = (0, mover.velocity[1], 0)
        mover.step_pose(pose, duration, tick)
        bot.position, bot.velocity = tuple(mover.position), mover.velocity
        bot.yaw, bot.pitch = pose.aim()
        bot.moved_tick = tick
        if pose.frame > script.frame:
            edges = button_edges(bot.dva_buttons, pose.buttons, pose.action)
            bot.dva_buttons = pose.buttons
            match.combat.advance_bot_weapon(bot, script, pose.frame, not stunned, edges)
        mover.track_mods(statescript_mods(script.component), pose.frame)

    first = mover.first_step_frame(command.frame)
    if script.frame + 1 < first:
        match.combat.advance_bot_weapon(bot, script, script.frame+1, False,
                                       button_edges(bot.dva_buttons, 0))
        bot.dva_buttons = 0
        if script.frame < first-1:
            match.combat.advance_bot_weapon(bot, script, first-1, False)
    mover.step(command, seconds, match.tick, simulate_pose=step)
    player._advance_body_frame(Command(command.frame), posed=False)


def queue_demech_return(match, bot):
    """Reconstructed lethal trigger for the authored 0276/5 return send."""
    from ow174.game.script import nodes

    pilot = match._message_player(bot.spawner.get("dva_source_body"))
    if pilot is None or controlled_mech(pilot) is not bot:
        return
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == 0x0276:
            nodes.send(instance, instance.graph.node(5), None)
            return


def complete_return(pilot, bot):
    """Reconstruct safe grounded ejection after the owned return message."""
    from ow174.game.mover import Mover, mover_data

    mover = Mover(bot.position, round(bot.yaw), pilot.match.collision_world,
                  mover_data(pilot.hero.body), category=pilot.mover.category)
    radius, segment = mover._capsule()
    if mover._engine_body().fits(radius, radius + segment):
        pilot.mover = mover
    bot.dva_shell = not bot.dead and return_ready(bot)
    if bot.dva_shell:
        bot.last_tick = pilot.match.tick
        # Preserve the same VM and anchor its command-frame clock to world time.
        bot.spawner["dva_clock"] = {"frame": bot.body_script.frame,
                                   "frame_ms": bot.body_script.component.frame_ms,
                                   "world_ms": round(pilot.match.combat.now*1000),
                                   "world_tick": pilot.match.tick}
        bot.body_script.component.edges([(button, False) for button in tuple(bot.body_script.component.held)])
    else:
        bot.dva_mover = None
    bot.dva_buttons = 0
    pilot.match.combat.history.pop(pilot.body, None)
    component = pilot.body_script.component
    for instance in tuple(component.instances.values()):
        if instance.graph.index == 0x026A:
            ability = instance.states.get(instance.graph.node(9).state)
            if ability is not None and ability.active:
                # End the retained Call Mech ability lock through its normal stop path.
                ability.stop()
    component.run_now()


def self_destruct(bot):
    return next((i for i in bot.body_script.component.instances.values()
                 if i.graph.index == 0x0B2F), None) if bot.body_script is not None else None


def return_ready(bot, *, queued=False):
    """Validate the lethal or armed return; the fuse begins after the send."""
    from ow174.game.script.nodes import Ability

    if bot.dead:
        return True
    instance = self_destruct(bot)
    if instance is None:
        return False
    state = instance.states.get(0 if queued else 6)
    return state is not None and state.active and (
        not queued or bool(state.flags & Ability.RUNNING))


def shell_owner(world, instance):
    if world.match is None or instance.graph.index != 0x0B2F:
        return None
    return next((bot for bot in world.match.bots if bot.entity == world.body and bot.dva_shell
                 and bot.body_script is not None and bot.body_script.component is instance.component), None)


def completion_source(pilot, sender):
    if pilot.match.combat.is_dead(pilot):
        return None
    return next((bot for bot in pilot.match.bots if bot.entity == sender and bot.dva_shell
                 and bot.spawner.get("dva_controller") == pilot.entity
                 and bot.spawner.get("dva_source_body") == pilot.body), None)


def shell_native(world, instance, cfg):
    selector = cfg.get("m_D63CBEC2") or {}
    if (cfg.get("$") == "STU_F76C486C" and selector.get("$") == "STU_B5A0CAF0"
            and (bot := shell_owner(world, instance)) is not None):
        # This factory establishes the pet-owner relationship for its detached shell.
        return expr.Entity(bot.spawner["dva_controller"])
    return NotImplemented


class ShellSurfaceDistance(runtime.InstanceContext):
    """Run the original blast amount expression against a sampled barrier surface."""

    def __init__(self, instance, distance):
        super().__init__(instance, None)
        self.distance = expr.f32(distance)

    def var(self, scope, var):
        if (scope, var) == (expr.INSTANCE, 680):
            return self.distance
        return super().var(scope, var)


def shell_targets(world, state):
    from ow174.game.combat import EPSILON, capsule_visible, ray_world, segment_distance

    instance = state.instance
    bot = shell_owner(world, instance)
    if bot is None or state.node.pos != 8:
        return
    centre = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
    reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    selected, distances, shields = [], [], {}
    fight = world.match.combat
    if isinstance(centre, expr.Vec3) and math.isfinite(reach) and reach > 0:
        origin = tuple(a+b for a,b in zip(bot.position, centre, strict=True))
        collision = fight._collision()
        for target in fight._enemies(bot):
            if target.owner.entity == bot.spawner.get("dva_controller"):
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(origin, a, b)-target.radius)
            if distance > reach or not capsule_visible(collision, origin, a, b, target.radius):
                continue
            delta = tuple((low+high)/2-point for low, high, point in zip(a,b,origin,strict=True))
            length = math.sqrt(sum(value*value for value in delta))
            if length > EPSILON:
                direction = tuple(value/length for value in delta)
                shield_hit = fight.barriers.ray(bot, origin, direction, min(reach, length-target.radius))
                if shield_hit is not None:
                    hit, shield = shield_hit
                    wall = ray_world(collision, origin, direction, hit)
                    if wall is None or wall+EPSILON >= hit:
                        previous = shields.get(id(shield))
                        if previous is None or hit < previous[0]:
                            shields[id(shield)] = hit, shield
                    continue
            selected.append(expr.Entity(target.entity))
            distances.append(expr.f32(distance))
    for field, values in (("m_8A2905A0", selected), ("m_CEE20F6F", distances)):
        instance.write(expr.dynamic(state.node.field(field)), tuple(values))
    # Resolve every protected target before the same blast can break a shield.
    value = instance.graph.node(24).field("m_value")
    for hit, shield in shields.values():
        amount = expr.to_float(expr.evaluate(value, ShellSurfaceDistance(instance, hit)))
        fight.barriers.damage(shield, amount, bot.entity)


def shell_damage(world, state, elapsed):
    bot = shell_owner(world, state.instance)
    if bot is None or state.node.pos != 22:
        return NotImplemented
    if not elapsed:
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        entities = selected if isinstance(selected, tuple) else (selected,)
        targets = {expr.to_entity(entity) for entity in entities}
        amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
        loadout = state.evaluate(state.node.field("m_modifyHealth.m_loadout"), watch=False)
        fight = world.match.combat
        for target in fight._enemies(bot):
            if target.entity in targets:
                shielded = fight.barriers.bubble(target) is not None
                fight._damage(bot, target, amount, False,
                              loadout.guid if isinstance(loadout, expr.Asset) else None, fight.tick)
                if not shielded and not fight.vitals_of(target).dead:
                    shell_push(bot, state, target)
        state.request_finish()
    return True


def shell_push(bot, state, target):
    """Apply the authored radial ring multipliers to a surviving player mover."""
    from ow174.game.combat import aim, segment_distance

    mover = getattr(target.owner, "mover", None)
    if mover is None:
        return
    a, b = target.segment()
    distance = max(0.0, segment_distance(bot.position, a, b)-target.radius)
    settings = state.node.field("m_modifyHealth")
    horizontal = expr.to_float(state.evaluate(settings["m_E33EFEE1"], watch=False))
    lift = expr.to_float(state.evaluate(settings["m_583C208B"], watch=False))
    if not all(math.isfinite(value) for value in (horizontal, lift)):
        return
    for ring in settings["m_knockbackRings"]:
        radius = expr.to_float(state.evaluate(ring["m_radius"], watch=False))
        if distance > radius:
            continue
        horizontal *= expr.to_float(state.evaluate(ring["m_9102DA3F"], watch=False))
        lift *= expr.to_float(state.evaluate(ring["m_10399AE7"], watch=False))
        dx, dz = target.feet[0]-bot.position[0], target.feet[2]-bot.position[2]
        length = math.hypot(dx, dz)
        if length > 1e-6:
            dx, dz = dx/length, dz/length
        else:
            dx, _, dz = aim(round(bot.yaw), 0)
        vx, vy, vz = mover.velocity
        mover.velocity = (vx+horizontal*dx, max(vy, lift), vz+horizontal*dz)
        return
