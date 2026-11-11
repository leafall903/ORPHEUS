"""Lucio's reconstructed aura host using the authored 00A4.053/0267 graph.

Target acquisition and world-time integration are server reconstructions.
The status graph selects heal/speed, self/ally rates and Amp branches. This
does not claim native status allocation/replication or complete speed support.
"""

import math
from dataclasses import dataclass

from ow174.game import stats
from ow174.game.mover import IN_AIR
from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000079
# Target build 0126.054 hero settings. These are the server spawn defaults.
SETTING_DEFAULTS = {
    7766: False,  # Hold to Crossfade
    9473: False,  # Allow Backwards Wall Ride
    32856: True,  # Wall Jump on Release
}
CROSSFADE = 0x04A00000000000A4
SOUND_BARRIER = 0x04A0000000000191
BARRIER_KEY = "lucio_sound_barrier"
ULTIMATE_ENABLED = 0x253A
ULTIMATE_GENERATION = 0x253C
PASSIVE_GENERATION = 0x3276
COMBAT_GENERATION = 0x32DE
SPAWN_ULTIMATE_READY = 0x32E0
CHARGE_ACTIVE = 0x0240000000000694


def charge_player(world, component):
    """Resolve the active owner through the host's current player binding."""
    match = world.match
    if match is None:
        return None
    return next((player for player in match.players
                 if player.body == component.entity and match._shown(player)
                 and player.body_script is not None
                 and player.body_script.component is component), None)


def replacement_charge_ratio(script, hero, new_body):
    """Capture effective charge only across a current same-hero body replacement."""
    if (script is None or hero != HERO or script.hero != hero or script.entity == new_body
            or script.retired or script.component.fault is not None):
        return None
    cost = expr.to_float(script.component.entity_var(338).value())
    charge = expr.to_float(script.component.entity_var(342).value())
    if cost > 0. and math.isfinite(cost) and math.isfinite(charge):
        return max(0., min(1., charge / cost))
    return None


def restore_charge(script, ratio, controller):
    """Run actual 0B8B's ratio restore entry, including its spawn rules."""
    if ratio is None:
        return
    component = script.component
    instance = next((i for i in component.instances.values() if i.graph.index == 0x0B8B), None)
    if instance is not None:
        runtime.send_instance_message(instance, 0x02400000000014F2, {0x951: expr.f32(ratio)}, controller)
        component.run_now()


def advance_passive_charge(player, tick, *, due_only=False):
    """Drive actual 0B8B's passive formula on the reconstructed world clock."""
    from ow174.game.combat import TICK
    from ow174.game.script.nodes import seconds_to_ms

    script = player.body_script
    if script is None or script.hero != HERO:
        return
    tick_ms = round(TICK * 1000)
    now = tick * tick_ms
    deadline = getattr(script, "passive_ult_deadline", None)
    if due_only and (deadline is None or deadline > now):
        return
    component = script.component
    instance = next((i for i in component.instances.values() if i.graph.index == 0x0B8B), None)
    if instance is None:
        return
    gate = instance.graph.node(1)
    state = instance.node_state(gate)
    if state.active:
        # Snapshots predating the world clock may contain a live command timer.
        state.end(False)
    common = next((i for i in component.instances.values() if i.graph.index == 0x01C7), None)
    if common is not None and not common.node_state(common.graph.node(77)).active:
        runtime.send_instance_message(common, CHARGE_ACTIVE, {}, player.entity)
        component.run_now()
    blocked = (not expr.truthy(instance.evaluate(gate.field("m_condition")))
               or any(expr.truthy(instance.evaluate(instance.graph.node(pos).field("m_condition")))
                      for pos in (63, 64)))
    if blocked:
        script.passive_ult_deadline = None
        return

    wait = instance.graph.node(3)
    interval = seconds_to_ms(instance.evaluate(wait.field("m_timeout")))
    if interval <= 0:
        return
    if deadline is None:
        script.passive_ult_deadline = now + interval
        return
    value = instance.graph.node(4).field("m_value")
    charge = component.entity_var(342)
    cost = expr.to_float(component.entity_var(338).value())
    while deadline <= now:
        before = charge.value()
        after = expr.to_float(instance.evaluate(value))
        if after <= expr.to_float(before):
            # Zero generation still consumes the elapsed intervals.
            deadline += (1 + (now - deadline) // interval) * interval
            break
        script.set_ult_charge(after)
        deadline += interval
        if after >= cost:
            # Charge accrued after the last owner frame. Replayed frames
            # cannot use it to resurrect a released, expired Q press.
            elapsed = max(0, tick - getattr(script, "passive_ult_last_command_tick", tick)) * tick_ms
            for ability in script.ultimates:
                window = seconds_to_ms(ability.evaluate(ability.node.field("m_246438AD"), watch=False))
                cutoff = script.frame * script.frame_ms + elapsed - window
                ability.presses[:] = [press for press in ability.presses if press[0] >= cutoff]
            deadline = None
            break
    script.passive_ult_deadline = deadline


def damage_charge_query(world, cfg, instance):
    """Host inputs for the ordinary combat-body branch of actual 0B8B."""
    if instance is None or instance.graph.index != 0x0B8B or world.match is None:
        return NotImplemented
    cls = cfg.get("$")
    if cls == "STU_A6B80C63":
        details = cfg.get("m_814F8EC3")
        if expr.dynamic(details) == (expr.INSTANCE, 16100) and instance.evaluate(details) == ():
            return instance.evaluate(cfg.get("m_418D9D9D"))
        return NotImplemented
    fight = world.match.combat
    if cls == "STU_A58C1E63":
        source = instance.evaluate(cfg.get("m_source"))
        target = instance.evaluate(cfg.get("m_target"))
        if source != expr.Entity(world.body) or not isinstance(target, expr.Entity):
            return False
        player = charge_player(world, instance.component)
        return player is not None and any(
            row.entity == target.id and row.owner is not player
            and (world.match.game_map.free_for_all or row.team != player.team)
            for row in fight._targets()
        )
    if cls in ("STU_AC829876", "STU_25ECB55F"):
        target = instance.evaluate(cfg.get("m_entity"))
        if not isinstance(target, expr.Entity):
            return False
        row = next((row for row in fight._targets() if row.entity == target.id), None)
        if cls == "STU_AC829876":
            selected = {k: v for k, v in (cfg.get("m_filter") or {}).items() if v not in (None, 0)}
            if selected == {"$": "STUConfigVarFilter", "m_84065FD1": 1}:
                return row is not None
        elif cfg.get("m_88B0050A") == "0x0D6000000000011E":
            # 104319 Roadhog's body definition owns this charge-reduction tag.
            hero = getattr(row.owner, "hero", None) if row is not None else None
            return getattr(hero, "guid", None) == 0x02E0000000000040
    return NotImplemented


def _charge_event(source, message, params):
    """Deliver a reconstructed combat event to the original Lucio charge graph."""
    script = getattr(source, "body_script", None)
    if script is None or script.hero != HERO:
        return
    component = script.component
    enabled = component.entity_var(ULTIMATE_ENABLED, create=False)
    if component.owner_dead or (enabled is not None and enabled.value() is False):
        return
    instance = next((i for i in component.instances.values() if i.graph.index == 0x0B8B), None)
    if instance is not None:
        runtime.send_instance_message(instance, message, params, script.entity)
        component.run_now()


def charge_damage(shooter, target, dealt, loadout):
    """Report actual base-pool loss to the original damage-credit receiver."""
    if dealt > 0.:
        params = {0x13: dealt, 0x15: expr.Entity(target.entity), 0x3D: expr.Asset(loadout or 0), 0x624: ()}
        _charge_event(shooter, 0x0240000000000012, params)


def barrier_owner_filter(world, cfg, instance):
    """Narrow reconstructed slam gate; not a generic native filter decoder."""
    if instance is None or instance.graph.index != 0x073A or world.match is None:
        return NotImplemented
    target = cfg.get("m_entity") or {}
    nonzero = {key: value for key, value in (cfg.get("m_filter") or {}).items() if value not in (None, 0)}
    if (target.get("$") != "STU_B5A0CAF0"
            or nonzero != {"$": "STUConfigVarFilter", "m_84065FD1": 0x3FFFFFFF, "m_4EA686DB": 16}):
        return NotImplemented
    if graph.status_definition(SOUND_BARRIER) is NotImplemented:
        return False
    owner = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    # The mover's air age starts at zero on the first airborne pose.
    return (owner is not None and not (owner.mover.flags_word & IN_AIR)
            and not world.match.combat.is_dead(owner))


@dataclass
class Lease:
    source: object
    target: object
    instance: object
    created_tick: int


def keep_active_amp_on_stun(owner, state) -> bool:
    """Reconstructed buff policy: preserve an already applied actual0277 Amp.

    This does not grant stun immunity or allow a new cast. The original graph's
    timer still owns duration/cooldown; only the blanket Ability.stop is skipped.
    Native control categorization/cleanse remains unverified.
    """
    if getattr(getattr(owner, "hero", None), "guid", None) != HERO:
        return False
    if state.instance.graph.index != 0x0277 or state.index != 0:
        return False
    marker = state.instance.component.entity_var(2381, create=False)
    return marker is not None and any(
        link.state.instance is state.instance and link.state.index == 3 and link.state.active
        for link in marker.links
    )


def effect_gap_frame(script, after, end):
    """Keep aura transitions and the Barrier cast timer without adding movement poses."""
    if script.hero != HERO or script.component.owner_dead:
        return end
    timer_states = {0x0266: (2, 41), 0x0277: (2, 4), 0x073A: (4,)}
    return min(
        (frame for instance in script.component.instances.values()
         for event in instance.queue
         if event.kind == runtime.TIMER and event.state is not None and event.state.active
         and event.state.index in timer_states.get(instance.graph.index, ())
         if after < (frame := event.time // script.frame_ms) < end),
        default=end,
    )


@dataclass
class BarrierLease:
    source: int
    target: object
    instance: object
    capacity: float
    duration: float
    last_tick: int


@dataclass(frozen=True)
class BarrierGrant:
    source: int
    origin: tuple[float, float, float]
    radius: float
    duration: float
    tick: int


class Auras:
    def __init__(self, combat):
        self.combat = combat
        self.leases: dict[tuple[int, int], Lease] = {}
        self.healing: dict[tuple[int, int], list[tuple[int, tuple]]] = {}
        self.last_tick: int | None = None

    def has_moving_npc_recipients(self) -> bool:
        match = self.combat.match
        if match.game_map.free_for_all:
            return False
        teams = {bot.team for bot in match.bots
                 if not bot.dead and bot.body_script is not None and (bot.route or bot.impulse_mover is not None)}
        return bool(teams and graph.status_definition(CROSSFADE) is not NotImplemented and any(
            player.team in teams and player.hero.guid == HERO and match._shown(player)
            and player.body_script is not None and not self.combat.is_dead(player)
            for player in match.players
        ))

    def retire(self, body):
        for key in list(self.leases):
            if body in key:
                self._drop(key)
        for key in list(self.healing):
            if body in key:
                del self.healing[key]

    def _drop(self, key):
        lease = self.leases.pop(key)
        lease.instance.component.destroy(lease.instance)

    def _play_controller(self, lease):
        """Bridge actual0267's self PlayScript to persistent, policy0 actual07C7.

        Legacy permissive actions otherwise skip PlayScript. Keep this bounded
        to the known self-targeted status action; arbitrary world routing and
        native inherited invocation/transport context remain unimplemented.
        """
        from ow174.game.script import play

        instance = lease.instance
        component = instance.component
        if any(item.graph.index == 0x07C7 for item in component.instances.values()):
            return  # authored policy0 skips an existing controller
        node = instance.graph.node(6)
        resource = play.resolve_resource(instance, node)
        if (resource is None or resource.guid != 0x05800000000007C7
                or instance.evaluate(node.field("m_targets")) != expr.Entity(component.entity)
                or node.field("m_AA4DE426") != 0 or node.field("m_7ABA409C") != 0
                or node.field("m_params") is not None or node.field("m_6D7701B7") is not None):
            raise ValueError("unsupported Crossfade controller invocation")
        definition = graph.graph(resource.guid & 0xFFFF)
        if definition is None:
            raise ValueError("Crossfade requires actual07C7 movement controller")
        controller = component.play_loaded_graph(
            definition, source=component.entity, overrides=resource.overrides, policy=0,
        )
        if controller is not None:
            controller.update()

    def refresh(self, command_frame=None):
        from ow174.game.combat import EPSILON, _dot, _sub, ray_world, segment_distance

        definition = graph.status_definition(CROSSFADE)
        if definition is NotImplemented:
            return []  # Legacy runtime has no status resources; do not invent them.
        child = graph.guid(definition.get("m_5A4051BC"))
        child = graph.graph(child & 0xFFFF) if child is not None else None
        if child is None or child.index != 0x0267:
            raise ValueError("Crossfade requires the actual 0267 status graph")
        fight, wanted = self.combat, set()
        recipients = {}
        targets = fight._targets()
        sample_tick = fight.tick if command_frame is None else max(fight.tick + 1, command_frame)
        for source in targets:
            owner = source.owner
            hero = getattr(owner, "hero", None)
            if getattr(hero, "guid", None) != HERO or fight.vitals_of(source).dead:
                continue
            script = owner.body_script
            if script is None or script.retired:
                continue
            searches = [instance.states.get(3) for instance in script.component.instances.values()
                        if instance.graph.index == 0x0266]
            search = next((state for state in searches if state is not None and state.active), None)
            if search is None:
                continue
            radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
            if radius <= 0:
                continue
            origin = fight._eye(owner)
            for target in targets:
                ally = not fight.match.game_map.free_for_all and source.team == target.team
                if (target.owner is not owner and not ally) or fight.vitals_of(target).dead:
                    continue
                target_script = getattr(target.owner, "body_script", None)
                if target_script is None:
                    continue
                a, b = target.segment()
                if segment_distance(origin, a, b) - target.radius > radius:
                    continue
                centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
                offset = _sub(centre, origin)
                length = _dot(offset, offset) ** 0.5
                if target.owner is not owner and length > EPSILON:
                    direction = tuple(value / length for value in offset)
                    wall = ray_world(fight._collision(), origin, direction, length)
                    if wall is not None and wall < length:
                        continue
                key = (source.entity, target.entity)
                wanted.add(key)
                lease = self.leases.get(key)
                component = target_script.component
                if lease is not None and lease.instance.component is not component:
                    self._drop(key)
                    lease = None
                if lease is None:
                    instance = component.create(
                        child, values={(graph.INSTANCE, 27): expr.Entity(source.entity)},
                    )
                    lease = self.leases[key] = Lease(owner, target, instance, fight.tick)
                recipients.setdefault(component, []).append((key, lease, script.component.now))
        for key in list(self.leases):
            if key not in wanted:
                self._drop(key)
                self._record_healing(key, sample_tick, ())

        for component, entries in recipients.items():
            self._update_leases(component, entries)
            for key, lease, _ in entries:
                self._play_controller(lease)
                self._record_healing(key, max(sample_tick, lease.created_tick + 1), self._heals(lease))
        return targets

    @staticmethod
    def _update_leases(component, entries):
        target_now = component.now
        # Sibling 0267 leases share the receiver's priority stack. Drain their
        # notifications together before sampling any rate. The receiver's
        # command clock and its separate 07C7 controller stay at target_now.
        source_now = max(now for _, _, now in entries)
        instances = [lease.instance for _, lease, _ in entries]
        component.now = max(target_now, source_now)
        try:
            with component.operation():
                pending = instances
                while pending:
                    for instance in pending:
                        instance.update()
                    pending = [
                        instance for instance in instances
                        if not instance.stopped and instance.queue and instance.queue[0].time <= component.now
                    ]
        finally:
            component.now = target_now

    @staticmethod
    def _heals(lease):
        from ow174.game.combat import hit_def

        effects = []
        for state in lease.instance.states.values():
            if not state.active or state.node.cls != "STUStatescriptStateModifyHealth":
                continue
            # Only the two authored 0267 healing operations are supported.
            modify = state.node.field("m_modifyHealth") or {}
            if state.node.field("m_7B2B3F5C") != 1 or modify.get("m_93CDFD2F") != 0:
                continue
            hit = hit_def(state, {"m_modifyHealth": modify})
            if hit is None or hit.amount <= 0:
                continue
            tags = tuple(
                tag for row in modify.get("m_148F3152") or ()
                if isinstance(tag := state.evaluate(row.get("m_EE3055BD"), watch=False), expr.Asset)
                and tag.guid
            )
            effects.append((hit.amount, tags))
        return tuple(effects)

    def _record_healing(self, key, tick, effects):
        timeline = self.healing.get(key)
        if timeline is None:
            if effects:
                self.healing[key] = [(tick, effects)]
            return
        last_tick, last_effects = timeline[-1]
        if effects == last_effects:
            return
        # The body VMs are forward-only. A later observation cannot undo a
        # rate already observed at a newer command frame.
        if tick <= last_tick:
            timeline[-1] = last_tick, effects
        else:
            timeline.append((tick, effects))

    def update(self, tick, *, observe=True):
        if self.last_tick is not None and tick <= self.last_tick:
            return
        previous = tick - 1 if self.last_tick is None else self.last_tick
        observed = self.refresh() if observe else self.combat._targets()
        targets = {target.entity: target for target in observed}
        self.last_tick = tick

        changes = {}
        active = {}
        for key, timeline in list(self.healing.items()):
            consumed = 0
            for at, effects in timeline:
                if at > tick:
                    break
                consumed += 1
                if at <= previous:
                    active[key] = effects
                else:
                    changes.setdefault(at, {})[key] = effects
            if consumed:
                # Retain the last nonempty sample as the next interval baseline.
                last = timeline[consumed - 1]
                del timeline[:consumed - bool(last[1])]
                if not timeline:
                    del self.healing[key]

        start = previous + 1
        for at, rates in sorted(changes.items()):
            self._settle_healing(targets, active, start, at)
            active.update(rates)
            start = at
        self._settle_healing(targets, active, start, tick + 1)

    def _settle_healing(self, targets, active, start, end):
        from ow174.game.combat import TICK

        if start >= end:
            return
        for (source_id, target_id), effects in active.items():
            source = targets.get(source_id)
            target = targets.get(target_id)
            if not effects or source is None or target is None:
                continue
            vitals = self.combat.vitals_of(target)
            if vitals.dead or self.combat.vitals_of(source).dead:
                continue
            elapsed = self.combat.healing_ticks(target_id, start - 1, end - 1) * TICK
            for amount, tags in effects:
                given = vitals.heal(amount * elapsed)
                if given > 0:
                    source.owner.stats.add(stats.HEALING, given)
                    self.combat._sync_body_health(target.owner, vitals)
                    params = {0x3F: expr.Entity(target_id), 0x40: expr.f32(given), 0x41: tags}
                    _charge_event(source.owner, 0x0240000000000044, params)


class Barriers:
    """One-shot team shielding from authored slam and073B pool initialization.

    Reconstruct a linear capacity/duration decay and immediate radial delivery.
    Native propagation, time-scaling295F1CDE and health-pool flags need further
    comparison. Applied shields persist if the caster subsequently dies.
    """

    def __init__(self, combat):
        self.combat = combat
        self.seen = {}
        self.pending: list[BarrierGrant] = []
        self.leases: dict[int, BarrierLease] = {}

    def retire(self, entity):
        self.pending[:] = [grant for grant in self.pending if grant.source != entity]
        latest = getattr(self, "latest_grants", None)
        if latest is not None:
            latest.pop(entity, None)
            if not latest:
                del self.latest_grants
        for key in list(self.seen):
            if key[0] == entity:
                del self.seen[key]
        if entity in self.leases:
            self._drop(entity)

    def _drop(self, entity):
        lease = self.leases.pop(entity)
        vitals = self.combat.vitals.get(entity)
        if vitals is not None:
            vitals.remove_temporary_shields(BARRIER_KEY)
            self.combat._sync_body_health(lease.target.owner, vitals)
        lease.instance.component.destroy(lease.instance)

    def command(self, player):
        if player.hero.guid != HERO:
            return
        script = player.body_script
        if script is None or script.retired or self.combat.is_dead(player):
            return
        for instance in list(script.component.instances.values()):
            if instance.graph.index != 0x073A:
                continue
            # The slam starts the 830 ms recovery wait on every cast. State 0
            # can stay active across casts, so its counter is not a slam serial
            slam, search = instance.states.get(6), instance.states.get(8)
            if slam is None or search is None:
                continue
            key = (player.body, instance.id)
            previous = self.seen.get(key, 0)
            self.seen[key] = slam.counter
            if previous == slam.counter or not slam.active:
                continue
            duration = expr.to_float(instance.context().var(graph.ENTITY, 1212))
            radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
            if duration <= 0 or radius <= 0:
                continue
            tick = slam.start // script.frame_ms
            grant = BarrierGrant(player.body, self.combat._eye(player), radius, duration, tick)
            if tick > self.combat.tick:
                self.pending.append(grant)
            else:
                self._grant(grant, self.combat.tick)

    def awaiting_slam(self, attacker, frame):
        """Check whether an enemy's current 073A can slam on an unreceived pose."""
        fight = self.combat
        for player in fight.match.players:
            if (player is attacker or player.hero.guid != HERO or not fight.match._shown(player)
                    or (not fight.match.game_map.free_for_all and player.team == attacker.team)):
                continue
            script = player.body_script
            if (script is None or script.retired or script.frame >= frame
                    or fight.is_dead(player) or fight.is_stunned(player, tick=frame)):
                continue
            for instance in script.component.instances.values():
                if instance.graph.index != 0x073A or instance.stopped:
                    continue
                startup = instance.states.get(3)
                if startup is None or not startup.active:
                    continue
                windup = instance.states.get(4)
                if windup is None or not windup.active:
                    return True  # the authored startup is waiting for a grounded pose
                if any(event.kind == runtime.TIMER and event.state is windup
                       and event.time // script.frame_ms == frame for event in instance.queue):
                    return True
        return False

    def due(self, tick):
        return any(grant.tick <= tick for grant in self.pending)

    def publish(self, tick):
        due = sorted((grant for grant in self.pending if grant.tick <= tick), key=lambda grant: grant.tick)
        if due:
            self.pending[:] = [grant for grant in self.pending if grant.tick > tick]
        for grant in due:
            # World projectiles and NPC weapons resolve before this frame's decay.
            self._grant(grant, tick - 1)

    def _grant(self, grant, aged_through):
        from ow174.game.combat import EPSILON, TICK, _dot, _sub, ray_world, segment_distance

        last_tick = max(grant.tick, aged_through)
        elapsed = (last_tick - grant.tick) * TICK
        if elapsed >= grant.duration:
            return

        targets = {target.entity: target for target in self.combat._targets()}
        source = targets.get(grant.source)
        if (source is None or self.combat.vitals_of(source).dead
                or self.combat.is_stunned(source.owner)):
            return
        definition = graph.status_definition(SOUND_BARRIER)
        child_guid = graph.guid(definition.get("m_5A4051BC")) if isinstance(definition, dict) else None
        child = graph.graph(child_guid & 0xFFFF) if child_guid is not None else None
        if child is None or child.index != 0x073B:
            raise ValueError("Sound Barrier requires actual073B status resources")
        free_for_all = self.combat.match.game_map.free_for_all
        latest = getattr(self, "latest_grants", {})
        for entity, target in targets.items():
            if entity != grant.source and (free_for_all or target.team != source.team):
                continue
            if (latest.get(entity, grant.tick) > grant.tick
                    or self.combat.replacement_ticks.get(entity, grant.tick) > grant.tick):
                continue
            if self.combat.vitals_of(target).dead:
                continue
            receiver = getattr(target.owner, "body_script", None)
            if receiver is None:
                continue
            if entity != grant.source:
                a, b = target.segment()
                if segment_distance(grant.origin, a, b) - target.radius > grant.radius:
                    continue
                centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
                offset = _sub(centre, grant.origin)
                length = _dot(offset, offset) ** 0.5
                if length > EPSILON:
                    direction = tuple(value / length for value in offset)
                    wall = ray_world(self.combat._collision(), grant.origin, direction, length)
                    if wall is not None and wall < length:
                        continue
            if entity in self.leases:
                self._drop(entity)
            effect = receiver.component.create(
                child, values={(graph.INSTANCE, 27): expr.Entity(grant.source)},
            )
            runtime.send_instance_message(effect, 0x024000000000000F, {0x2B: grant.duration}, grant.source)
            effect.update()
            state = effect.states.get(2)
            capacity = (
                expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False)) if state else 0
            )
            decay = capacity * elapsed / grant.duration
            if state is None or not state.active or capacity - decay <= EPSILON:
                receiver.component.destroy(effect)
                continue
            vitals = self.combat.vitals_of(target)
            pool = vitals.grant_temporary_shields(BARRIER_KEY, capacity)
            if decay > 0:
                vitals.drain_temporary_shields(BARRIER_KEY, decay)
                effect.write((graph.INSTANCE, 199), pool.current)
                effect.update()
            self.leases[entity] = BarrierLease(
                grant.source, target, effect, capacity, grant.duration, last_tick,
            )
            latest[entity] = grant.tick
            self.latest_grants = latest
            self.combat._sync_body_health(target.owner, vitals)

    def update(self, tick):
        from ow174.game.combat import TICK

        live = {target.entity for target in self.combat._targets()}
        for entity, lease in list(self.leases.items()):
            vitals = self.combat.vitals.get(entity)
            if entity not in live or vitals is None or vitals.dead:
                self._drop(entity)
                continue
            elapsed = max(0, tick - lease.last_tick) * TICK
            lease.last_tick = max(tick, lease.last_tick)
            vitals.drain_temporary_shields(BARRIER_KEY, lease.capacity * elapsed / lease.duration)
            pool = vitals.temporary_shields.get(BARRIER_KEY)
            if pool is None:
                self._drop(entity)
                continue
            lease.instance.write((graph.INSTANCE, 199), pool.current)
            lease.instance.update()
            self.combat._sync_body_health(lease.target.owner, vitals)
