"""Crossfade status and Amp It Up driven by the authored 0267 receiver."""

import math
from dataclasses import dataclass

from ow174.game import actors
from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000079
# Authored 0126.054 defaults for Hold to Crossfade, Backwards Wall Ride and Wall Jump on Release.
SETTING_DEFAULTS = {7766: False, 9473: False, 32856: True}
CROSSFADE = 0x0267
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


def stop_passive_timer(component):
    """The charge child is created by 01C7 after the body's initial graphs."""
    for instance in tuple(component.instances.values()):
        if instance.graph.index == 0x0B8B:
            gate = instance.states.get(0)
            if gate is not None and gate.active:
                gate.end(False)


def advance_passive_charge(player, tick, *, due_only=False):
    """Drive actual 0B8B's passive formula on the reconstructed world clock."""
    from ow174.game.combat import TICK
    from ow174.game.script.nodes import seconds_to_ms

    script = player.body_script
    if script is None or script.hero != HERO or script.retired or script.component.fault is not None:
        return
    if script.ult_seconds <= 0 or script.component.owner_dead or player.match.combat.is_dead(player):
        script.passive_ult_deadline = None
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
    if cls == "STU_F7262B36":
        if instance.evaluate(cfg.get("m_801B298F")) == expr.Entity(world.body):
            player = charge_player(world, instance.component)
            enabled = instance.component.entity_var(ULTIMATE_ENABLED, create=False)
            if (player is not None and player.body_script.ult_seconds > 0 and not instance.component.owner_dead
                    and (enabled is None or enabled.value() is not False)):
                return expr.Entity(player.entity)
            return expr.Entity(0)
        return NotImplemented
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


def charge_healing(shooter, target, given, tags):
    if given > 0.:
        params = {
            0x3F: expr.Entity(target.entity), 0x40: expr.f32(given),
            0x41: tuple(expr.Asset(tag) for tag in tags),
        }
        _charge_event(shooter, 0x0240000000000044, params)


@dataclass
class Lease:
    instance: runtime.Instance
    created_tick: int


def keep_active_amp_on_stun(script, state) -> bool:
    """Keep applied 0277 Amp on its authored duration and cooldown during stun."""
    if script.hero != HERO:
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


class Auras:
    def __init__(self, combat):
        self.combat = combat
        self.leases: dict[tuple[int, int], Lease] = {}
        self.healing: dict[tuple[int, int], list[tuple[int, tuple]]] = {}
        self.last_tick: int | None = None

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
        from ow174.game.combat import capsule_visible, segment_distance

        child = graph.graph(CROSSFADE)
        if child is None:
            return []
        fight, wanted = self.combat, set()
        recipients = {}
        targets = fight._targets()
        sample_tick = fight.tick if command_frame is None else max(fight.tick + 1, command_frame)
        for source in targets:
            owner = source.owner
            hero = getattr(owner, "hero", None)
            if (getattr(hero, "guid", None) != HERO or not hasattr(owner, "mover")
                    or fight.vitals_of(source).dead):
                continue
            script = owner.body_script
            if script is None or script.retired or script.component.strict:
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
                if (isinstance(target.owner, actors.Deployable) or (target.owner is not owner and not ally)
                        or fight.vitals_of(target).dead):
                    continue
                target_script = getattr(target.owner, "body_script", None)
                if target_script is None:
                    continue
                a, b = target.segment()
                if segment_distance(origin, a, b) - target.radius > radius:
                    continue
                if target.owner is not owner and not capsule_visible(
                    fight._collision(), origin, a, b, target.radius, walls=fight.walls,
                ):
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
                    lease = self.leases[key] = Lease(instance, fight.tick)
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
                        # The source owns the song and Amp slots. Recheck them on
                        # the recipient without installing cross-component watchers.
                        for position in (1, 3, 4):
                            state = instance.states.get(instance.graph.node(position).state)
                            if state is not None and state.active:
                                state.dependency_changed()
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
                tag.guid for row in modify.get("m_148F3152") or ()
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
            elapsed = (end - start) * TICK
            for amount, tags in effects:
                given = self.combat._heal_target(
                    source.owner, target, amount * elapsed, source_entity=source_id,
                    tags=tags, seconds=elapsed, end_tick=end,
                )
                charge_healing(source.owner, target, given, tags)
