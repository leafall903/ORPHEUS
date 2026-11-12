"""Data-derived Baptiste world effects missing from the legacy body VM.

This narrow adapter consumes actual body-state activations, so it cannot bypass
the authored ability gates/cooldowns. It does not claim native filter, combat-mod
or presentation equivalence. Regenerative Burst's radius/duration/amount come
from 19A5; 1B2D doubles the amount when source equals recipient.
"""

import math
from dataclasses import dataclass, field

from ow174.game import actors, weapon_spread
from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000221
BURST_GRAPH, TRACK_STATE = 0x19A5, 4
RADIUS, DURATION, AMOUNT = 11861, 215, 499
GRENADE_RADIUS, GRENADE_HEAL = 19948, 17411
TICK = 0.016


def primary_direction(state, forward, owner_entity, counter, shot):
    """1932 node 10, with the server aim basis and reconstructed seed identities."""
    launch = state.node.field("m_5EC1EB38")
    if (not launch or launch.get("$") != "STU_239B571E" or state.spread_ramp is None
            or launch.get("m_6D992E59") is not None):
        return forward
    lower = expr.f32(expr.to_float(state.evaluate(launch.get("m_7F0FED02"), watch=False)))
    upper = expr.f32(expr.to_float(state.evaluate(launch.get("m_429A6C85"), watch=False)))
    # AE71CF uses the pre-shot count. The output variable uses the post-shot count.
    fraction = state.spread_for_count(shot)
    degrees = expr.f32(lower + expr.f32(expr.f32(upper - lower) * fraction))
    radius = expr.f32(degrees * expr.f32(math.pi / 180))
    if radius <= 0.0 or not math.isfinite(radius):
        return forward
    seed = weapon_spread.seed_base(owner_entity, state.instance.id, state.node.pos, counter)
    return weapon_spread.single_cone_direction(forward, radius, seed, shot)


def grenade_direction(state, forward):
    """1932 node 58 adds the launch value along world Y before normalizing."""
    launch = state.node.field("m_5EC1EB38") or {}
    offset = expr.f32(expr.to_float(state.evaluate(launch.get("m_22CE7F30"), watch=False)))
    if not offset or not math.isfinite(offset):
        return forward
    x, y, z = forward
    y = expr.f32(expr.f32(y) + offset)
    length = math.hypot(x, y, z)
    if length < 1.0842021724855044e-18:  # D5ECC4 normalizer's float32 length-squared threshold
        return (0.0, -1.0, 0.0)
    return (x / length, y / length, z / length)


def projectile_gravity(state):
    """Project the E1A5B579 coefficient with the owner scale read at launch."""
    motions = state.node.field("m_projectileMotions") or ()
    if len(motions) != 1 or motions[0].get("$") != "STU_E1A5B579":
        return None
    gravity = state.evaluate(motions[0].get("m_599AB8D7"), watch=False)
    if type(gravity) not in (int, float) or not math.isfinite(gravity):
        return None
    slot = state.instance.component.entity_var(9573, create=False)
    scale = slot.value() if slot is not None else None
    while isinstance(scale, tuple) and scale:
        scale = scale[0]
    scale = expr.f32(expr.to_float(scale)) if type(scale) in (bool, int, float) else 1.0
    value = expr.f32(expr.f32(gravity) * scale)
    return value if math.isfinite(value) else None


def _charge_blocked(component):
    """Known0B8B flag vetoes; None2426 does not prove native eligibility.

    The explicit host still supplies its own eligibility for missing2426 and
    native ownership. It must never override a known false2426 or true842.
    """
    enabled = component.entity_var(2426).value()
    return ((enabled is not None and not expr.truthy(enabled))
            or expr.truthy(component.entity_var(842).value()))


def passive_charge(script):
    """0B8B's values/cap with an explicit input-frame host clock.

    Native ownership/2426 eligibility and scheduler phase remain unaccepted.
    Never run a second passive clock alongside the actual authored Wait.
    """
    component = script.component
    instance = next((item for item in component.instances.values()
                     if item.graph.index == 0x0B8B), None)
    if instance is None or _charge_blocked(component):
        script.passive_charge_ms = 0
        return
    timer = instance.graph.node(3)
    state = instance.states.get(timer.state)
    if state is not None and state.active:
        script.passive_charge_ms = 0
        return
    charge = expr.to_float(component.entity_var(342).value())
    cost = expr.to_float(component.entity_var(338).value())
    if not 0 <= charge < cost:
        script.passive_charge_ms = 0
        return
    interval = round(expr.to_float(instance.evaluate(timer.field("m_timeout"))) * 1000)
    if interval <= 0:
        raise ValueError("invalid authored passive charge interval")
    script.passive_charge_ms += script.frame_ms
    while script.passive_charge_ms >= interval:
        script.passive_charge_ms -= interval
        value = expr.to_float(instance.evaluate(instance.graph.node(4).field("m_value")))
        if math.isfinite(value) and value > charge:
            script.set_ult_charge(min(cost, value))
            charge = min(cost, value)


class _ChargeInput(runtime.InstanceContext):
    """Supply eligible HP to0B8B's authored expression without editing its vars."""

    def __init__(self, instance, identifier, amount):
        super().__init__(instance, None)
        self.identifier, self.amount = identifier, amount

    def var(self, scope, identifier):
        if (scope, identifier) == (0, self.identifier):
            return self.amount
        return super().var(scope, identifier)


@dataclass
class _Regeneration:
    source: object
    source_body: int
    recipient: object
    entity: int
    start: int
    duration: float
    total: float
    elapsed: float = 0.0


@dataclass(frozen=True)
class FieldSpec:
    source_body: int
    radius: float
    duration: float
    arming: float
    hover: float
    gravity: float
    sweep_radius: float = 0.0
    bounce_limit: int | None = None
    bounce_scale: float = 0.0


@dataclass
class _Field:
    actor: actors.Deployable
    spec: FieldSpec
    active_tick: int
    protections: dict = field(default_factory=dict)


class BaptisteEffects:
    def __init__(self, combat):
        self.combat = combat
        self.seen: dict[int, tuple[int, int]] = {}
        self.regeneration: list[_Regeneration] = []
        self.protection_seen: dict[int, dict[tuple[int, int], int]] = {}
        self.fields: dict[int, _Field] = {}
        self.field_bindings: dict[tuple[int, int], int] = {}
        self.matrices: dict[int, actors.Deployable] = {}

    def damage_charge(self, source, target, dealt, *, source_body=None):
        self._combat_charge(source, target, dealt, healing=False, source_body=source_body)

    def healing_charge(self, source, target, eligible, *, source_body=None):
        self._combat_charge(source, target, eligible, healing=True, source_body=source_body)

    def _combat_charge(self, source, target, amount, *, healing, source_body=None):
        """Actual eligible HP drives0B8B; event eligibility is reconstructed."""
        from ow174.game.script.nodes import Ability

        if getattr(getattr(source, "hero", None), "guid", None) != HERO:
            return
        ally = not self.combat.match.game_map.free_for_all and target.team == source.team
        if (source.gone or not source.has_body or self.combat.is_dead(source)
                or target.owner is source or isinstance(target.owner, actors.Deployable)
                or (source_body is not None and source.body != source_body)
                or (healing and not ally) or (not healing and ally)
                or not math.isfinite(amount) or amount <= 0):
            return
        script = source.body_script
        if (script is None or _charge_blocked(script.component)
                or any(state.flags & Ability.RUNNING for state in script.ultimates)):
            return
        instance = next((item for item in script.component.instances.values()
                         if item.graph.index == 0x0B8B), None)
        if instance is None:
            return
        before = expr.to_float(script.component.entity_var(342).value())
        cost = expr.to_float(script.component.entity_var(338).value())
        if not 0 <= before < cost:
            return
        position, identifier = (32, 499) if healing else (23, 336)
        value = expr.evaluate(instance.graph.node(position).field("m_value"),
                              _ChargeInput(instance, identifier, expr.f32(amount)))
        after = expr.to_float(value)
        if math.isfinite(after) and after > before:
            script.set_ult_charge(min(cost, after))

    def _matrix_owner(self, instance):
        if instance.graph.index != 0x1902 or instance.component.world.hero != HERO:
            return None
        return next((p for p in self.combat.match.players
                     if p.has_body and p.body == instance.component.entity and not p.gone), None)

    def matrix_native(self, instance, cfg):
        """Narrow static-world transform/orientation reconstruction for1902 only."""
        owner = self._matrix_owner(instance)
        if owner is None:
            return NotImplemented
        cls = cfg.get("$")
        if cls in {"STU_66F3B89E", "STU_021D01CD"}:
            if instance.evaluate(cfg.get("m_entity")) != expr.Entity(owner.body):
                return NotImplemented
            angle = owner.mover.yaw * math.tau / 65536
            if cls == "STU_021D01CD":
                angle += math.pi / 2
            return expr.Vec3(math.sin(angle), 0.0, math.cos(angle))
        if cls == "STU_C5148EFE" and instance.evaluate(cfg.get("m_entity")) == expr.Entity(0):
            # Entity0 denotes the static world in this adapter. No moving-parent
            # placement or arbitrary foreign transform is inferred.
            point = instance.evaluate(cfg.get("m_BD631144"))
            if isinstance(point, expr.Vec3):
                return point
        return NotImplemented

    def matrix_confirm(self, state):
        """Reconstructed direct aim ray, not the native swept placement solver."""
        owner = self._matrix_owner(state.instance)
        if owner is None or state.index != 9:
            return NotImplemented
        fight = self.combat
        if fight.is_dead(owner):
            return False
        from ow174.game.combat import aim

        limit = state.evaluate(state.node.field("m_6386E7B4"), watch=False)
        slope = state.evaluate(state.node.field("m_2ADABA5F"), watch=False)
        if (type(limit) not in (int, float) or type(slope) not in (int, float)
                or not math.isfinite(limit) or not math.isfinite(slope) or limit <= 0):
            return False
        collision = fight._collision()
        hit = (collision.cast_ray(fight._eye(owner), aim(owner.mover.yaw, owner.mover.pitch), limit)
               if collision is not None else None)
        valid = hit is not None and hit.normal[1] >= math.cos(slope)
        values = {"m_E21062B4": valid}
        if valid:
            values.update(m_30710B76=expr.Vec3(*hit.point), m_2339572B=expr.Vec3(*hit.normal),
                          m_1549F0F9=expr.Entity(0))
        for field_name, value in values.items():
            target = expr.lvalue(state.node.field(field_name))
            if target is not None:
                state.instance.write(target, value)
        return valid

    def matrix_spawn(self, instance, node):
        owner = self._matrix_owner(instance)
        if owner is None or node.pos != 54:
            return NotImplemented
        definition = instance.evaluate(node.field("m_entityDefinition"))
        point = instance.evaluate(node.field("m_position"))
        direction = instance.evaluate(node.field("m_AD8E614B"))
        duration = self._number(instance, 567)
        if (not isinstance(definition, expr.Asset) or definition.guid != 0x0400000000002B50
                or not isinstance(point, expr.Vec3) or not isinstance(direction, expr.Vec3)
                or not all(math.isfinite(v) for v in (*point, *direction))
                or duration is None or duration <= 0 or math.hypot(direction.x, direction.z) == 0):
            return NotImplemented
        angle = math.atan2(direction.x, direction.z)
        actor = self.combat.create_deployable(
            owner, definition.guid, "Amplification Matrix", tuple(point), health=0, radius=0,
            height=0, seconds=duration, rotation=(0.0, math.sin(angle / 2), 0.0, math.cos(angle / 2)),
        )
        self.matrices[actor.entity] = actor
        return expr.Entity(actor.entity)

    def matrix_multiplier(self, source, start, end, *, team=None, time_span=None):
        """Reconstructed crossing of92E3's authenticated local mesh rectangle.

        Native tag/modifier consumption and multiple-window stacking are open.
        This adapter latches one additive+1 modifier per weapon flight, regardless
        of later expiry. It does not turn Burst/melee/field effects into shots.
        """
        from ow174.game.props import _rotate32

        affiliation = source.team if team is None else team
        for actor in self.matrices.values():
            if (actor.entity not in self.combat.deployables
                    or actor.owner.gone or actor.owner.hero.guid != HERO):
                continue
            if self.combat.match.game_map.free_for_all:
                if actor.owner is not source:
                    continue
            elif actor.team != affiliation:
                continue
            inverse = (*(-v for v in actor.rotation[:3]), actor.rotation[3])
            local = [_rotate32(inverse, tuple(v - p for v, p in zip(point, actor.position, strict=True)))
                     for point in (start, end)]
            a, b = local
            span = b[2] - a[2]
            if abs(span) <= 1e-9:
                continue
            share = -a[2] / span
            if not 0 < share <= 1:
                continue
            at = (self.combat.tick if time_span is None else
                  time_span[0] + share * (time_span[1] - time_span[0]))
            if not actor.created_tick <= at < actor.expires_tick:
                continue
            x, y = (a[i] + (b[i] - a[i]) * share for i in (0, 1))
            if abs(x) > 4.4999995 or not -1.1999999 <= y <= 3.7999997:
                continue
            #1909 nodes9/12:0319/0317 amount1, interpreted additively.
            modifier = graph.graph(0x1909)
            if modifier is None:
                return 1.0
            amount = expr.evaluate(modifier.node(12).field("m_50530EAB"), expr.Context())
            return 1.0 + amount if type(amount) in (int, float) and amount >= 0 else 1.0
        return 1.0

    def retire_body(self, body):
        self.seen.pop(body, None)
        self.protection_seen.pop(body, None)
        self.regeneration[:] = [
            item for item in self.regeneration if item.entity != body
        ]
        for ward in list(self.fields.values()):
            self._remove_protection(ward, body)
            if ward.actor.owner.gone:
                self.combat.destroy_deployable(ward.actor.entity)

    @staticmethod
    def _number(instance, identifier):
        var = instance.vars.get(identifier)
        if var is None:
            return None
        value = var.value()
        return float(value) if type(value) in (int, float) and math.isfinite(value) else None

    def command(self, player, command):
        if player.hero.guid != HERO or command is None or player.body_script is None:
            return
        for instance in player.body_script.component.instances.values():
            if instance.graph.index != BURST_GRAPH:
                continue
            tracker = instance.states.get(TRACK_STATE)
            # TrackTargets transitions and retires inside the same script frame;
            # its activation counter remains the event, even when already off.
            if tracker is None or not tracker.counter:
                continue
            activation = (instance.id, tracker.counter)
            if self.seen.get(player.body) == activation:
                continue
            self.seen[player.body] = activation
            radius = self._number(instance, RADIUS)
            duration = self._number(instance, DURATION)
            amount = self._number(instance, AMOUNT)
            if radius is None or duration is None or amount is None or min(radius, duration, amount) <= 0:
                continue
            self._burst(player, command.frame, radius, duration, amount)

    def grenade_parameters(self, state):
        """1932/state26 plus recovered1C26: owner radius/area heal and downward motion.

        The 1C26 source graph is kept as external research, not silently mounted
        as a working script. This adapter reconstructs only its declared heal.
        """
        component = state.instance.component
        if (component.world.hero != HERO or state.instance.graph.index != 0x1932
                or state.index != 26):
            return None
        radius = self._number(component, GRENADE_RADIUS)
        amount = self._number(component, GRENADE_HEAL)
        gravity = projectile_gravity(state)
        if radius is None or amount is None or gravity is None or min(radius, amount) <= 0:
            return None
        return radius, amount, gravity

    def field_parameters(self, state):
        component = state.instance.component
        if component.world.hero != HERO or state.instance.graph.index != 0x1761 or state.index != 12:
            return None
        metadata = graph.graph(0x1762)
        if metadata is None:
            return None
        radius = None
        for row in metadata.node(29).field("m_143D4C5B") or []:
            target = row.get("m_out_Var") or {}
            if target.get("m_identifier") == 1165:
                radius = expr.evaluate(row.get("m_value"), expr.Context())
        duration = expr.evaluate(metadata.node(16).field("m_timeout"), expr.Context())
        arming = expr.evaluate(metadata.node(28).field("m_timeout"), expr.Context())
        hover = expr.evaluate(metadata.node(50).field("m_value"), expr.Context())
        gravity = projectile_gravity(state)
        values = (radius, duration, arming, hover)
        if gravity is None or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                                  for value in values):
            return None
        direct = state.node.field("m_6396149F") or {}
        sweep_radius = state.evaluate(direct.get("m_1FFD8312"), watch=False)
        if type(sweep_radius) not in (int, float) or not math.isfinite(sweep_radius) or sweep_radius < 0:
            sweep_radius = 0.0
        bounce = state.node.field("m_E365DB20") or {}
        limit = state.evaluate(bounce.get("m_360294F0"), watch=False)
        scale = state.evaluate(bounce.get("m_0D20DAFD"), watch=False)
        enabled = state.evaluate(bounce.get("m_D9EFD182"), watch=False)
        bounce_limit = limit if enabled is True and type(limit) is int and limit >= 0 else None
        bounce_scale = float(scale) if type(scale) in (int, float) and math.isfinite(scale) else 0.0
        return FieldSpec(component.entity, *map(float, values), gravity, float(sweep_radius),
                         bounce_limit, bounce_scale)

    def projectile_impact(self, rocket, point):
        spec, source = rocket.deployment, rocket.shooter
        if not isinstance(spec, FieldSpec) or source.body != spec.source_body or source.gone:
            return
        for ward in list(self.fields.values()):
            if ward.actor.owner is source:
                self.combat.destroy_deployable(ward.actor.entity)
        # Manifest-verified9EBA MSTU capsule on model node2 (identity bind pose).
        # This replaces the unrelated29A9 movement proxy. Animated pose/native
        # filter equivalence and the rising curve remain unverified.
        position = (point[0], point[1] + spec.hover, point[2])
        actor = self.combat.create_deployable(
            source, 0x04000000000029A9, "Immortality Field", position,
            health=150.0, radius=0.46099997, height=1.01799993, seconds=spec.duration,
            capsule=((-1.04519895e-17, 0.18003005, 0.0), (1.7166137e-8, 0.08403006, 0.0)),
        )
        self.fields[actor.entity] = _Field(actor, spec, actor.created_tick + math.ceil(spec.arming / TICK))

    def _eligible(self, ward, target):
        fight, actor = self.combat, ward.actor
        if (self.combat.tick < ward.active_tick or self.combat.tick >= actor.expires_tick
                or actor.owner.gone or actor.owner.hero.guid != HERO
                or isinstance(target.owner, actors.Deployable)
                or fight.vitals_of(target).dead):
            return False
        if actor.entity in fight.sombra_controls.locks:
            return False
        if fight.match.game_map.free_for_all:
            if target.owner is not actor.owner:
                return False
        elif target.team != actor.team:
            return False
        if math.dist(actor.position, target.feet) > ward.spec.radius:
            return False
        centre = (target.feet[0], target.feet[1] + target.height * 0.5, target.feet[2])
        return fight.area_visible(actor.position, centre)

    def protection_valid(self, target, instance_id):
        actor_id = self.field_bindings.get((target.entity, instance_id))
        if actor_id is None:
            return True  # Explicit mounted-script fixtures have no inferred zone.
        ward = self.fields.get(actor_id)
        return ward is not None and self._eligible(ward, target)

    def _remove_protection(self, ward, entity):
        binding = ward.protections.pop(entity, None)
        if binding is not None:
            component, instance = binding
            self.field_bindings.pop((entity, instance.id), None)
            if component.instances.get(instance.id) is instance:
                component.destroy(instance)

    def deployable_removed(self, entity):
        self.matrices.pop(entity, None)
        ward = self.fields.pop(entity, None)
        if ward is not None:
            for target in list(ward.protections):
                self._remove_protection(ward, target)

    def _update_fields(self, targets, frame):
        fight = self.combat
        for ward in list(self.fields.values()):
            actor = ward.actor
            if frame >= actor.expires_tick or actor.owner.gone or actor.owner.hero.guid != HERO:
                fight.destroy_deployable(actor.entity)
                continue
            eligible = {entity: target for entity, target in targets.items() if self._eligible(ward, target)}
            for entity in list(ward.protections):
                if entity not in eligible:
                    self._remove_protection(ward, entity)
            for entity, target in eligible.items():
                script = getattr(target.owner, "body_script", None)
                if script is None:
                    continue
                component = script.component
                previous = ward.protections.get(entity)
                if previous is not None:
                    if previous[0] is component:
                        continue
                    self._remove_protection(ward, entity)
                if component.frame < frame:
                    component.run_frame(frame)
                instance = component.play_loaded_graph(
                    graph.graph(0x1763), source=actor.entity, instigator=actor.source_body, policy=1,
                )
                component.run_now()
                ward.protections[entity] = component, instance
                self.field_bindings[(entity, instance.id)] = actor.entity

    def _burst(self, source, frame, radius, duration, amount):
        fight = self.combat
        origin = fight._eye(source)
        for target in fight._targets():
            if isinstance(target.owner, actors.Deployable):
                continue
            own = target.owner is source
            if not own and (fight.match.game_map.free_for_all or target.team != source.team):
                continue
            if fight.vitals_of(target).dead or math.dist(source.mover.position, target.feet) > radius:
                continue
            # Selected solid-world and pillar LOS with feet-distance range.
            centre = (target.feet[0], target.feet[1] + target.height * 0.5, target.feet[2])
            if not own and not fight.area_visible(origin, centre):
                continue
            self.regeneration.append(_Regeneration(
                source, source.body, target.owner, target.entity, frame, duration, amount * (2 if own else 1),
            ))

    def refresh_field_protection(self, frame, targets=None):
        """Refresh membership and top-up after attacks at the selected world tick."""
        if targets is None:
            targets = {target.entity: target for target in self.combat._targets()}
        self._update_fields(targets, frame)
        for entity in list(self.protection_seen):
            if entity not in targets:
                del self.protection_seen[entity]
        self._protection_top_up(targets.values())

    def update(self, frame):
        fight = self.combat
        for actor in list(self.matrices.values()):
            if frame >= actor.expires_tick or actor.owner.gone or actor.owner.hero.guid != HERO:
                fight.destroy_deployable(actor.entity)
        targets = {target.entity: target for target in fight._targets()}
        self.refresh_field_protection(frame, targets)
        for item in list(self.regeneration):
            target = targets.get(item.entity)
            if (target is None or target.owner is not item.recipient
                    or (target.owner is not item.source
                        and (fight.match.game_map.free_for_all or target.team != item.source.team))):
                self.regeneration.remove(item)
                continue
            vitals = fight.vitals_of(target)
            if vitals.dead:
                self.regeneration.remove(item)
                continue
            elapsed = min(item.duration, max(0.0, (frame - item.start) * TICK))
            if elapsed <= item.elapsed:
                continue
            seconds = elapsed - item.elapsed
            amount = item.total * seconds / item.duration
            item.elapsed = elapsed
            fight._heal_target(item.source, target, amount, source_entity=item.source_body,
                               seconds=seconds, end_tick=item.start + elapsed / TICK)
            if elapsed >= item.duration:
                self.regeneration.remove(item)

    def _protection_top_up(self, targets):
        """Run1763's actual conditional positive-health State after protection entry.

        Source attribution and complete native ModifyHealth flags remain open;
        this adapter neither installs protection nor invents field membership.
        """
        fight = self.combat
        for target in targets:
            script = getattr(target.owner, "body_script", None)
            instances = getattr(getattr(script, "component", None), "instances", None)
            if not isinstance(instances, dict) or fight.vitals_of(target).dead:
                continue
            for instance in instances.values():
                if instance.graph.index != 0x1763:
                    continue
                state = instance.states.get(1)
                if state is None or not state.active:
                    continue
                if not self.protection_valid(target, instance.id):
                    continue
                if fight.healing.factor(target) <= 0:
                    continue
                key = (instance.id, state.index)
                seen = self.protection_seen.setdefault(target.entity, {})
                if seen.get(key) == state.counter:
                    continue
                seen[key] = state.counter
                modify = state.node.field("m_modifyHealth") or {}
                if modify.get("m_93CDFD2F") != 0:
                    continue
                amount = state.evaluate(modify.get("m_amount"), watch=False)
                if type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0:
                    continue
                vitals = fight.vitals_of(target)
                if vitals.heal(amount) > 0:
                    fight._sync_body_health(target.owner, vitals)
