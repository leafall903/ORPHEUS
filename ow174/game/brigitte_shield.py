"""Host the finite pool attached by 0EE3's authored shield pet.

The 5DA0 mesh is authored. Its aim-relative pose, the pool and the break
lockout are reconstructed without a separate 0EE4 receiver VM.
"""

import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import barrier_mesh
from ow174.game.script import expr, runtime

HERO = 0x02E0000000000195
GRAPH = 0x0EE3
WHIP = 0x1259
FLAIL = 0x1248
FLAIL_STATES = (7, 8)
HALF_ANGLE = math.radians(60)
REGEN_DELAY = 2.0
REGEN_RATE = 85.0
BREAK_DELAY = 5.0
POSE_FORWARD = 0.5
_mesh_path = os.environ.get("ORPHEUS_BRIGITTE_COLLISION")
_mesh_digest = "7b5144339f3bd9ce503bd322987766d2d3ec8eb42d892242f1a122cfdc6548a6"
GEOMETRY_SHA256 = _mesh_digest if _mesh_path else None


def load_geometry():
    if not _mesh_path:
        return None
    descriptor = barrier_mesh.load_deployed_geometry(
        Path(_mesh_path), GEOMETRY_SHA256, "orpheus-brigitte-barrier-v1",
    )
    bottom = min(row[i] for row in descriptor.triangles for i in (1, 4, 7))
    rear = min(row[i] for row in descriptor.triangles for i in (2, 5, 8))
    return descriptor, bottom, rear


def pose(mover, bottom, rear, radius, height):
    yaw, pitch = mover.yaw * math.tau / 65536, mover.pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    right, up, forward = (cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp)
    # At level pitch the authored mesh's lower edge rests on the floor.
    # Keep the rear of the mesh beyond the wearer's capsule.
    support = max(forward[1] * radius, forward[1] * (height - radius)) + radius
    distance = max(POSE_FORWARD, support - rear + 0.02)
    centre = tuple(p - bottom * u + distance * f
                   for p, u, f in zip(mover.position, up, forward, strict=True))
    return centre, (right, up, forward)


def blocks_whip(combat, player, owner):
    """Apply the live Deflect cone to a Whip Shot body contact."""
    from ow174.game.combat import _dot, _number, _sub, aim

    if getattr(getattr(owner, "hero", None), "guid", None) != 0x02E0000000000029:
        return False
    script = owner.body_script
    if script is None or script.retired or script.component.strict or script.component.owner_dead:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index != 0x03A3:
            continue
        state = instance.states.get(10)
        if state is None or not state.active or state.ending:
            continue
        angle = _number(state.evaluate(state.node.field("m_025D0C39"), watch=False), 0.0)
        toward = _sub(combat._eye(player), combat._eye(owner))
        facing = aim(owner.mover.yaw, owner.mover.pitch)
        return _dot(facing, toward) >= math.hypot(*toward) * math.cos(math.radians(angle))
    return False


@dataclass
class WhipFlight:
    player: object
    body: int
    instance: object
    position: tuple
    direction: tuple
    speed: float
    remaining: float
    amount: float
    loadout: int
    last: float
    radius: float


@dataclass
class Shield:
    instance: object
    entity: int
    health: float
    maximum: float
    last: float
    regen_after: float = 0.0
    broken_until: float = 0.0
    raised: bool = False

    def active(self):
        state = self.instance.states.get(5)
        return self.health > 0 and state is not None and state.active and not state.ending

    def publish(self):
        self.instance.write((expr.INSTANCE, 586), expr.f32(self.health))
        state = self.instance.states.get(32)
        if state is not None and state.active:
            state.dependency_changed()


class Shields:
    def __init__(self, combat):
        self.combat = combat
        self.geometry = load_geometry()
        self.by_body = {}
        self.whip_seen = {}
        self.whips = []
        self.flail_hits = {}

    def retire(self, body):
        self.by_body.pop(body, None)
        self.flail_hits.pop(body, None)
        self.whip_seen = {key: value for key, value in self.whip_seen.items() if key[0] != body}
        self.whips[:] = [flight for flight in self.whips if flight.body != body]

    def create(self, instance, node):
        if self.geometry is None or (instance.graph.index, node.pos) != (GRAPH, 8):
            return NotImplemented
        if node.field("m_entityDefinition")["m_entityDef"] != "0x0400000000002284":
            raise ValueError("Brigitte shield definition changed")
        maximum = expr.to_float(instance.find_var(expr.INSTANCE, 587).value())
        if maximum <= 0:
            raise ValueError("authored Brigitte shield capacity is unavailable")
        entity = self.combat.next_bot
        if entity >= 0xD0000000:
            raise ValueError("barrier entity allocator exhausted")
        self.combat.next_bot += 1
        self.by_body[instance.component.entity] = Shield(instance, entity, maximum, maximum, self.combat.now)
        instance.write(expr.dynamic(node.field("m_B3019338")), expr.Entity(entity))
        return True

    def body_health(self, entity):
        for shield in self.by_body.values():
            if shield.entity == entity:
                return shield.health, shield.maximum
        return None

    def enemies(self, shooter):
        if self.geometry is None:
            return
        free_for_all = self.combat.match.game_map.free_for_all
        for target in self.combat._targets():
            if target.owner is shooter or (target.team == shooter.team and not free_for_all):
                continue
            shield = self.by_body.get(target.entity)
            if shield is not None and shield.active() and self.combat.now >= shield.broken_until:
                yield shield

    def shield_frame(self, shield):
        body = shield.instance.component.entity
        target = next((item for item in self.combat._targets() if item.entity == body), None)
        if target is None:
            return None
        _, bottom, rear = self.geometry
        mover = target.owner.mover
        centre, _ = pose(mover, bottom, rear, target.radius, target.height)
        return centre, mover.yaw, mover.pitch

    def ray(self, shield, origin, direction, reach, *, radius=0.0):
        frame = self.shield_frame(shield)
        if frame is None:
            return None
        return barrier_mesh.intersect(self.geometry[0], *frame, origin, direction, reach, radius=radius)

    def nearest(self, shield, point):
        frame = self.shield_frame(shield)
        if frame is None:
            return None
        return barrier_mesh.nearest(self.geometry[0], *frame, point)

    trace = barrier_mesh.DeployedContacts.trace
    blocked = barrier_mesh.DeployedContacts.blocked

    @staticmethod
    def lower(shield):
        ability = shield.instance.states.get(30)
        if ability is not None and ability.active:
            ability.stop()
        state = shield.instance.states.get(5)
        if state is not None and state.active:
            state.request(runtime.ABORT)
        shield.instance.component.run_now()

    def enforce_lockout(self, player):
        shield = self.by_body.get(player.body)
        if shield is not None and self.combat.now < shield.broken_until and shield.active():
            self.lower(shield)

    def update(self, now):
        for player in self.combat.match.players:
            shield = self.by_body.get(player.body)
            if shield is None or self.combat.is_dead(player):
                continue
            self.enforce_lockout(player)
            active = shield.active()
            if shield.raised and not active:
                shield.regen_after = now + REGEN_DELAY
            start = max(shield.last, shield.regen_after)
            if not active and now > start and shield.health < shield.maximum:
                shield.health = min(shield.maximum, shield.health + (now - start) * REGEN_RATE)
                shield.publish()
                shield.instance.component.run_now()
            shield.raised = active
            shield.last = now

    def damage(self, shield, amount):
        shield.health = max(0.0, shield.health - amount)
        shield.regen_after = self.combat.now + REGEN_DELAY
        shield.publish()
        if shield.health == 0:
            shield.broken_until = self.combat.now + BREAK_DELAY
            self.lower(shield)
            shield.raised = False
        else:
            shield.instance.component.run_now()

    def flail_command(self, player, command, script) -> None:
        if player.hero.guid != HERO or script.component.strict or command is None:
            return
        # Local import avoids a combat module import cycle.
        from ow174.game.combat import _dot, _number, _sub, aim, ray_world, segment_distance

        combat = self.combat
        eye, direction = combat._eye(player), aim(*command.aim())
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        live = {}
        old = self.flail_hits.get(player.body, {})
        for instance in tuple(script.component.instances.values()):
            if instance.graph.index != FLAIL:
                continue
            for index in FLAIL_STATES:
                state = instance.states.get(index)
                if state is None or not state.active:
                    continue
                key = (instance.id, index)
                counter, hit = old.get(key, (state.counter, set()))
                if counter != state.counter:
                    hit = set()
                live[key] = (state.counter, hit)
                modify = state.node.field("m_modifyHealth") or {}
                amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0.0)
                loadout = state.evaluate(modify.get("m_loadout"), watch=False)
                loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
                reach = _number(instance.vars[1648].value(), 0.0) if 1648 in instance.vars else 0.0
                if amount <= 0 or reach <= 0:
                    continue
                for target in combat._enemies(player):
                    if target.entity in hit:
                        continue
                    shown = combat.seen(target, view_tick)
                    if shown is None:
                        continue
                    a, b = shown.segment()
                    if segment_distance(eye, a, b) - shown.radius > reach:
                        continue
                    # Aim at the closest height on the target capsule's axis.
                    centre = (a[0], min(b[1], max(a[1], eye[1])), a[2])
                    delta = _sub(centre, eye)
                    distance = math.sqrt(_dot(delta, delta))
                    if distance > 1e-9:
                        toward = tuple(value / distance for value in delta)
                        if _dot(direction, toward) < math.cos(HALF_ANGLE):
                            continue
                        blocked = ray_world(combat._collision(), eye, toward, distance)
                        if blocked is not None and blocked < distance - shown.radius:
                            continue
                    hit.add(target.entity)
                    if blocks_whip(combat, player, target.owner):
                        continue
                    bubble = combat.bubbles.for_target(target.owner)
                    if bubble is not None:
                        combat._shield_damage(player, bubble, amount)
                        continue
                    combat._damage(player, target, amount, False, loadout, command.frame)
        if live:
            self.flail_hits[player.body] = live
        else:
            self.flail_hits.pop(player.body, None)

    def whip_command(self, player, command, script):
        if script.hero != HERO or script.component.strict:
            return
        for instance in script.component.instances.values():
            if instance.graph.index == WHIP:
                self._launch_whip(player, command, instance)

    def _launch_whip(self, player, command, instance) -> None:
        """Use the real graph's outgoing phase and resource values, not the input edge.

        Whip head sweep is reconstructed. The graph owns windup, lockout,
        cooldown start and readiness. No independent cooldown can bypass it.
        """
        from ow174.game.combat import _number, aim

        phase = instance.states.get(6)
        ability = instance.states.get(0)
        if phase is None or not phase.active or ability is None or not ability.flags & 1:
            return
        key = (player.body, instance.id)
        if self.whip_seen.get(key) == phase.counter:
            return
        self.whip_seen[key] = phase.counter
        node = next(n for n in instance.graph.nodes if n.state == 17)
        modify = node.field("m_modifyHealth") or {}
        amount = _number(instance.evaluate(modify.get("m_amount")), 0.0)
        loadout = instance.evaluate(modify.get("m_loadout"))
        speed = _number(instance.vars[681].value(), 0.0)
        reach = _number(instance.vars[1648].value(), 0.0)
        if amount <= 0 or speed <= 0 or reach <= 0 or not isinstance(loadout, expr.Asset):
            return
        self.whips.append(WhipFlight(
            player, player.body, instance, self.combat._eye(player), aim(*command.aim()),
            speed, reach, amount, loadout.guid, self.combat.now, 0.5,
        ))

    @staticmethod
    def _fortify_blocks_whip(owner) -> bool:
        script = getattr(owner, "body_script", None)
        if script is None:
            return False
        # The live 0CF4 movement modifier sets property 26 to zero.
        return any(instance.graph.index == 0x0CF4
                   and (modifier := instance.states.get(8)) is not None
                   and modifier.active and not modifier.ending
                   for instance in script.component.instances.values())

    def _fly_whips(self) -> None:
        if not self.whips:
            return
        from ow174.game.combat import _along, _number, ray_capsule, ray_world

        combat = self.combat
        for flight in list(self.whips):
            player = flight.player
            ability = flight.instance.states.get(0)
            if (player.body != flight.body or combat.is_dead(player)
                    or player.body_script is None or combat.is_stunned(player) or ability is None or not ability.flags & 1):
                self.whips.remove(flight)
                continue
            elapsed = max(0.0, combat.now - flight.last)
            step = min(flight.remaining, flight.speed * elapsed)
            flight.last = max(flight.last, combat.now)
            if step <= 0:
                continue
            best = None
            for target in combat._enemies(player):
                a, b = target.segment()
                distance = ray_capsule(
                    flight.position, flight.direction, a, b, target.radius + flight.radius,
                )
                if distance is not None and distance <= step and (best is None or distance < best[0]):
                    best = (distance, target)
            wall = ray_world(combat._collision(), flight.position, flight.direction, step, radius=flight.radius)
            if wall is not None and (best is None or wall <= best[0]):
                best = (wall, None)
            limit = best[0] if best else step
            barrier = combat._shield_trace(player, flight.position, flight.direction, limit, radius=flight.radius)
            if barrier is not None and (best is None or barrier[0] < limit):
                self.whips.remove(flight)
                combat._shield_damage(player, barrier[1], flight.amount)
                continue
            if best is None:
                flight.position = _along(flight.position, flight.direction, step)
                flight.remaining -= step
                if flight.remaining <= 1e-9:
                    self.whips.remove(flight)
                continue
            self.whips.remove(flight)
            target = best[1]
            if target is None:
                continue
            if blocks_whip(combat, player, target.owner):
                continue
            before = combat.vitals_of(target).total
            combat._damage(player, target, flight.amount, False, flight.loadout, combat.tick)
            if (combat.vitals_of(target).total < before and not combat.vitals_of(target).dead
                    and not self._fortify_blocks_whip(target.owner)):
                # Read the authored25/3 terms and live ruleset14131 at contact.
                # Force direction, native target417/vector433 and resistance remain unproved.
                settings = flight.instance.graph.node(32).field("m_modifyHealth") or {}
                horizontal_speed = _number(flight.instance.evaluate(settings.get("m_E33EFEE1")), 0.0)
                vertical_speed = _number(flight.instance.evaluate(settings.get("m_583C208B")), 0.0)
                dx, _, dz = flight.direction
                horizontal = math.hypot(dx, dz)
                if horizontal > 1e-9 and (horizontal_speed or vertical_speed):
                    velocity = (dx / horizontal * horizontal_speed, vertical_speed,
                                dz / horizontal * horizontal_speed)
                    mover = getattr(target.owner, "mover", None)
                    if mover is not None:
                        mover.impulse(velocity)
                    else:
                        target.owner.impulse(velocity, combat.tick, combat._collision(),
                                             free_for_all=combat.match.game_map.free_for_all)

    def bash_move(self, player):
        shield = self.by_body.get(player.body)
        if shield is None:
            return None
        instance = shield.instance
        window = instance.states.get(instance.graph.node(21).state)
        if window is None or not window.active:
            # The unhosted dash transition normally clears this hit latch.
            latch = instance.find_var(expr.INSTANCE, 488, create=False)
            if latch is not None and expr.truthy(latch.value()):
                instance.write((expr.INSTANCE, 488), False)
            return None
        from ow174.game.combat import _along, aim

        # Node171 supplies displacement per 60 Hz step.
        speed = expr.to_float(instance.find_var(expr.INSTANCE, 7726).value()) * 60
        hit = instance.states.get(instance.graph.node(15).state)
        if hit is not None and hit.counter and hit.start >= window.start:
            # Keep the remaining throttle modifier from advancing past the hit.
            return tuple(player.mover.position), speed
        direction = aim(player.mover.yaw, 0)
        return _along(player.mover.position, direction, speed * window.ms / 1000), speed

    def interrupt_bash(self, body, actor):
        shield = self.by_body.get(body)
        if shield is None:
            return
        instance = shield.instance
        window = instance.states.get(instance.graph.node(21).state)
        if window is None or not window.active:
            return
        window.request(runtime.ABORT)
        movement = instance.graph.node(20).state
        for key in tuple(actor.mover.mods):
            if key[:2] == (instance.id, movement):
                del actor.mover.mods[key]
        actor.mover.velocity = (0.0, actor.mover.velocity[1], 0.0)

    def bash_owner(self, instance):
        fight = self.combat
        shield = self.by_body.get(instance.component.entity)
        if shield is None or shield.instance is not instance:
            return None
        return next((p for p in fight.match.players if p.body == instance.component.entity
                     and p.has_body and p.spawned and not p.gone and not fight.is_dead(p)
                     and not fight.is_stunned(p)), None)

    def bash_targets(self, state):
        from ow174.game.combat import aim, ray_capsule, ray_world

        caster = self.bash_owner(state.instance)
        if caster is None:
            return ()
        fight = self.combat
        origin, direction = fight._eye(caster), aim(caster.mover.yaw, caster.mover.pitch)
        reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        hits = []
        for target in fight._enemies(caster):
            distance = ray_capsule(origin, direction, *target.segment(), target.radius)
            if distance is not None and distance <= reach:
                hits.append((distance, target.entity))
        if not hits:
            return ()
        distance, entity = min(hits)
        if (ray_world(fight._collision(), origin, direction, distance) is not None
                or fight._shield_trace(caster, origin, direction, distance) is not None
                or fight.bubbles.for_target(next(target.owner for target in fight._enemies(caster) if target.entity == entity)) is not None):
            return ()
        return (expr.Entity(entity),)

    def bash_hit(self, state):
        caster = self.bash_owner(state.instance)
        if caster is None:
            return
        fight = self.combat
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        if not selected:
            return
        entity = expr.to_entity(selected[0])
        target = next((target for target in fight._enemies(caster) if target.entity == entity), None)
        if target is None:
            return
        modify = state.node.field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(modify["m_amount"], watch=False))
        loadout = state.evaluate(modify["m_loadout"], watch=False)
        guid = loadout.guid if isinstance(loadout, expr.Asset) else None
        fight._damage(caster, target, amount, False, guid, fight.tick, world_tick=fight.tick)

    def bash_stun(self, instance, node):
        selected = instance.evaluate(node.field("m_targets"))
        caster = self.bash_owner(instance)
        if selected and caster is not None:
            fight = self.combat
            entity = expr.to_entity(selected[0])
            target = next((target for target in fight._enemies(caster) if target.entity == entity), None)
            if target is not None and fight.bubbles.for_target(target.owner) is None:
                duration = node.field("m_69F4B52C.m_6525E98B.m_1EB5A024")[0]["m_value"]
                from ow174.game import combat, fortify, phase_defenses

                seconds = expr.to_float(instance.evaluate(duration))
                deadline = fight.tick + math.ceil(seconds / combat.TICK)
                if (seconds > 0 and not fight.vitals_of(target).dead
                        and not phase_defenses.phase(target.owner)[0]
                        and not fortify.control_flag_active(target.owner)
                        and fight._replace_timed_control(target, deadline * 16)):
                    fight.stuns[target.entity] = deadline
                    fight._interrupt(target)
        return True
