"""Brigitte's first server combat bridge for build 104319.

1248's actual ModifyHealth states own timing, amount and loadout. Target selection
is reconstructed: a 120-degree forward volume at the authored reach, against the
shared body capsules, with static occlusion. This is NOT a native TrackTargets
implementation or original-client acceptance. Sweep/hardpoint geometry,
native pet entities, general projectile barriers, bash and Rally still require
their remaining engine consumers. The shield adapter below is reconstructed.
Inspire below reconstructs the timed healing consumer from 12D8/11B9.
"""

import math
from dataclasses import dataclass

from ow174.game import reinhardt_barrier, sigma_barrier, ultimate, winston_barrier
from ow174.game.script import expr, graph

HERO = 0x02E0000000000195
OPPONENT_COUNTERS = {
    0x02E0000000000007: (0x007D, 74),  # Reinhardt Charge
    0x02E000000000012F: (0x0D06, 6),   # Doomfist Rocket Punch
}
COUNTER_CHILDREN = {0x007D: (153, 161), 0x0D06: (165, 164), 0x0EE3: (119, 121)}
FLAIL = 0x1248
FLAIL_STATES = (7, 8)
# Reconstruction, pending native sweep/hardpoint measurements.
HALF_ANGLE = math.radians(60)
INSPIRE = 0x12D8
FLAIL_LOADOUT = 0x0B900000000001A6
WHIP = 0x1259
WHIP_LOADOUT = 0x0B900000000001A3
REPAIR = 0x0EDD
BARRIER = 0x0EE3
CRYO_PHASE = frozenset((0x05FE,))
RALLY = 0x12BE
STUN = 0x224E


@dataclass
class InspireHeal:
    healer: object
    target: int
    until: float
    last: float
    rate: float


@dataclass
class RepairHeal(InspireHeal):
    #0EE1 captures21709/940 once; refresh changes40 but not that controller.
    controller: object
    picked_hero: int
    source_present: bool = True
    cleanup_at: float | None = None


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
class RepairCast:
    player: object
    target: int
    charges: float
    amount: float
    speed: float
    duration: float


@dataclass
class RepairDelivery:
    cast: RepairCast
    at: float


@dataclass
class Barrier:
    instance: object
    health: float
    maximum: float
    last: float
    damaged_at: float | None = None
    raised_at: float | None = None


@dataclass
class BashStun:
    target: object
    instance: object


class BrigitteCombat:
    def __init__(self, combat):
        self.combat = combat
        # body -> (instance, state) -> (activation counter, entities already hit)
        self.hits: dict[int, dict[tuple[int, int], tuple[int, set[int]]]] = {}
        self.inspire: dict[tuple[int, int], InspireHeal] = {}
        self.next_inspire: dict[int, float] = {}
        self.whip_seen: dict[tuple[int, int], int] = {}
        self.whips: list[WhipFlight] = []
        self.repair_seen: dict[tuple[int, int], int] = {}
        self.repair_casts: dict[int, RepairCast] = {}
        self.repair_deliveries: list[RepairDelivery] = []
        self.repairs: dict[tuple[int, int], RepairHeal] = {}
        self.barriers: dict[int, Barrier] = {}
        self.rally_seen: dict[tuple[int, int], int] = {}
        self.stuns: list[BashStun] = []

    def retire(self, body: int) -> None:
        for stun in list(self.stuns):
            if stun.instance.component.entity == body:
                self._drop_stun(stun)
        self.hits.pop(body, None)
        self.next_inspire.pop(body, None)
        # Issued11B9 belongs to its recipient, not the retired caster component.
        self.inspire = {key: heal for key, heal in self.inspire.items() if heal.target != body}
        self.whip_seen = {key: count for key, count in self.whip_seen.items() if key[0] != body}
        self.whips[:] = [flight for flight in self.whips if flight.body != body]
        self.repair_seen = {key: count for key, count in self.repair_seen.items() if key[0] != body}
        self.repair_casts.pop(body, None)
        self.repair_deliveries[:] = [
            delivery for delivery in self.repair_deliveries
            if delivery.cast.player.body != body and delivery.cast.target != body
        ]
        self.repairs = {key: heal for key, heal in self.repairs.items() if heal.target != body}
        self.barriers.pop(body, None)
        self.rally_seen = {key: count for key, count in self.rally_seen.items() if key[0] != body}

    @staticmethod
    def rally_active(component) -> bool:
        return not component.owner_dead and any(
                   instance.graph.index == RALLY and (state := instance.states.get(0)) is not None
                   and state.active for instance in component.instances.values())

    def died(self, player) -> None:
        """Stop the independent aura phase on death; issued cohorts keep their own expiry."""
        if player.hero.guid != HERO or player.body_script is None:
            return
        from ow174.game.script import runtime

        component = player.body_script.component
        for instance in list(component.instances.values()):
            if (instance.graph.index == RALLY and (state := instance.states.get(0)) is not None
                    and state.active):
                state.request(runtime.ABORT)
        component.run_now()

    @staticmethod
    def charge_allowed(script) -> bool:
        """Honor known0B8B gates; unresolved owner initialization stays explicit."""
        if script.retired or script.component.owner_dead or BrigitteCombat.rally_active(script.component):
            return False
        gate = script.component.entity_var(2426).value()
        # Missing2426/F726 controller initialization is an unaccepted host
        # boundary. Preserve the existing numeric bridge for an unset gate.
        return ((gate is None or expr.truthy(gate))
                and not expr.truthy(script.component.entity_var(842).value())
                and expr.to_float(script.component.entity_var(342).value())
                < expr.to_float(script.component.entity_var(338).value()))

    def charge(self, player, amount: float, kind: str = "damage") -> None:
        """Use0B8B amount/scalar/cap expressions for reconstructed eligible events."""
        if (getattr(getattr(player, "hero", None), "guid", None) != HERO
                or not math.isfinite(amount) or amount <= 0):
            return
        script = player.body_script
        if script is None or self.combat.is_dead(player) or not self.charge_allowed(script):
            return
        current = expr.to_float(script.component.entity_var(342).value())
        value = ultimate.combat_value(script, amount, kind)
        if math.isfinite(value) and value > current:
            script.set_ult_charge(value)

    def _rally_command(self, player, instance) -> None:
        """Consume actual0.5s PlayScript repeat counter, reconstruct recipient armor."""
        from ow174.game.combat import _number, segment_distance

        tracker = instance.states.get(19)
        pulse = instance.states.get(20)
        if (tracker is None or not tracker.active or pulse is None
                or not self.rally_active(instance.component)):
            return
        key = player.body, instance.id
        count = (pulse.counter - self.rally_seen.get(key, 0)) & 0xFFFF
        if count == 0:
            return
        self.rally_seen[key] = pulse.counter
        radius = _number(instance.vars[11861].value(), 0.0)
        cap = _number(instance.vars[1274].value(), 0.0)
        interval = _number(instance.evaluate(pulse.node.field("m_timeout")), 0.0)
        amount = _number(instance.vars[13240].value(), 0.0) * interval
        expiry_node = next(n for n in graph.graph(0x12C1).nodes if n.state == 6)
        lifetime = _number(instance.evaluate(expiry_node.field("m_timeout")), 0.0)
        if amount <= 0 or radius <= 0 or cap <= 0 or lifetime <= 0:
            return
        combat = self.combat
        centre = tuple(player.mover.position)
        for target in combat._targets():
            allied = not combat.match.game_map.free_for_all and target.team == player.team
            if target.owner is not player and not allied:
                continue
            vitals = combat.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            # Reconstructed aura; TrackTargets' m_0297060F LOS flag is clear.
            # Exact filter/edge policy remains open.
            for offset in reversed(range(min(count, 32))):
                at = pulse.start / 1000 - offset * interval
                vitals.grant_armour(amount, cap, at + lifetime)
            combat._sync_body_health(target.owner, vitals)

    def _expire_rally(self) -> None:
        for target in self.combat._targets():
            vitals = self.combat.vitals.get(target.entity)
            if vitals is not None and vitals.bonus_armour and vitals.expire_armour(self.combat.now) > 0:
                self.combat._sync_body_health(target.owner, vitals)

    def native(self, cfg, instance):
        if (instance.graph.index == STUN and cfg.get("$") == "STU_AC829876"
                and cfg == instance.graph.node(9).field("m_condition")):
            # Exact authored ordinary-actor filter on an explicitly hosted hit
            # recipient.224E's own2547/priority checks still decide immunity.
            for stun in self.stuns:
                if stun.instance is instance:
                    return any(target.owner is stun.target and not self.combat.vitals_of(target).dead
                               for target in self.combat._targets())
            return NotImplemented
        """Explicit reconstructed owner-barrier HP query for0EE3's missing Pet.

        This exact query supplies its existing Stack32; it does not pretend a
        pet entity has been created or provide a global engine health getter.
        """
        if (instance.graph.index != BARRIER or instance.component.world.hero != HERO
                or cfg.get("$") != "STU_9B7648C3"
                or cfg.get("m_entity", {}).get("m_identifier") != 13214):
            return NotImplemented
        body = instance.component.entity
        if body not in self.barriers:
            maximum = float(instance.vars[587].value()) if 587 in instance.vars else 250.0
            self.barriers[body] = Barrier(instance, maximum, maximum, self.combat.now)
        return expr.f32(self.barriers[body].health)

    def _barrier_active(self, player, barrier) -> bool:
        state = barrier.instance.states.get(30)
        return (player.spawned and not self.combat.is_dead(player) and barrier.health > 1
                and state is not None and state.active and bool(state.flags & 1))

    @staticmethod
    def _sync_barrier(barrier) -> None:
        state = barrier.instance.states.get(32)
        if state is not None and state.active:
            state.dependency_changed()
            barrier.instance.component.run_now()

    def _update_barriers(self) -> None:
        combat = self.combat
        for player in combat.match.players:
            barrier = self.barriers.get(player.body)
            if barrier is None or combat.is_dead(player):
                continue
            now = combat.now
            if self._barrier_active(player, barrier):
                barrier.raised_at = now
            else:
                deadlines = [barrier.last]
                if barrier.damaged_at is not None:
                    deadlines.append(barrier.damaged_at + 2)
                if barrier.raised_at is not None:
                    deadlines.append(barrier.raised_at + 2)
                start = max(deadlines)
                if now > start and barrier.health < barrier.maximum:
                    # 0EE4/Wait18=2s; ModifyHealth20's base rate is85/s.
                    barrier.health = min(barrier.maximum, barrier.health + (now - start) * 85)
                    self._sync_barrier(barrier)
            barrier.last = max(barrier.last, now)

    def barrier_ray(self, shooter, origin, direction, length, *, radius=0.0, ignore_body=None, team=None):
        """First contact with the existing reconstructed held shield faces."""
        hits = self.barrier_contacts(shooter, origin, direction, length, radius=radius,
                                     ignore_body=ignore_body, team=team)
        hit = min(hits, key=lambda hit: hit[0], default=None)
        return None if hit is None else (hit[0], hit[2])

    def barrier_contacts(self, shooter, origin, direction, length, *, radius=0.0, ignore_body=None, team=None):
        """Original face geometry, including explicit captured affiliation."""
        from ow174.game.combat import _along, _dot, _sub

        for barrier, centre, normal, right, up in self._barrier_faces(shooter, team=team):
            if barrier.instance.component.entity == ignore_body:
                continue
            denominator = _dot(direction, normal)
            if denominator >= -1e-9:
                continue
            centre = _along(centre, normal, radius)
            distance = _dot(_sub(centre, origin), normal) / denominator
            # The face and wearer capsule can meet at the same point.
            # Keep the face eligible when the two ray solves round differently.
            if distance < -1e-6 or distance > length + 1e-9:
                continue
            distance = min(length, max(0.0, distance))
            offset = _sub(_along(origin, direction, distance), centre)
            if abs(_dot(offset, right)) <= 1 + radius and abs(_dot(offset, up)) <= 1.1 + radius:
                owner = next(player for player in self.combat.match.players
                             if player.body == barrier.instance.component.entity)
                yield distance, owner, barrier

    def _barrier_faces(self, shooter, *, team=None):
        from ow174.game.combat import _along, aim

        team = shooter.team if team is None else team
        for player in self.combat.match.players:
            if (player is shooter or (not self.combat.match.game_map.free_for_all
                                      and player.team == team)):
                continue
            barrier = self.barriers.get(player.body)
            if barrier is None or not self._barrier_active(player, barrier):
                continue
            normal = aim(player.mover.yaw, player.mover.pitch)
            x, y, z = player.mover.position
            centre = _along((x, y + 1, z), normal, 0.5)
            yaw = player.mover.yaw * 2 * math.pi / 65536
            right = (math.cos(yaw), 0, -math.sin(yaw))
            up = (normal[1] * right[2], normal[2] * right[0] - normal[0] * right[2],
                  -normal[1] * right[0])
            yield barrier, centre, normal, right, up

    def blast_barriers(self, shooter, origin, blast):
        """Reconstructed splash against the closest point on each visible front face.

        Snapshot these contacts before damaging shields so a breaking shield
        still covers bodies from this single explosion. Native sampling is open.
        """
        from ow174.game.combat import _dot, _sub

        contacts = []
        for barrier, centre, normal, right, up in self._barrier_faces(shooter):
            delta = _sub(origin, centre)
            if _dot(delta, normal) < -1e-6:
                continue
            horizontal = max(-1, min(1, _dot(delta, right)))
            vertical = max(-1.1, min(1.1, _dot(delta, up)))
            point = tuple(centre[k] + horizontal * right[k] + vertical * up[k] for k in range(3))
            delta = _sub(point, origin)
            distance = math.sqrt(_dot(delta, delta))
            amount = blast.amount * blast.splash(distance)
            if amount <= 0:
                continue
            if distance > 1e-9:
                direction = tuple(value / distance for value in delta)
                wall = self.combat.ray_obstruction(origin, direction, distance)
                if wall is not None and wall < distance - 1e-6:
                    continue
            contacts.append((barrier, amount))
        return contacts

    def damage_barrier(self, barrier, amount) -> None:
        """A stopped shot spends shield HP, never hero armor or ultimate charge."""
        if amount <= 0 or barrier.health <= 1:
            return
        # The authored owner graph treats exactly1 as broken (Switch31) and
        # <=1 as unusable (node99). Preserve that terminal Pet health sentinel.
        barrier.health = max(1, barrier.health - amount)
        barrier.damaged_at = self.combat.now
        self._sync_barrier(barrier)
        if barrier.health <= 1:
            # Supply the missing Pet-break stop. Existing Stack32->Switch31
            # still sets the graph-owned5s cooldown; no parallel timer is used.
            ability = barrier.instance.states.get(30)
            if ability is not None:
                ability.stop()
                barrier.instance.component.run_now()

    def prepare(self, player, command, script) -> None:
        """Reconstruct Repair Pack's allied aim target before native ability checks.

        Target ranking/cone are a reconstruction of0EDD's TrackTargets16. Charge,
        start conditions, target536 capture and lockout remain graph-owned.
        """
        opponent = OPPONENT_COUNTERS.get(player.hero.guid)
        if opponent is not None:
            graph_id, tracker_id = opponent
            instance = next((item for item in script.component.instances.values()
                             if item.graph.index == graph_id), None)
            if instance is not None:
                counter = self._bash_target(player, command, instance.states.get(tracker_id),
                                            counter=True, counter_hero=HERO)
                if counter is not None and self._resolve_charge_counter(player, counter, instance):
                    return
                if graph_id == 0x007D:
                    windup = instance.states.get(37)
                    if windup is not None and windup.active:
                        # This empty timer waits for the Charge host's phase handoff.
                        windup.request_finish()
            return
        if player.hero.guid != HERO:
            return
        from ow174.game.combat import _dot, _number, _sub, aim, segment_distance

        combat = self.combat
        eye, direction = combat._eye(player), aim(*command.aim())
        for instance in tuple(script.component.instances.values()):
            if instance.graph.index == BARRIER:
                self._prepare_bash(player, command, instance)
            if instance.graph.index != REPAIR:
                continue
            reach = _number(instance.vars[1648].value(), 0.0) if 1648 in instance.vars else 0.0
            found = []
            for target in combat._targets():
                if (target.owner is player or combat.match.game_map.free_for_all
                        or target.team != player.team or combat.vitals_of(target).dead):
                    continue
                a, b = target.segment()
                if segment_distance(eye, a, b) - target.radius > reach:
                    continue
                centre = (a[0], min(b[1], max(a[1], eye[1])), a[2])
                delta = _sub(centre, eye)
                distance = math.sqrt(_dot(delta, delta))
                if distance > 1e-9:
                    toward = tuple(value / distance for value in delta)
                    alignment = _dot(direction, toward)
                    if alignment < math.cos(math.radians(10)):
                        continue
                    blocked = combat.ray_obstruction(eye, toward, distance)
                    if blocked is not None and blocked < distance - target.radius:
                        continue
                else:
                    alignment = 1
                found.append((-alignment, distance, target.entity))
            entity = min(found)[2] if found else None
            instance.write((expr.INSTANCE, 375), (expr.Entity(entity),) if entity is not None else ())

    def bash_barrier_distance(self, player, origin, direction, reach, *, ignore_body=None):
        held = self.barrier_ray(player, origin, direction, reach, ignore_body=ignore_body)
        deployed = sigma_barrier.barrier_intersection(self.combat, player, origin, direction, reach)
        field = reinhardt_barrier.intersection(self.combat, player, origin, direction, reach)
        dome = winston_barrier.intersection(self.combat, player, origin, direction, reach)
        protective = self.combat.orisa_barriers.intersection(player, origin, direction, reach)
        ice = self.combat.walls.cast(origin, direction, reach)
        return min((hit[0] for hit in (held, deployed, field, dome, protective, ice)
                    if hit is not None), default=None)

    def _bash_travel_distance(self, player, origin, direction, reach):
        from ow174.game.combat import ray_capsule

        nearest = self.bash_barrier_distance(player, origin, direction, reach)
        for target in self.combat._targets():
            if target.owner is player or not self.combat._invulnerable(target.owner, phases=CRYO_PHASE):
                continue
            a, b = target.segment()
            distance = ray_capsule(origin, direction, a, b, target.radius)
            if distance is not None and distance <= reach and (nearest is None or distance < nearest):
                nearest = distance
        return nearest

    def bash_movement_limit(self, player):
        """Provide a travel bound only while the authored dash modifier is active."""
        if player.hero.guid != HERO:
            return None
        instance = next((item for item in player.body_script.component.instances.values()
                         if item.graph.index == BARRIER), None)
        dash = instance.states.get(14) if instance is not None else None
        if dash is None or not dash.active or dash.ending:
            return None
        x, y, z = player.mover.position
        origin = (x, y + 1, z)

        def travel_limit(direction, reach):
            distance = self._bash_travel_distance(player, origin, direction, reach)
            if distance is not None and distance < reach:
                # Queue the authored dash stop for this pose's body-script advance.
                instance.write((expr.INSTANCE, 488), True)
            return distance

        return travel_limit

    def _prepare_bash(self, player, command, instance) -> None:
        """Resolve the counter query before supplying ordinary Bash contacts."""
        counter = self._bash_target(player, command, instance.states.get(75), counter=True)
        if counter is not None and self._resolve_charge_counter(player, counter, instance):
            return
        target = self._bash_target(player, command, instance.states.get(12))
        instance.write((expr.INSTANCE, 375), (expr.Entity(target.entity),) if target is not None else ())

    def _bash_target(self, player, command, tracker, *, counter=False, counter_hero=None):
        from ow174.game.combat import _dot, _number, _sub, aim, ray_world, segment_distance

        if tracker is None or not tracker.active:
            return None
        radius = _number(tracker.evaluate(tracker.node.field("m_radius"), watch=False), 0.0)
        angle = _number(tracker.evaluate(tracker.node.field("m_A30B383E"), watch=False), 0.0)
        offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
        x, y, z = player.mover.position
        if not isinstance(offset, expr.Vec3) and tracker.instance.graph.index == 0x0D06:
            # Reconstruct node14's eye-relative centre using its authored -0.18m bias.
            offset = expr.Vec3(0, self.combat._eye(player)[1] - y - 0.18, 0)
        if not isinstance(offset, expr.Vec3) or radius <= 0:
            return None
        yaw, pitch = command.aim()
        heading = yaw * math.tau / 65536
        dx, dz = math.sin(heading), math.cos(heading)
        origin = (x + dz * offset.x + dx * offset.z, y + offset.y,
                  z - dx * offset.x + dz * offset.z)
        direction = aim(yaw, pitch)
        found = []
        for target in self.combat._enemies(player):
            if counter_hero is not None:
                hero = getattr(target.owner, "hero", None)
                if getattr(hero, "guid", None) != counter_hero:
                    continue
            if counter and not self._opposing_charge(target.owner, yaw):
                continue
            a, b = target.segment()
            if segment_distance(origin, a, b) - target.radius > radius:
                continue
            centre = (a[0], min(b[1], max(a[1], origin[1])), a[2])
            delta = _sub(centre, origin)
            distance = math.sqrt(_dot(delta, delta))
            if distance > 1e-9:
                toward = tuple(value / distance for value in delta)
                if _dot(direction, toward) < math.cos(math.radians(angle / 2)):
                    continue
                wall = ray_world(self.combat._collision(), origin, toward, distance)
                if wall is not None and wall < distance - target.radius:
                    continue
                ignore_body = target.entity if counter_hero == HERO else None
                barrier = self.bash_barrier_distance(
                    player, origin, toward, distance, ignore_body=ignore_body,
                )
                if barrier is not None and barrier <= distance - target.radius:
                    continue
            found.append((distance, target.entity, target))
        return min(found, default=(None, None, None))[2]

    def _bash_command(self, player, command, instance, old, live) -> None:
        """Consume the graph's actual damage activation, once per selected target."""
        from ow174.game import brigitte_force, defenses
        from ow174.game.combat import TICK, _number, aim

        state = instance.states.get(10)
        if state is None or not state.active:
            return
        key = instance.id, 10
        counter, hit = old.get(key, (state.counter, set()))
        if counter != state.counter:
            hit = set()
        live[key] = state.counter, hit
        if hit:
            return  # ModifyHealth10 applies once when begun, not to later tracker replacements.
        modify = state.node.field("m_modifyHealth") or {}
        amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0.0)
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        spec = brigitte_force.evaluate_bash(state)
        impact_tick = round(self._whip_world_time(player, command.frame * instance.component.frame_ms) / TICK)
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        # The missing one-shot health consumer must finish after application.
        # Leaving10 active makes later native begin requests no-ops forever.
        # Snapshot targets first; stop may clear the graph's live tracker data.
        state.request_finish()
        instance.component.run_now()
        if not isinstance(selected, tuple) or not isinstance(loadout, expr.Asset):
            return
        for target in self.combat._enemies(player):
            if expr.Entity(target.entity) not in selected or target.entity in hit:
                continue
            hit.add(target.entity)
            before = self.combat.vitals_of(target).total
            self.combat._damage(player, target, amount, False, loadout.guid, command.frame,
                                world_tick=impact_tick)
            if (not self.combat.vitals_of(target).dead
                    and self.combat.vitals_of(target).total < before):
                controlled = self.host_control(player, target, instance, instance.graph.node(13))
                if spec is not None and controlled:
                    # Reconstruct direction from the horizontal Bash aim. The control gate
                    # preserves the active Fortify/Particle Barrier recipient policy.
                    direction = aim(command.aim()[0], 0)
                    owner = target.owner
                    mover = self.combat.knockbacks.active.get(target.entity)
                    if mover is None:
                        mover = (getattr(owner, "control_mover", None)
                                 or getattr(owner, "impulse_mover", None)
                                 or getattr(owner, "mover", None))
                    incoming = tuple(mover.velocity if mover is not None else owner.velocity)
                    # Retain the host impact snapshot/axis policy. Native argument9
                    # actor capture and direction resolution remain unaccepted.
                    impulse = spec.impulse(direction, incoming)
                    response = defenses.knockback_scale(owner)
                    impulse = expr.Vec3(*(axis * response for axis in impulse))
                    self.combat.knockbacks.apply(target, impulse, impact_tick=impact_tick,
                                                source_entity=player.body)

    def _opposing_charge(self, owner, yaw) -> bool:
        from ow174.game.combat import _dot, aim

        script = getattr(owner, "body_script", None)
        mover = getattr(owner, "mover", None)
        if script is None or mover is None or self.combat.is_stunned(owner):
            return False
        charging = script.component.entity_var(596, create=False)
        if charging is None or not expr.truthy(charging.value()):
            return False
        return _dot(aim(yaw, 0), aim(mover.yaw, 0)) < 0

    def _resolve_charge_counter(self, player, target, source) -> bool:
        own = next((item for item in self.combat._targets() if item.owner is player), None)
        if own is None:
            return False
        self_node, target_node = COUNTER_CHILDREN[source.graph.index]
        self.host_control(player, own, source, source.graph.node(self_node))
        self.host_control(player, target, source, source.graph.node(target_node))
        return True

    def host_control(self, player, target, source, node) -> bool:
        """Host the authored 224E request and report whether this child applies control.

        Duration/control are graph-owned; no independent stun clock or forced
        state activation is used. Filter/record ownership are reconstructed.
        """
        from ow174.game.script import play

        script = getattr(target.owner, "body_script", None)
        if script is None:
            return False
        resource = play.resolve_resource(source, node)
        if resource is None or resource.guid & 0xFFFF != STUN:
            return False
        child = script.component.play_loaded_graph(
            graph.graph(STUN), source=player.body, overrides=resource.overrides,
            policy=node.field("m_7ABA409C"), instigator=player.body,
        )
        if child is None:
            return False
        self.stuns.append(BashStun(target.owner, child))
        script.component.run_now()
        phase = child.states.get(20)
        controlled = phase is not None and phase.active
        if controlled:
            self.combat._interrupt(target)
        mover = getattr(target.owner, "mover", None)
        if mover is not None:
            from ow174.game.mover import statescript_mods

            mover.track_mods(statescript_mods(script.component), self.combat.tick)
        return controlled

    def _drop_stun(self, stun):
        stun.instance.component.destroy(stun.instance)
        self.stuns.remove(stun)

    def controlled(self, body):
        """Expose the hosted graph's active control phase to shared input policy."""
        return any(stun.instance.component.entity == body
                   and (phase := stun.instance.states.get(20)) is not None and phase.active
                   for stun in self.stuns)

    def _update_stuns(self):
        living = {target.entity: target for target in self.combat._targets()
                  if not self.combat.vitals_of(target).dead}
        for stun in list(self.stuns):
            component = stun.instance.component
            target = living.get(component.entity)
            script = getattr(stun.target, "body_script", None)
            phase = stun.instance.states.get(20)
            if (target is None or target.owner is not stun.target or script is None
                    or script.component is not component or phase is None or not phase.active):
                self._drop_stun(stun)

    def damaged(self, player, loadout) -> None:
        """Reconstructed proc: successful flail damage snapshots living visible allies.

        12D8 supplies 1s cooldown, 20m radius, 90 healing and 6s duration;
        11B9 consumes a recipient's timed ModifyHealth. Engine targeting,
        anti-heal/healing modifiers and native presentation remain open.
        """
        if getattr(getattr(player, "hero", None), "guid", None) != HERO or loadout not in (
            FLAIL_LOADOUT, WHIP_LOADOUT,
        ):
            return
        combat = self.combat
        if combat.now < self.next_inspire.get(player.body, -math.inf):
            return
        instance = next((i for i in player.body_script.component.instances.values()
                         if i.graph.index == INSPIRE), None)
        if instance is None:
            return
        from ow174.game.combat import _dot, _number, _sub, segment_distance

        def value(index):
            return _number(instance.vars[index].value(), 0.0) if index in instance.vars else 0.0

        cooldown, radius, duration, amount = (value(index) for index in (472, 11861, 215, 499))
        if radius <= 0 or duration <= 0 or amount <= 0:
            return
        play = next(n for n in instance.graph.nodes if n.pos == 2)
        # Authored12D8 PlayScript policy3 replaces every11B9 on the recipient,
        # including another caster's instance (64D2C0/64CD10).
        if play.field("m_AA4DE426") != 3:
            return
        self._heal_effects(self.inspire)
        self.next_inspire[player.body] = combat.now + cooldown
        eye = combat._eye(player)
        for target in combat._targets():
            allied = not combat.match.game_map.free_for_all and target.team == player.team
            if target.owner is not player and not allied:
                continue
            if combat.vitals_of(target).dead:
                continue
            a, b = target.segment()
            if segment_distance(eye, a, b) - target.radius > radius:
                continue
            centre = (a[0], min(b[1], max(a[1], eye[1])), a[2])
            delta = _sub(centre, eye)
            distance = math.sqrt(_dot(delta, delta))
            if distance > 1e-9:
                toward = tuple(value / distance for value in delta)
                blocked = combat.ray_obstruction(eye, toward, distance)
                if blocked is not None and blocked < distance - target.radius:
                    continue
            # The recipient owns one11B9; replacement also changes its healer.
            for key, heal in list(self.inspire.items()):
                if heal.target == target.entity:
                    del self.inspire[key]
            self.inspire[player.body, target.entity] = InspireHeal(
                player, target.entity, combat.now + duration, combat.now, amount / duration,
            )

    def update(self) -> None:
        self._update_stuns()
        self._expire_rally()
        self._update_barriers()
        self._fly_whips()
        self._update_repair_lifetimes()
        self._deliver_repairs()
        self._update_repair_lifetimes()
        self._heal_effects(self.inspire)
        self._heal_effects(self.repairs)

    def _heal_effects(self, effects, *, now=None) -> None:
        if not effects:
            return
        from ow174.game import stats

        combat = self.combat
        when = combat.now if now is None else now
        targets = {target.entity: target for target in combat._targets()}
        for key, heal in list(effects.items()):
            target = targets.get(heal.target)
            if target is None or combat.vitals_of(target).dead:
                effects.pop(key)
                continue
            until = heal.until
            if isinstance(heal, RepairHeal) and heal.cleanup_at is not None:
                until = min(until, heal.cleanup_at)
            vitals = combat.vitals_of(target)
            elapsed = vitals.healing_time(heal.last, min(when, until))
            heal.last = max(heal.last, min(when, until))
            given = vitals.heal(elapsed * heal.rate)
            if given > 0:
                heal.healer.stats.add(stats.HEALING, given)
                if key[0] == heal.healer.body:
                    self.charge(heal.healer, given, "healing")
                combat._sync_body_health(target.owner, combat.vitals_of(target))
            if when >= until:
                effects.pop(key)

    def _update_repair_lifetimes(self) -> None:
        """Host0EE1 source-loss decisions from the server's body/controller roster.

        Native registry/controller callbacks, exact poll phase and operation7
        transport remain unaccepted. Numeric healing follows the issued request
        deadline without claiming a complete recipient-script implementation.
        """
        if not self.repairs:
            return
        from ow174.game.script.nodes import seconds_to_ms

        bodies = {target.entity for target in self.combat._targets()}
        for (source, _target), heal in self.repairs.items():
            present = source in bodies
            if not present and heal.source_present:
                controller = next((player for player in self.combat.match.players
                                   if player is heal.controller), None)
                picked = controller.hero.guid if controller is not None else None
                if picked != heal.picked_hero:
                    heal.cleanup_at = (self.combat.now if heal.cleanup_at is None
                                       else min(heal.cleanup_at, self.combat.now))
                elif heal.cleanup_at is None:
                    timeout = graph.graph(0x0EE1).node(48).field("m_timeout")
                    delay = max(0, seconds_to_ms(expr.evaluate(timeout, expr.Context()))) / 1000
                    heal.cleanup_at = self.combat.now + delay
            # A rebind/reappearance changes the predicate but never cancels a
            # previously started Wait13; a later false edge can still terminate.
            heal.source_present = present

    def _repair_command(self, player, instance) -> None:
        """Capture the graph's locked target; launch only when it consumes a charge."""
        from ow174.game.combat import _number

        phase = instance.states.get(19)
        key = (player.body, instance.id)
        charges = _number(instance.vars[11].value(), 0.0)
        if phase is not None and phase.active and self.repair_seen.get(key) != phase.counter:
            self.repair_seen[key] = phase.counter
            target = expr.to_entity(instance.vars[536].value())
            if target:
                duration_node = next(n for n in graph.graph(0x0EE1).nodes if n.pos == 7)
                self.repair_casts[player.body] = RepairCast(
                    player, target, charges, _number(instance.vars[420].value(), 0.0),
                    _number(instance.vars[6368].value(), 0.0),
                    _number(instance.evaluate(duration_node.field("m_value")), 0.0),
                )
        cast = self.repair_casts.get(player.body)
        if cast is None:
            return
        if charges < cast.charges:
            self.repair_casts.pop(player.body)
            target = next((t for t in self.combat._targets() if t.entity == cast.target), None)
            if target is not None and cast.speed > 0 and cast.duration > 0:
                distance = math.dist(self.combat._eye(player), (
                    target.feet[0], target.feet[1] + target.eye, target.feet[2],
                ))
                self.repair_deliveries.append(RepairDelivery(cast, self.combat.now + distance / cast.speed))
        elif phase is None or not phase.active:
            self.repair_casts.pop(player.body)

    def interrupted(self, body: int) -> None:
        """Invalidate an unlaunched cast before authored abort paths spend its charge."""
        self.repair_casts.pop(body, None)

    def _deliver_repairs(self) -> None:
        """Reconstruct the homing delivery and0EE1's timed healing consumer.

        The cast graph consumes resources/recharges them.0EE1 duration2 and
        source amount110 imply55/s; repeated packs extend duration. Exact
        homing paths, target effect refresh rules and visual state remain open.
        """
        combat = self.combat
        self.repair_casts = {
            body: cast for body, cast in self.repair_casts.items() if not combat.is_dead(cast.player)
        }
        if not self.repair_deliveries:
            return
        targets = {t.entity: t for t in combat._targets()}
        for delivery in sorted(self.repair_deliveries, key=lambda item: item.at):
            if combat.now < delivery.at:
                continue
            self.repair_deliveries.remove(delivery)
            self._heal_effects(self.repairs, now=delivery.at)
            cast = delivery.cast
            target = targets.get(cast.target)
            if (target is None or combat.vitals_of(target).dead or combat.match.game_map.free_for_all
                    or target.team != cast.player.team or target.owner is cast.player):
                continue
            key = cast.player.body, cast.target
            #0EDD node42 tests the recipient's22726; node43 sends0F6F to
            # reuse its0EE1, extend22729 and rebind source40, across casters.
            prior = next((heal for heal in self.repairs.values() if heal.target == cast.target), None)
            for previous, heal in list(self.repairs.items()):
                if heal.target == cast.target:
                    del self.repairs[previous]
            until = max(delivery.at, prior.until) + cast.duration if prior else delivery.at + cast.duration
            last = prior.last if prior is not None else delivery.at
            self.repairs[key] = RepairHeal(
                cast.player, cast.target, until, last, cast.amount / cast.duration,
                prior.controller if prior is not None else cast.player,
                prior.picked_hero if prior is not None else cast.player.hero.guid,
                prior.source_present if prior is not None else True,
                prior.cleanup_at if prior is not None else None,
            )

    def _whip_world_time(self, player, milliseconds):
        from ow174.game.combat import TICK

        script = player.body_script
        birth = getattr(script, "brigitte_birth", None)
        if birth is None:
            return self.combat.now
        tick, frame = birth
        return tick * TICK + (milliseconds - frame * script.frame_ms) / 1000

    @staticmethod
    def _whip_owner_frame(player, world_tick):
        from ow174.game.combat import TICK

        script = player.body_script
        birth = getattr(script, "brigitte_birth", None)
        if birth is None:
            return script.frame
        tick, frame = birth
        return frame + math.ceil((world_tick - tick) * TICK * 1000 / script.frame_ms - 1e-7)

    def _launch_whip(self, player, command, instance) -> None:
        """Use the real graph's outgoing phase and resource values, not the input edge.

        Whip head sweep is reconstructed. The graph owns windup, lockout,
        cooldown start and readiness. No independent cooldown can bypass it.
        """
        from ow174.game.combat import _number

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
        captured = instance.vars[433].value() if 433 in instance.vars else None
        if amount <= 0 or speed <= 0 or reach <= 0 or not isinstance(loadout, expr.Asset):
            return
        if (not isinstance(captured, expr.Vec3) or not all(map(math.isfinite, captured))
                or not any(captured)):
            return
        self.whips.append(WhipFlight(
            player, player.body, instance, self.combat._eye(player), tuple(captured),
            speed, reach, amount, loadout.guid, self._whip_world_time(player, phase.start), 0.5,
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

    def _fly_whips(self, player_filter=None, *, through=None) -> None:
        if not self.whips:
            return
        from ow174.game.combat import TICK, _along, ray_capsule, ray_world

        combat = self.combat
        now = combat.now if through is None else min(combat.now, through)
        for flight in list(self.whips):
            player = flight.player
            if player_filter is not None and player is not player_filter:
                continue
            ability = flight.instance.states.get(0)
            if (player.body != flight.body or combat.is_dead(player)
                    or player.body_script is None or ability is None or not ability.flags & 1):
                self.whips.remove(flight)
                continue
            start = flight.last
            elapsed = max(0.0, now - start)
            step = min(flight.remaining, flight.speed * elapsed)
            flight.last = max(flight.last, now)
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
            wall = ray_world(combat._collision(), flight.position, flight.direction, step)
            if wall is not None and (best is None or wall <= best[0]):
                best = (wall, None)
            ice = combat.walls.cast(flight.position, flight.direction, step, radius=flight.radius)
            if ice is not None and (best is None or ice[0] < best[0]):
                best = (ice[0], None)
            else:
                ice = None
            barrier = self.barrier_ray(
                player, flight.position, flight.direction, min(step, best[0] if best else step),
                radius=flight.radius,
            )
            limit = barrier[0] if barrier is not None else best[0] if best else step
            deployed = sigma_barrier.barrier_intersection(
                combat, player, flight.position, flight.direction, limit,
            )
            field = reinhardt_barrier.intersection(
                combat, player, flight.position, flight.direction, limit, radius=flight.radius,
            )
            dome = winston_barrier.intersection(
                combat, player, flight.position, flight.direction, limit, radius=flight.radius,
            )
            protective = combat.orisa_barriers.intersection(
                player, flight.position, flight.direction, limit, radius=flight.radius,
            )
            if (protective is not None
                    and all(hit is None or protective[0] < hit[0] for hit in (dome, field, deployed))
                    and (protective[0] < limit or (barrier is None and best is None))):
                self.whips.remove(flight)
                combat.orisa_barriers.damage(protective[1], flight.amount)
                continue
            if (dome is not None
                    and all(hit is None or dome[0] < hit[0] for hit in (field, deployed))
                    and (dome[0] < limit or (barrier is None and best is None))):
                self.whips.remove(flight)
                dome[1].health = max(0.0, dome[1].health - flight.amount)
                continue
            if field is not None and (deployed is None or field[0] < deployed[0]) and (
                field[0] < limit or (barrier is None and best is None)
            ):
                self.whips.remove(flight)
                reinhardt_barrier.damage(combat, field[1], flight.amount)
                continue
            if deployed is not None and (deployed[0] < limit or (barrier is None and best is None)):
                self.whips.remove(flight)
                sigma_barrier.damage_barrier(combat, deployed[1], flight.amount)
                continue
            if barrier is not None:
                self.whips.remove(flight)
                self.damage_barrier(barrier[1], flight.amount)
                continue
            if best is None:
                flight.position = _along(flight.position, flight.direction, step)
                flight.remaining -= step
                if flight.remaining <= 1e-9:
                    self.whips.remove(flight)
                continue
            self.whips.remove(flight)
            if ice is not None:
                combat.walls.damage(ice[1], player, flight.amount)
                continue
            target = best[1]
            if target is None:
                continue
            before = combat.vitals_of(target).total
            contact_tick = math.ceil((start + best[0] / flight.speed) / TICK - 1e-7)
            owner_frame = self._whip_owner_frame(player, contact_tick)
            combat._damage(player, target, flight.amount, False, flight.loadout, owner_frame,
                           world_tick=contact_tick)
            if (combat.vitals_of(target).total < before and not combat.vitals_of(target).dead
                    and not self._fortify_blocks_whip(target.owner)):
                from ow174.game import brigitte_force, defenses
                from ow174.game.mover import IN_AIR

                flags = combat.match.script_movement_flags(target.entity, player.body)
                if type(flags) is not int:
                    continue
                spec = brigitte_force.evaluate_whip(flight.instance, target.entity, bool(flags & IN_AIR))
                if spec is None:
                    continue
                owner = target.owner
                mover = combat.knockbacks.active.get(target.entity)
                if mover is None:
                    mover = (getattr(owner, "control_mover", None)
                             or getattr(owner, "impulse_mover", None)
                             or getattr(owner, "mover", None))
                incoming = tuple(mover.velocity if mover is not None else owner.velocity)
                # Host impact snapshot, matching the existing shared producer policy.
                # Native argument9 actor/capture phase and D73830 remain unaccepted.
                impulse = spec.impulse(incoming)
                # Reuse the shared impact-time response policy once after forming
                # the delta; this does not identify native CC or D73830 ordering.
                response = defenses.knockback_scale(owner)
                impulse = expr.Vec3(*(axis * response for axis in impulse))
                combat.knockbacks.apply(target, impulse, impact_tick=contact_tick,
                                        source_entity=flight.body)

    def passive_command(self, player, script) -> None:
        """Consume an issued aura's graph pulses even while active input is controlled."""
        if player.hero.guid != HERO:
            return
        for instance in script.component.instances.values():
            if instance.graph.index == RALLY:
                self._rally_command(player, instance)

    def command(self, player, command, script) -> None:
        if player.hero.guid != HERO or command is None:
            return
        # Local import avoids a combat module import cycle.
        from ow174.game.combat import TICK, _dot, _number, _sub, aim, ray_world, segment_distance

        combat = self.combat
        eye, direction = combat._eye(player), aim(*command.aim())
        # Target history is keyed by world ticks; HIT labels remain owner frames.
        world, owner = script.world_frame_anchor or getattr(
            script, "brigitte_birth", (combat.tick, command.frame),
        )
        elapsed_ms = (command.frame - owner) * script.frame_ms - command.view_delay(player.quanta) * 1000
        view_tick = world + elapsed_ms / round(TICK * 1000)
        live = {}
        old = self.hits.get(player.body, {})
        for instance in tuple(script.component.instances.values()):
            if instance.graph.index == BARRIER:
                self._bash_command(player, command, instance, old, live)
            if instance.graph.index == REPAIR:
                self._repair_command(player, instance)
            if instance.graph.index == WHIP:
                self._launch_whip(player, command, instance)
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
                        if combat.walls.cast(eye, toward, max(0.0, distance - shown.radius)):
                            # A broken pillar cannot expose this target to the same swing.
                            hit.add(target.entity)
                            continue
                    hit.add(target.entity)
                    combat._damage(player, target, amount, False, loadout, command.frame)
                for piece in combat.walls.flail_contacts(eye, direction, reach, math.cos(HALF_ANGLE)):
                    if piece.entity not in hit:
                        hit.add(piece.entity)
                        combat.walls.damage(piece, player, amount)
        self._fly_whips(player, through=self._whip_world_time(
            player, command.frame * script.frame_ms,
        ))
        if live:
            self.hits[player.body] = live
        else:
            self.hits.pop(player.body, None)
