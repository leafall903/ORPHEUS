"""Scoped Assassin1157/1177 consumers with declared physical transform routing.

The original graphs select/latch the controller target and create victim1177.
Health routes through that player's actual possessed body. Node25's initial25
is consumed on entry before its transition ends the state. Node15's mode1 is
interpreted as100 damage/second after Wait24, with continuous armour handling.
The health mode/clock/armour semantics are reconstructed pending native comparison.
No synthetic gun is emitted.
"""

import math

from ow174.game.script import expr

ASSASSIN_BODY = 0x0400000000001EBD
TROOPER_BODY = 0x0400000000001D40
TALON_HIT_MESSAGE = 0x024000000000077A
ENFORCER_BODY = 0x0400000000002279
ENFORCER_CONTROLLER_BODY = 0x040000000000227A
ENFORCER_CONTROLLER = 0x13EA
ENFORCER_WEAPON = 0x13EB
ENFORCER_MOVEMENT = 0x13EF
ENFORCER_MOVEMENT_GATE = 12299
# Each entry holds the graph and its raw Wait and directional state indexes.
REACTION_GRAPHS = {
    TROOPER_BODY: (0x13AC, 2, 3),
    ENFORCER_BODY: (0x1506, 1, 2),
    0x0400000000001EBB: (0x14EB, 1, 2),
}
# DATA:1EBD->7C3.021 alias561/category82->508A.006, header+0x10.
# The same1.4666667 appears in7C3's parallel animation game-data duration.
ASSASSIN_RECOVERY_SECONDS = 1.4666666984558105

# Exact body and TrackTargets / ModifyHealth / Ability nodes from 104319.
NPC_MELEE = {
    0x1422: (0x0400000000001D40, 5, 4, 8),
    0x140E: (0x0400000000001EBB, 6, 5, 9),
}
# These authored magazines own a running reload gate in instance variable 51.
NPC_RELOAD = {
    0x102E: 0x0400000000001D40,
    0x13EB: ENFORCER_BODY,
}

def reaction_movement(bot, frame=None):
    """Find the live directional state owned by the authored reaction wait."""
    reaction = REACTION_GRAPHS.get(bot.body)
    component = getattr(bot.body_script, 'component', None)
    if reaction is None or bot.dead or component is None or component.owner_dead:
        return None
    graph_id, wait_index, motion_index = reaction
    for instance in component.instances.values():
        if instance.graph.index != graph_id:
            continue
        wait, motion = instance.states.get(wait_index), instance.states.get(motion_index)
        if not (wait and wait.active and motion and motion.active):
            continue
        start_frame = motion.start // component.frame_ms
        end_frame = (wait.start + wait.ms) // component.frame_ms
        if frame is None or start_frame < frame <= end_frame:
            return motion
    return None


def reaction_axis(world, instance, cfg):
    kind = cfg.get('$')
    if (kind not in ('STU_021D01CD', 'STU_66F3B89E') or world.match is None
            or expr.to_entity(instance.evaluate(cfg.get('m_entity'))) != world.body):
        return NotImplemented
    actor = next((bot for bot in world.match.bots if not bot.dead and bot.entity == world.body), None)
    if actor is None:
        return NotImplemented
    reaction = REACTION_GRAPHS.get(actor.body)
    if reaction is None or reaction[0] != instance.graph.index:
        return NotImplemented
    angle = round(actor.yaw) * math.tau / 65536
    sine, cosine = expr.f32(math.sin(angle)), expr.f32(math.cos(angle))
    return expr.Vec3(sine, 0, cosine) if kind == 'STU_021D01CD' else expr.Vec3(cosine, 0, -sine)


def melee_context(world, state, node_slot):
    spec = NPC_MELEE.get(state.instance.graph.index)
    if world.match is None or spec is None or state.node.pos != spec[node_slot]:
        return None
    actor = next((bot for bot in world.match.bots if bot.entity == world.body and bot.body == spec[0]), None)
    return (world.match.combat, actor, spec) if actor is not None else None


def melee_volume(actor, tracker):
    from ow174.game import combat

    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    forward = combat.aim(round(actor.yaw), 0)
    x, y, z = actor.position
    centre = (x + forward[0] * offset.z, y + offset.y, z + forward[2] * offset.z)
    return (x, y + combat.EYE, z), forward, centre, radius


def melee_contacts(fight, actor, volume):
    from ow174.game import combat

    origin, _, centre, radius = volume
    if fight.actions_blocked(actor) or fight.hacks.active(actor.entity):
        return
    for target in fight._enemies(actor):
        a, b = target.segment()
        if (combat.segment_distance(centre, a, b) <= radius + target.radius
                and combat.capsule_visible(fight._collision(), origin, a, b, target.radius,
                                            walls=fight.walls)):
            yield target


def melee_targets(world, state):
    context = melee_context(world, state, 1)
    if context is None:
        return NotImplemented
    fight, actor, _ = context
    return tuple(target.entity for target in melee_contacts(fight, actor, melee_volume(actor, state)))


def melee_health(world, state):
    context = melee_context(world, state, 2)
    if context is None:
        return NotImplemented
    fight, actor, spec = context
    if fight.actions_blocked(actor) or fight.hacks.active(actor.entity):
        return True
    tracker = state.instance.node_state(state.instance.graph.node(spec[1]))
    volume = melee_volume(actor, tracker)
    origin, forward, centre, radius = volume
    amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    selected = {entity for entity, _ in tracker.targets}
    for target in melee_contacts(fight, actor, volume):
        if target.entity in selected:
            affected = fight._damage(actor, target, amount, False, None, fight.tick,
                                     melee_direction=forward, world_tick=fight.tick)
            if affected and not fight.vitals_of(target).dead:
                fight.impulses.radial_health(target, actor.position, state)
    fight.barriers.hit_capsule(actor, centre, centre, radius, amount, origin)
    return True


def enforcer_speed_state(bot):
    if bot.body != ENFORCER_BODY or bot.dead or bot.body_script is None:
        return None
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == ENFORCER_MOVEMENT:
            state = instance.states.get(instance.graph.node(0).state)
            return state if state is not None and state.active else None
    return None


def enforcer_walk_scale(bot):
    """Consume the active 13EF property-0 multiplier for route movement."""
    state = enforcer_speed_state(bot)
    if state is None:
        return 1.0
    row = state.node.field("m_BC5E91CF")[0]
    return expr.to_float(state.evaluate(row["m_9B7A63EA"], watch=False))


def enforcer_actor(world, instance):
    if world.match is None or instance.graph.index != ENFORCER_CONTROLLER:
        return None
    return next((bot for bot in world.match.bots
                 if bot.controller_entity == instance.component.entity
                 and bot.body == ENFORCER_BODY and not bot.dead), None)


def enforcer_target(fight, actor, manual_target):
    if manual_target:
        return next((target for target in fight._enemies(actor)
                     if manual_target in (target.entity, target.owner.entity)), None)
    if actor.spawner.get('auto_target'):
        # The host ranks live enemies; the graph owns its range and sight gates.
        return min(fight._enemies(actor),
                   key=lambda target: (math.dist(actor.position, target.feet), target.entity), default=None)
    return None


def enforcer_visible(fight, source, target):
    from ow174.game.combat import ray_world

    collision = fight.match.collision_world
    if collision is None:
        return False
    a = (source.feet[0], source.feet[1] + source.eye, source.feet[2])
    b = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
    delta = tuple(end - start for start, end in zip(a, b, strict=True))
    distance = math.hypot(*delta)
    if distance <= 1e-9:
        return True
    direction = tuple(value / distance for value in delta)
    hit = ray_world(collision, a, direction, distance)
    return (hit is None or hit >= distance) and not fight.walls.blocks(a, b)


def enforcer_fire_direction(fight, bot):
    """Reconstructed trigger and aim while the authored engagement scope runs."""
    from ow174.game.combat import EYE

    if fight.actions_blocked(bot) or enforcer_speed_state(bot) is None:
        return None
    controller = bot.body_script.controller
    if controller is None or controller.owner_dead:
        return None
    target = enforcer_target(fight, bot, controller.world.manual_target)
    source = next((source for source in fight._targets() if source.owner is bot), None)
    if source is None or target is None or not enforcer_visible(fight, source, target):
        return None
    x, y, z = bot.position
    tx, ty, tz = target.feet
    delta = (tx - x, ty + target.eye - y - EYE, tz - z)
    length = math.hypot(*delta)
    return tuple(value / length for value in delta) if length > 1e-9 else None


def trooper_magazine_query(world, instance, cfg):
    """Host the explicit target and pose inputs for the authored 11E3 gate."""
    if world.match is None or instance.graph.index != 0x11E3:
        return NotImplemented
    selection, source_position, target_facing = instance.graph.node(32).field("m_condition.m_configVars")
    if not any(cfg is query for query in (selection, source_position, target_facing)):
        return NotImplemented
    actor = next((bot for bot in world.match.bots
                  if bot.controller_entity == instance.component.entity
                  and bot.body == TROOPER_BODY and not bot.dead), None)
    if actor is None:
        return NotImplemented
    if cfg is source_position:
        return expr.Vec3(*actor.position)
    identity = world.manual_target
    target = None
    if identity:
        target = next((candidate for candidate in world.match.combat._enemies(actor)
                       if identity in (candidate.entity, candidate.owner.entity)), None)
    if cfg is selection:
        return expr.Entity(target.entity if target is not None else 0)
    if target is None:
        return expr.Vec3(0, 0, 0)
    from ow174.game.combat import aim

    pose = getattr(target.owner, "mover", target.owner)
    return expr.Vec3(*aim(pose.yaw, pose.pitch))


def enforcer_query(world, instance, cfg):
    """Host the selected slot and visibility query only for controller 13EA."""
    kind = cfg.get("$")
    if kind not in ("STU_A37A910D", "STU_D379F515", "STU_783B4CF0", "STU_71A87E92"):
        return NotImplemented
    actor = enforcer_actor(world, instance)
    if actor is None:
        return NotImplemented
    fight = world.match.combat
    if kind == "STU_783B4CF0":
        entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
        vitals = fight.vitals.get(entity) if entity == actor.entity else None
        return expr.f32(vitals.total) if vitals is not None else 0.0
    if kind == "STU_71A87E92":
        return world.position(expr.to_entity(instance.evaluate(cfg.get("m_entity"))))
    if kind == "STU_A37A910D":
        owner = expr.to_entity(instance.evaluate(cfg.get("m_152B6961")))
        slot = instance.evaluate(cfg.get("m_A60150E2"))
        if owner != actor.controller_entity or slot != 0:
            return expr.Entity(0)
        target = enforcer_target(fight, actor, world.manual_target)
        return expr.Entity(target.entity if target is not None else 0)
    owner = expr.to_entity(instance.evaluate(cfg.get("m_7826B991")))
    entity = expr.to_entity(instance.evaluate(cfg.get("m_target")))
    if owner != actor.controller_entity or not entity or world.match.collision_world is None:
        return False
    targets = fight._targets()
    source = next((t for t in targets if t.owner is actor), None)
    target = next((t for t in targets if t.entity == entity), None)
    return source is not None and target is not None and enforcer_visible(fight, source, target)


def poll_enforcer_gate(controller):
    """Recheck the authored 63 transition after polling its pose inputs."""
    for instance in controller.instances.values():
        if instance.graph.index == ENFORCER_CONTROLLER:
            state = instance.states.get(25)
            if state is not None and state.active:
                state.try_complete()
                controller.run_now()
            return


def discard_talon_hit_messages(component):
    """Remove queued hit reactions before the death frame is drained."""
    for instance in component.instances.values():
        if instance.graph.index in (0x0E20, 0x0E54):
            instance.queue = [event for event in instance.queue
                              if event.message is None or event.message[0] != TALON_HIT_MESSAGE]


def _active_talon_hit_waits(instance):
    # The common listener has separate root waits for its 2560, directional
    # and 255F routes. Each root owns the shorter waits in its subtree.
    for position in (20, 27, 87):
        node = instance.graph.node(position)
        wait = instance.states.get(node.state) if node is not None else None
        if wait is not None and wait.active:
            yield wait


def retire_talon_hit(component):
    """End active Talon hit reactions when the body enters death."""
    for instance in tuple(component.instances.values()):
        if instance.graph.index == 0x0E20:
            for wait in _active_talon_hit_waits(instance):
                wait.end(False)


def clocked(bot):
    """Preserve authored NPC special timing across older no-input windows."""
    from ow174.game import heavy_charge
    from ow174.game.script.nodes import Ability, AIBehavior

    script = bot.body_script
    component = getattr(script, "component", None)
    if bot.dead or component is None or component.owner_dead:
        return False
    if reaction_movement(bot) is not None:
        return True
    for instance in component.instances.values():
        if instance.graph.index == 0x0E20:
            if any(event.message is not None and event.message[0] == TALON_HIT_MESSAGE for event in instance.queue):
                return True
            if next(_active_talon_hit_waits(instance), None) is not None:
                return True
        if instance.graph.index != 0x0E62:
            continue
        wait, listener = instance.states.get(30), instance.vars.get(0x25EA)
        if (wait is not None and wait.active and listener is not None
                and listener.base == expr.Asset(0x0580000000000E20)):
            return True
    if bot.body == ASSASSIN_BODY:
        return script.controller is not None and not script.controller.owner_dead
    if bot.body == TROOPER_BODY and script.controller is not None and not script.controller.owner_dead:
        marker = script.controller.entity_var(13397, create=False)
        if script.controller.world.manual_target or (marker is not None and expr.truthy(marker.value())):
            return True
    if bot.body == ENFORCER_BODY and script.controller is not None and not script.controller.owner_dead:
        gate = script.controller.entity_var(ENFORCER_MOVEMENT_GATE, create=False)
        if (gate is not None and gate.value() == 1) or enforcer_speed_state(bot) is not None:
            return True
        if bot.spawner.get('auto_target') or script.controller.world.manual_target or any(
            state.selected for instance in script.controller.instances.values()
            if instance.graph.index == ENFORCER_CONTROLLER
            for state in instance.states.values() if isinstance(state, AIBehavior)
        ):
            return True
    match = component.world.match
    if match is not None and bot.entity in match.combat.sniper_repositions:
        return True
    charge = heavy_charge.charge(bot)
    if charge is not None and (heavy_charge.BUTTON in component.held or heavy_charge.phase(charge)):
        return True
    for instance in component.instances.values():
        if NPC_RELOAD.get(instance.graph.index) == bot.body:
            reload_gate = instance.vars.get(51)
            if reload_gate is not None and expr.truthy(reload_gate.value()):
                return True
        spec = NPC_MELEE.get(instance.graph.index)
        if spec is not None and bot.body == spec[0]:
            state = instance.states.get(instance.graph.node(spec[3]).state)
            running = isinstance(state, Ability) and bool(state.flags & (Ability.RUNNING | Ability.PUSHED))
            if 31 in component.held or running:
                return True
    return False


def recovery_duration(world, state):
    """Resource clip duration with a reconstructed host playback/finish clock.

    Actual Anim31 owns the13401 override and clears536 on its original end plug.
    Its graph duration is absent; exact alias561/category82 resolves through
    actual7C3/640 resources to508A's1.4666667s clip. Native transition/rate and
    authoritative animation completion remain outside this host timer policy.
    """
    if (world.match is None or world.hero != 0x02E00000000001BB
            or state.instance.graph.index != 0x1157 or state.node.pos != 31):
        return NotImplemented
    actor = next((b for b in world.match.bots if b.entity == state.instance.component.entity), None)
    if actor is None or actor.body != ASSASSIN_BODY:
        return NotImplemented
    alias = state.evaluate(state.node.field('m_11E0A658'), watch=False)
    category = state.evaluate(state.node.field('m_22326315'), watch=False)
    if alias != expr.Asset(0x0C80000000000561) or category != expr.Asset(0x0280000000000082):
        return NotImplemented
    return ASSASSIN_RECOVERY_SECONDS


def attack(bot):
    script = bot.body_script
    if (bot.body != ASSASSIN_BODY or bot.dead or script is None or script.component.owner_dead
            or script.controller is None or script.controller.owner_dead):
        return None
    if not script.controller.world.manual_target:
        return None
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x1157), None)
    root = instance.states.get(10) if instance is not None else None
    return instance if root is not None and root.active else None


def victim_scope(player, source=None):
    controller = getattr(player, 'controller_component', None)
    if controller is None:
        return None
    return next((i for i in controller.instances.values() if i.graph.index == 0x1177
                 and (source is None or i.cached_source_entity == source)
                 and (state := i.states.get(0)) is not None and state.active), None)


def constraint(instance, player):
    """Actual28 owns the default type8 zero-local-offset target constraint."""
    state = instance.node_state(instance.graph.node(28))
    if (not state.active or state.node.cls != 'STU_72DDE005'
            or any(state.node.field(key) is not None for key in
                   ('m_9193F8E8', 'm_888BCD8B', 'm_4B679A9F', 'm_C6CA4680', 'm_32E43969'))):
        return None
    target = expr.to_entity(state.evaluate(state.node.field('m_3E3E203A'), watch=False))
    return state if target == player.entity else None


def contact_target(fight, source, player):
    """Declared contact policy: actual capsule overlap and an unoccluded axis segment.

    Capsule sizes come from the two current bodies, not an invented acquisition
    radius. This is a reconstructed admission/lifetime rule, not native72DDE005
    attachment or AI approach. Target collection alone cannot establish contact.
    """
    from ow174.game.combat import EPSILON, ray_world

    if fight.match.collision_world is None:
        return None
    target = next((t for t in fight._enemies(source) if t.owner is player), None)
    own = next((t for t in fight._targets() if t.owner is source), None)
    if target is None or own is None:
        return None
    a, b = own.segment()
    c, d = target.segment()
    if b[1] < c[1]:
        origin, end = b, c
    elif d[1] < a[1]:
        origin, end = a, d
    else:
        height = (max(a[1], c[1]) + min(b[1], d[1])) / 2
        origin, end = (a[0], height, a[2]), (c[0], height, c[2])
    distance = math.dist(origin, end)
    if distance > own.radius + target.radius + EPSILON:
        return None
    if distance > EPSILON:
        direction = tuple((v - u) / distance for u, v in zip(origin, end, strict=True))
        wall = ray_world(fight.match.collision_world, origin, direction, distance)
        if wall is not None and wall < distance - EPSILON:
            return None
        if fight.walls.blocks(origin, end):
            return None
    return target


def initial_health(world, state):
    """Original1157/node25 entry precedes transition43->45 victim PlayScript.

    Frame-end polling misses this state: its transition queues its own abort
    in the same frame. This host entry consumer preserves that graph ordering.
    Health flags/tags/difficulty/contact interpretation remains reconstructed.
    """
    if world.match is None or state.instance.graph.index != 0x1157 or state.node.pos != 25:
        return NotImplemented
    fight = world.match.combat
    admissions = fight.assassins.admissions
    admissions.pop(state.instance.component.entity, None)
    source = next((b for b in world.match.bots if b.entity == state.instance.component.entity), None)
    if source is None or attack(source) is not state.instance or fight.actions_blocked(source):
        return True
    if fight.hacks.active(source.entity):
        return True
    controller = expr.to_entity(state.evaluate(state.node.field('m_FA3ECC43'), watch=False))
    player = next((p for p in fight.match.players if p.entity == controller and not p.gone), None)
    target = contact_target(fight, source, player) if player is not None else None
    if target is None:
        return True
    settings = state.node.field('m_modifyHealth')
    amount = expr.to_float(state.evaluate(settings['m_amount'], watch=False))
    if (amount > 0 and fight._damage(source, target, amount, False, None, fight.tick, world_tick=fight.tick)
            and not fight.vitals_of(target).dead):
        admissions[source.entity] = (
            state.instance.id, state.instance.component.frame, player.entity, player.body,
        )
    return True


class Assassins:
    def __init__(self, combat):
        self.combat = combat
        self.health_clock = {}
        self.bindings = {}
        self.admissions = {}

    def admit(self, instance, player):
        """Consume the initial hit for the paired victim request in this source frame."""
        component = instance.component
        return self.admissions.pop(component.entity, None) == (
            instance.id, component.frame, player.entity, player.body,
        )

    def bind(self, source, player):
        self.bindings[source.entity] = (player.entity, player.body)
        instance = attack(source)
        if instance is not None and constraint(instance, player) is not None:
            self._place(source, player, instance)
        player._track_movement_mods(player.body_script.frame)

    def _place(self, source, player, instance):
        """Default type8 data, with explicit controller->body/world transform policy.

        Native default output carries target/local0 and zero velocity. The host
        resolves that controller's live possessed-body feet at zero offset.
        It does not emulate native parent/bone transforms or scheduling.
        Original30 supplies the opposite-forward expression. NPC pitch is real
        stored/replicated pose data, not a new VM variable or animation alias.
        """
        source.position = tuple(player.mover.position)
        source.velocity = (0., 0., 0.)
        source.held = (0, 0)
        self.combat.impulses.retire(source.entity)
        facing = instance.node_state(instance.graph.node(30))
        if facing.active:
            direction = facing.evaluate(facing.node.field('m_7CCD8B4B'), watch=False)
            if isinstance(direction, expr.Vec3):
                source.yaw = math.atan2(direction.x, direction.z) * 65536 / (2 * math.pi)
                elevation = math.atan2(direction.y, math.hypot(direction.x, direction.z))
                source.pitch = -elevation * 65536 / (2 * math.pi)
        source.moved_tick = self.combat.tick

    def before_pose(self, player):
        """Capture a live graph/identity before an actual parent-body movement step."""
        if not self.controlled(player):
            return None
        scope = victim_scope(player)
        source = next(b for b in self.combat.match.bots if b.entity == scope.cached_source_entity)
        return source, attack(source), player.body

    def after_pose(self, player, binding):
        """Carry only the already-bound child; teleports/separation cannot acquire a pin."""
        if binding is None:
            return
        source, instance, body = binding
        if (player.body == body and self.bindings.get(source.entity) == (player.entity, body)
                and attack(source) is instance and constraint(instance, player) is not None
                and victim_scope(player, source.entity) is not None):
            self._place(source, player, instance)

    def interrupt(self, source):
        """Declared rescue policy: control/melee/external source force releases1157."""
        if getattr(source, 'body', None) != ASSASSIN_BODY:
            return
        instance = attack(source)
        if instance is not None:
            self.cancel(source, instance)
        else:
            self.release(source.entity)

    def retire_victim(self, player):
        """Retire the old binding before death/replacement/leave changes identity."""
        for entity, (controller, body) in list(self.bindings.items()):
            if controller == player.entity and body == player.body:
                source = next((b for b in self.combat.match.bots if b.entity == entity), None)
                if source is not None:
                    self.interrupt(source)
                else:
                    self.release(entity)

    def movement_mods(self, player):
        """Project only actual bound1177 records to the possessed body's mover.

        The records/operations/priorities are original data. Controller->body
        routing and using the latest body command frame for no-input changes
        are declared host policies, not native attachment/phase equivalence.
        Four-part keys retain controller identity apart from body instance IDs.
        """
        from ow174.game.mover import statescript_mods

        if not self.controlled(player):
            return {}
        scope = victim_scope(player)
        return {(player.entity, *key): value
                for key, value in statescript_mods(player.controller_component).items()
                if key[0] == scope.id}

    def controlled(self, player):
        script = getattr(player, 'body_script', None)
        scope = victim_scope(player)
        if scope is None or script is None or script.retired or script.component.owner_dead:
            return False
        bot = next((b for b in self.combat.match.bots if b.entity == scope.cached_source_entity), None)
        instance = attack(bot) if bot is not None else None
        return (instance is not None and not (self.combat.is_stunned(bot) or self.combat.is_asleep(bot))
                and self.bindings.get(bot.entity) == (player.entity, player.body)
                and expr.to_entity(instance.vars[536].value()) == player.entity
                and constraint(instance, player) is not None
                and contact_target(self.combat, bot, player) is not None)

    def release(self, source):
        self.admissions.pop(source, None)
        for player in self.combat.match.players:
            controller = player.controller_component
            changed = False
            for instance in list(controller.instances.values()):
                if instance.graph.index == 0x1177 and instance.cached_source_entity == source:
                    controller.destroy(instance)
                    changed = True
            if changed and player.body_script is not None:
                player._track_movement_mods(player.body_script.frame)
        self.health_clock = {k: v for k, v in self.health_clock.items() if k[0] != source}
        self.bindings.pop(source, None)

    def cancel(self, bot, instance):
        """A failed/lost contact ends the original held attack and its victim scope."""
        self.release(bot.entity)
        component = instance.component
        button = expr.to_int(instance.evaluate(instance.graph.node(3).field('m_logicalButton')))
        component.edges([(button, False)])
        component.run_now()

    def update(self):
        fight = self.combat
        self.admissions.clear()
        active = set()
        for bot in fight.match.bots:
            if bot.body != ASSASSIN_BODY:
                continue
            instance = attack(bot)
            if instance is None:
                self.release(bot.entity)
                continue
            if fight.is_stunned(bot) or fight.is_asleep(bot):
                self.cancel(bot, instance)
                continue
            target_id = expr.to_entity(instance.vars[536].value())
            player = next((p for p in fight.match.players if p.entity == target_id and not p.gone
                           and p.has_body and p.body_script is not None), None)
            if (player is None or victim_scope(player, bot.entity) is None
                    or self.bindings.get(bot.entity) != (player.entity, player.body)
                    or constraint(instance, player) is None):
                self.cancel(bot, instance)
                continue
            target = contact_target(fight, bot, player)
            if target is None:
                self.cancel(bot, instance)
                continue
            self._place(bot, player, instance)
            state = instance.states.get(13)
            if state is None or not state.active:
                continue  # original1s Wait24 has not entered ModifyHealth15
            mode = expr.to_int(state.evaluate(state.node.field('m_95F1FB0B'), watch=False))
            amount = expr.to_float(state.evaluate(
                state.node.field('m_modifyHealth')['m_amount'], watch=False))
            if mode != 1 or not state.node.field('m_7B2B3F5C'):
                raise ValueError('unsupported Assassin repeated-health mode')
            if amount <= 0:
                continue
            key = (bot.entity, instance.id, state.counter, player.body)
            active.add(key)
            previous = self.health_clock.setdefault(key, state.start)
            now = round(fight.now * 1000)
            elapsed = max(0, now - previous)
            if elapsed:
                self.health_clock[key] = now
                fight._damage(bot, target, amount * elapsed / 1000, False, None, fight.tick,
                              world_tick=fight.tick, beam=True)
                if fight.vitals_of(target).dead:
                    self.release(bot.entity)
        self.health_clock = {k: v for k, v in self.health_clock.items() if k in active}
        for player in fight.match.players:
            for instance in list(player.controller_component.instances.values()):
                if instance.graph.index == 0x1177 and not self.controlled(player):
                    player.controller_component.destroy(instance)
                    if player.body_script is not None:
                        player._track_movement_mods(player.body_script.frame)
