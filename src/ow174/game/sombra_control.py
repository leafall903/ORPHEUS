"""Scoped host for the authored Sombra Hack and EMP body applications."""

import math
from copy import deepcopy
from dataclasses import dataclass, replace

from ow174.game import actors, bots, health_packs
from ow174.game.script import entity_queries, expr, graph, nodes, play, play_queue, runtime
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E000000000012E
HACK, EMP, PULSE, EFFECT = 0x0B1B, 0x0B31, 0x0B32, 0x0B1F
SKILLS = frozenset((35, 36, 37))
SKILL_INPUT = 0x38


@dataclass
class Lock:
    application: runtime.Instance
    until: float
    pulse: runtime.Instance | None
    clock: tuple[int, int] | None = None  # actual(world tick, recipient component ms) at delivery


class DeviceWorld(runtime.World):
    """The hosted receiver for a live deployable's actual 0B32 and 0B1F graphs."""

    def __init__(self, fight, body, tags, *, hero=0):
        super().__init__(hero, fight.match.game_map.mode_guid)
        self.fight = fight
        self.body = body
        self.tags = tags

    def component76_keys(self, entity):
        return entity_queries.Component76Keys(self.tags) if entity == self.body else NotImplemented

    def native(self, cfg, instance):
        if (instance is not None and instance.graph.index == EFFECT
                and cfg is instance.graph.node(19).field("m_condition")):
            from ow174.game.sombra_replay import _shown_sources

            return expr.to_entity(instance.evaluate(cfg.get("m_entity"))) in _shown_sources(self.fight.match)
        portals = getattr(self.fight, "portals", None)
        if (portals is not None and instance is not None and instance.graph.index == PULSE
                and cfg.get("$") == entity_queries.POSSESSED):
            for pos in (15, 16):
                if cfg is instance.graph.node(pos).field("m_targets")["m_configVars"][0]:
                    # The mounted pair supplies the body behind the authored owner/possession chain.
                    _, owner = portals.endpoint_owner(self.body)
                    return expr.Entity(owner.entity if owner is not None else 0)
        return super().native(cfg, instance)

    def entity_var_slot(self, entity, var, *, create=False):
        return self.fight.sombra_controls.endpoint_slot(self.body, entity, var)

    def play_script_selected(self, instance, node):
        return self.fight.sombra_controls.play(instance, node)

    def modify_health(self, state, elapsed_ms):
        if (state.instance.graph.index, state.node.pos) != (PULSE, 9):
            return NotImplemented
        if elapsed_ms == 0:
            self.fight.sombra_controls.drain_device(state)
            state.request_finish()
        return True


class Controls:
    def __init__(self, fight):
        self.fight = fight
        self.locks = {}
        self.held = {}
        self.device_receivers = {}
        self.lock_history = {}  # body -> (actual recipient component, component-ms windows)
        self.body_bases = {}  # original888 base on the actual recipient component

    def silence_body(self, component):
        entity = component.entity
        if entity not in self.body_bases:
            self.body_bases[entity] = component, component.entity_var(888).base
        component.set(888, True)

    def restore_body(self, entity):
        previous = self.body_bases.pop(entity, None)
        if previous is not None:
            component, base = previous
            component.set(888, base)

    def remember_lock(self, application, duration):
        """Retain the original skill-lock window on its actual recipient clock."""
        component = application.component
        existing = self.lock_history.get(component.entity)
        windows = [] if existing is None or existing[0] is not component else existing[1]
        # As in the original EMP host, only unconsumed older windows survive.
        windows[:] = [window for window in windows if component.frame*component.frame_ms < window[1]]
        start = application.start // component.frame_ms * component.frame_ms
        end = application.start + math.ceil(duration*1000)
        if windows and start <= windows[-1][1]:
            start, end = windows[-1][0], max(end, windows[-1][1])
            windows.pop()
        windows.append((start,end))
        self.lock_history[component.entity] = component, windows

    def observe_lock(self, application):
        """Retain the end of the real authored recipient wait, not a new timer."""
        wait = application.states.get(58) if application.graph.index == EFFECT else None
        row = self.lock_history.get(application.component.entity)
        if (not isinstance(wait, nodes.Wait) or row is None
                or row[0] is not application.component):
            return
        for index, (start, end) in enumerate(row[1]):
            if start <= application.start <= end:
                row[1][index] = start, max(end, wait.start + wait.ms)
                break

    def locked_at(self, script, frame):
        component = getattr(script, "component", None)
        row = self.lock_history.get(getattr(component, "entity", None))
        if row is None or row[0] is not component:
            return False
        now = frame * component.frame_ms
        return any(start <= now < end for start, end in row[1])

    def first_lock_after(self, script, frame, tick):
        """Original first-lock query; both bounds are recipient body frames.

        Application.start is recipient component milliseconds. World ticks are
        deliberately not guessed into that domain when an owner has another epoch.
        """
        component = getattr(script, 'component', None)
        row = self.lock_history.get(getattr(component, 'entity', None))
        if row is None or row[0] is not component:
            return None
        for start, _ in row[1]:
            impact_frame = start // script.frame_ms
            if frame < impact_frame <= tick:
                return impact_frame
        return None

    def input(self, player, command):
        """Latch held skills until a received command releases them."""
        entity = player.body
        masked = self.held.get(entity, 0) & command.buttons
        if entity in self.locks or self.locked_at(player.body_script, command.frame):
            masked |= command.buttons & SKILL_INPUT
        if masked:
            self.held[entity] = masked
            return replace(command, buttons=command.buttons & ~masked)
        self.held.pop(entity, None)
        return command

    def player(self, body):
        return next((p for p in self.fight.match.players if p.has_body and not p.gone and p.body == body), None)

    def component_locked_at(self, component, frame):
        row = self.lock_history.get(component.entity)
        return (row is not None and row[0] is component
                and any(start <= frame * component.frame_ms < end for start, end in row[1]))

    def blocked(self, state):
        """Original silence policy: retain weapons/passives and drawn Dragonblade."""
        from ow174.game import npc_melee

        instance = state.instance
        component = instance.component
        if component.entity not in self.locks and not self.component_locked_at(component, component.frame):
            return False
        if instance.graph.index == 0x0317 and state.flags & nodes.Ability.RUNNING:
            return False
        if instance.graph.index == 0x0043 or npc_melee.ability(component) is state:
            return False
        if instance.graph.index == HACK and state.index == 53:
            return True
        if instance.graph.index == 0x0095 and state.node.pos == 136:
            return True
        target = next((target for target in self.fight._targets()
                       if target.entity == component.entity), None)
        if target is None:
            return True
        body = target.owner.body if isinstance(target.owner, bots.Bot) else target.owner.hero.body
        record = graph.entity_body(body) or graph.bodies().get(body, {})
        weapons = set(record.get("weapons") or ()) - {None}
        if (not isinstance(target.owner, bots.Bot) and target.owner.hero.guid == 0x02E000000000006E
                and 29 in state.buttons()):
            return True
        parent = instance
        while parent is not None:
            if parent.graph.index in weapons:
                return False
            parent = parent.parent
        return any(state.buttons())

    def button_allowed(self, component, button):
        """Original logical-button consumers on this recipient component clock."""
        if (component.entity not in self.locks and not self.component_locked_at(component, component.frame)
                or button in (14, 20, 31, 111)):
            return True
        if component.world.hero == 0x02E0000000000003 and button in (29, 35):
            return False  # Original Tracer Blink bindings.
        if component.world.hero == 0x02E0000000000008 and button in (37, 11, 29):
            return False
        if button == 29 and component.world.hero == 0x02E000000000023B:
            return False
        if button == 36 and component.world.hero == 0x02E00000000001BB:
            return False
        for instance in component.instances.values():
            for state in instance.states.values():
                if (isinstance(state, nodes.Ability) and state.active and button in state.buttons()
                        and self.blocked(state)):
                    return False
        return True

    def interrupt(self, player, *, hack_only=False):
        script = getattr(player, "body_script", None)
        if script is None:
            return
        if not hack_only and getattr(script, "hero", None) == 0x02E0000000000008:
            script.interrupt_control()
        if not hack_only and getattr(script, "hero", None) == 0x02E00000000001CA:
            from ow174.game import ball

            ball.cancel_grapple(self.fight, player)
        for instance in tuple(script.component.instances.values()):
            if hack_only and instance.graph.index != HACK:
                continue
            for state in tuple(instance.states.values()):
                if not isinstance(state, nodes.Ability):
                    continue
                if hack_only or self.blocked(state):
                    state.presses.clear()
                    state.stop()

    def retire(self, entity):
        self.lock_history.pop(entity, None)
        self.held.pop(entity, None)
        self.device_receivers.pop(entity, None)
        for lock in self.locks.pop(entity, ()):
            self.destroy(lock)
        self.restore_body(entity)

    @staticmethod
    def destroy(lock):
        for application in (lock.application, lock.pulse):
            if application is None:
                continue
            component = application.component
            if component.instances.get(application.id) is application:
                component.destroy(application)

    def update(self):
        from ow174.game.sombra_replay import _shown_sources

        sources = _shown_sources(self.fight.match)
        for entity, (component, windows) in tuple(self.lock_history.items()):
            if component.owner_dead:
                self.lock_history.pop(entity, None)
            elif windows:
                windows[:] = [window for window in windows[:-1]
                              if component.frame*component.frame_ms < window[1]] + windows[-1:]
        for component in tuple(self.device_receivers.values()):
            if type(component.world) is DeviceWorld and component.frame < self.fight.tick:
                component.run_frame(self.fight.tick)
        for entity, locks in tuple(self.locks.items()):
            vitals = self.fight.vitals.get(entity)
            if entity not in self.device_receivers and (vitals is None or vitals.dead):
                self.retire(entity)
                continue
            keep = []
            for lock in locks:
                self.observe_lock(lock.application)
                orphaned = lock.application.source_entity() not in sources
                if self.fight.now < lock.until and not (orphaned and lock.application.stopped):
                    keep.append(lock)
                else:
                    self.destroy(lock)
            if keep:
                self.locks[entity] = keep
            else:
                del self.locks[entity]
                self.device_receivers.pop(entity, None)
                self.restore_body(entity)

    def endpoint_slot(self, body, entity, var):
        from ow174.game.symmetra_portals import ENDPOINT_SLOTS

        pair, _ = self.fight.portals.endpoint_pair(body)
        if pair is None or var not in ENDPOINT_SLOTS:
            return NotImplemented
        _, owner = self.fight.portals.endpoint_owner(body)
        if owner is None or owner.entity != entity:
            return None
        endpoint = pair.endpoints[ENDPOINT_SLOTS.index(var)]
        slot = owner.entity_var(var, create=False)
        return slot if slot is not None and slot.value() == expr.Entity(endpoint.entity) else None

    def device_binding(self, entity):
        """Original device tags on the existing summoned actor, without another host."""
        from ow174.game import symmetra_portals

        for turret in self.fight.turrets.values():
            if turret.actor.entity == entity:
                return turret.actor, frozenset((0x34,)), True
        for sentry in self.fight.sentries.sentries:
            if sentry.bot.entity == entity:
                return sentry.bot, frozenset((0x36F,)), sentry.attached_at is not None
        pair, index = self.fight.portals.endpoint_pair(entity)
        if pair is not None:
            return pair.endpoints[index], symmetra_portals.ENDPOINT_TAGS[index], True
        return None

    def enemies(self, player):
        from ow174.game import ball_mines

        return (target for target in self.fight._enemies(player)
                if not isinstance(target.owner, (actors.Deployable, ball_mines.Mine))
                and getattr(target.owner, "body_script", None) is not None
                and self.device_binding(target.entity) is None
                and not self.fight.damage_immune(target.owner))

    def hackable(self, player, *, emp=False):
        from ow174.game.combat import Target

        yield from self.enemies(player)
        yield from (target for target, _ in self.device_targets(player, emp=emp))
        for pack in self.fight.health_packs.values():
            if health_packs.available_to(self.fight, pack, player):
                yield Target(pack.entity, None, pack.position, 0.7, 0.35, 0.35, pack)

    def device_targets(self, player, *, emp=False):
        from ow174.game import ball_mines
        from ow174.game.combat import Target

        fight = self.fight
        for ward in fight.baptiste.fields.values():
            actor = ward.actor
            if (actor.health > 0 and actor.owner is not player and not actor.owner.gone
                    and (fight.match.game_map.free_for_all or actor.team != player.team)):
                yield fight._deployable_target(actor), frozenset((0x6E6,))
        for target in fight._targets():
            binding = self.device_binding(target.entity)
            if binding is None:
                continue
            actor, tags, attached = binding
            owner = getattr(actor, "combat_owner", None)
            if (attached and not actor.dead and owner is not None and owner is not player
                    and not owner.gone and (fight.match.game_map.free_for_all or target.team != player.team)):
                yield target, tags
        manager = getattr(fight, "ball_mines", None)
        if emp and manager is not None:
            radius = ball_mines.DEVICE_RADIUS
            for device in manager.enemies(player, include_flights=True):
                x, y, z = device.centre()
                yield Target(device.body, None, (x, y-radius, z), 2*radius, radius, radius, device), ball_mines.TAGS

    @staticmethod
    def invoke(component, definition, request):
        """Start the actual loaded request with its authored start-entry context."""
        before = {instance.id for instance in component.instances.values()}
        queue = play_queue.Queue(component, lambda _: play_queue.Load("ready", definition))
        queue.submit(request)
        matches = [instance for instance in component.instances.values()
                   if instance.graph.index == definition.index]
        return next((instance for instance in matches if instance.id not in before),
                    matches[0] if matches and request.policy == 2 else None)

    def apply_devices(self, instance, definition, resource, request, caster):
        from ow174.game import ball_mines

        if caster is None:
            return
        for target, tags in self.device_targets(caster, emp=instance.graph.index in (EMP, PULSE)):
            if target.entity not in request.targets:
                continue
            component = self.device_receivers.get(target.entity)
            if component is None:
                script = getattr(target.owner, "body_script", None)
                component = script.component if script is not None else getattr(target.owner, "component", None)
                if component is None:
                    world = DeviceWorld(self.fight, target.entity, tags)
                    component = runtime.Component(target.entity, world=world)
                    component.frame = self.fight.tick
                    component.now = component.frame * component.frame_ms
                self.device_receivers[target.entity] = component
            params, overrides = request.params, resource.overrides
            if component is not instance.component:
                # Transfer values without sharing either component's private VM storage.
                params, overrides = deepcopy((params, overrides))
            transferred = replace(request, params=params,
                                  resource=replace(request.resource, overrides=overrides))
            application = self.invoke(component, definition, transferred)
            if application is None:
                if target.entity not in self.locks:
                    self.device_receivers.pop(target.entity, None)
                continue
            if definition.index == EFFECT:
                component.run_now()
                duration = expr.to_float(application.find_var(graph.INSTANCE, 215).value())
                if duration > 0 and math.isfinite(duration):
                    locks = self.locks.setdefault(target.entity, [])
                    locks[:] = [lock for lock in locks if lock.application is not application]
                    pulse = instance if instance.graph.index == PULSE else None
                    until = self.fight.now + duration
                    if isinstance(target.owner, ball_mines.Mine):
                        # Mine Components are born on this same world clock;
                        # their actual entry cursor can be at frame end (+15ms).
                        until = (application.start + round(duration * 1000)) / 1000
                    locks.append(Lock(application, until, pulse, (self.fight.tick, application.start)))
                    self.remember_lock(application, duration)
                    for sentry in self.fight.sentries.sentries:
                        if sentry.bot is target.owner:
                            self.fight.sentries._clear_slow(sentry)
            elif component is not instance.component:
                component.run_now()
            if target.entity not in self.locks:
                self.device_receivers.pop(target.entity, None)

    def drain_device(self, state):
        entity = state.instance.component.entity
        amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth")["m_amount"], watch=False))
        if self.device_binding(entity) is not None:
            self.drain(state)

    def body_barrier(self, target):
        from ow174.game import barriers

        if target.owner is None or not hasattr(target.owner, "body_script"):
            return None
        for barrier in self.fight.barriers.scripted.values():
            protected = barrier.recipient if barrier.recipient is not None else barrier.parent
            if (protected is target.owner and (barrier.recipient_body or barrier.parent_body) == target.entity
                    and barrier.component.world.hero == barriers.ZARYA and barrier.vitals is not None
                    and barrier.vitals.total > 0 and barriers._live(self.fight, barrier)):
                return barrier
        return self.fight.barriers.for_target(target.owner) or self.fight._active_projected_barrier(target.entity)

    def clear(self, player, origin, ray, reach, *, barriers=True):
        from ow174.game.combat import ray_world

        if self.fight.match.collision_world is None:
            return False
        wall = ray_world(self.fight._collision(), origin, ray, reach)
        if wall is not None and wall < reach:
            return False
        if self.fight.walls.cast(origin, ray, reach) is not None:
            return False
        return not barriers or (
            self.fight._barrier_contact(player, origin, ray, reach) is None
            and self.fight.brigitte.barrier_ray(player, origin, ray, reach) is None
        )

    def native(self, body, cfg, instance):
        cls = cfg.get("$")
        binding = self.device_binding(body)
        if binding is not None and instance.graph.index in (EFFECT, PULSE):
            if cls == "STU_25ECB55F" and expr.to_entity(instance.evaluate(cfg.get("m_entity"))) == body:
                return (expr.guid_of(cfg.get("m_88B0050A")) & 0xFFFFFFFFFFFF) in binding[1]
            if instance.graph.index == PULSE and cls == entity_queries.POSSESSED:
                for position in (15, 16):
                    if cfg is instance.graph.node(position).field("m_targets")["m_configVars"][0]:
                        _, owner = self.fight.portals.endpoint_owner(body)
                        return expr.Entity(owner.entity if owner is not None else 0)
        if (instance.graph.index == EFFECT and cls == "STU_AC829876"
                and cfg is instance.graph.node(21).field("m_condition")):
            # Original receiver predicate: the actual body can receive Hack.
            if binding is not None:
                return False
            target = next((target for target in self.fight._targets()
                           if target.entity == body), None)
            script = getattr(getattr(target, "owner", None), "body_script", None)
            if (script is None or getattr(script, "retired", False)
                    or script.component is not instance.component or script.component.owner_dead):
                return False
            reject = script.component.entity_var(14679, create=False)
            return reject is None or not expr.truthy(reject.value())
        if (instance.graph.index == PULSE and cls == "STU_AC829876"
                and cfg is instance.graph.node(8).field("m_condition")):
            # Body receiver; attached barrier pools use the existing drain bridge.
            return False
        if instance.graph.index != HACK:
            return NotImplemented
        if cls == "STU_AC829876" and cfg is instance.graph.node(204).field("m_condition"):
            player = self.player(body)
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            return player is not None and any(t.entity == entity for t in self.hackable(player))
        if (cls == "STU_53F4CA65"
                and cfg is instance.graph.node(164).field("m_condition")["m_configVars"][0]):
            entity = expr.to_entity(instance.find_var(graph.INSTANCE, 8909).value())
            pack = self.fight.health_packs.get(entity)
            if pack is not None:
                player = self.player(body)
                return player is not None and health_packs.available_to(self.fight, pack, player)
        return NotImplemented

    def targets(self, player, state):
        from ow174.game.combat import aim, ray_capsule

        scope = state.instance.graph.index, state.node.pos
        if scope not in ((HACK, 50), (HACK, 52), (HACK, 68), (EMP, 40)):
            return NotImplemented
        if self.fight.is_dead(player):
            return ()
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        if not math.isfinite(radius) or radius <= 0:
            return ()
        origin = self.fight._eye(player)
        direction = aim(player.mover.yaw, player.mover.pitch)
        selected = state.instance.find_var(graph.INSTANCE, 375).value()
        found = []
        for target in self.hackable(player, emp=scope[0] == EMP):
            if scope[0] == HACK and self.body_barrier(target) is not None:
                continue
            a, b = target.segment()
            offset = (a[0] - origin[0], max(a[1], min(b[1], origin[1])) - origin[1], a[2] - origin[2])
            distance = math.hypot(*offset)
            if scope == (HACK, 52):
                if not isinstance(selected, tuple) or expr.Entity(target.entity) not in selected:
                    continue
                if sum(axis * facing for axis, facing in zip(offset, direction, strict=True)) <= 0:
                    continue
            if scope in ((HACK, 52), (EMP, 40)):
                ray = tuple(axis / distance for axis in offset) if distance else direction
                reach = max(0.0, distance - target.radius)
            else:
                ray = direction
                reach = ray_capsule(origin, ray, a, b, target.radius)
            if reach is not None and reach <= radius and self.clear(player, origin, ray, reach, barriers=scope[0] == HACK):
                found.append((reach, target.entity))
        found.sort()
        return tuple(expr.Entity(entity) for _, entity in (found if scope[0] == EMP else found[:1]))

    def preselect(self, player, command):
        if player.hero.guid != HERO or not command.buttons & 2 or self.fight.is_dead(player):
            return
        component = player.body_script.component
        if component.strict:
            return
        for instance in component.instances.values():
            if instance.graph.index != HACK:
                continue
            ability = instance.state(53)
            key = (graph.INSTANCE, 375)
            if ability.flags & nodes.Ability.RUNNING and instance.find_var(*key).value():
                return
            search = instance.node_state(instance.graph.node(68))
            instance.write(key, self.targets(player, search))

    def ray(self, player, state):
        key = state.instance.find_var(graph.INSTANCE, 8909).value()
        target = next((target for target in self.hackable(player) if expr.Entity(target.entity) == key), None)
        if target is None or self.body_barrier(target) is not None:
            return runtime.RaycastResult(False, expr.Entity(0), None, None)
        origin = self.fight._eye(player)
        x, y, z = target.feet
        point = (x, y + target.eye, z)
        delta = tuple(end - start for start, end in zip(origin, point, strict=True))
        distance = math.hypot(*delta)
        clear = not distance or self.clear(player, origin, tuple(axis / distance for axis in delta),
                                           max(0.0, distance - target.radius))
        return runtime.RaycastResult(clear, expr.Entity(target.entity if clear else 0), expr.Vec3(*point), None)

    def play(self, instance, node):
        from ow174.game import ball_mines

        if instance.component.strict or instance.graph.index not in (HACK, EMP, PULSE):
            return NotImplemented
        resource = play.resolve_resource(instance, node)
        if resource is None:
            return NotImplemented
        definition = graph.graph(resource.guid & 0xFFFFFFFFFFFF)
        if definition is None or (instance.graph.index, definition.index) not in (
            (HACK, EFFECT), (EMP, PULSE), (PULSE, EFFECT),
        ):
            return NotImplemented
        source = instance.source_entity() if instance.graph.index == PULSE else instance.component.entity
        if instance.graph.index == EMP:
            caster = self.player(source)
            if caster is not None:
                self.drain_barriers(caster, instance, definition)
        context = play.InvocationContext(source, source, source)
        request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
        if request is None:
            return True
        fight = self.fight
        caster = self.player(source)
        self.apply_devices(instance, definition, resource, request, caster)
        if fight.health_packs:
            for entity in request.targets:
                pack = fight.health_packs.get(entity)
                if pack is None or caster is None or not health_packs.available_to(fight, pack, caster):
                    continue
                if definition.index == EFFECT:
                    health_packs.apply_hack(pack, definition, resource, request)
                else:
                    health_packs.submit(pack.component, definition, resource, request)
        for target in fight._targets():
            if isinstance(target.owner, (actors.Deployable, ball_mines.Mine)) or self.device_binding(target.entity) is not None:
                continue
            if target.entity not in request.targets or fight.vitals_of(target).dead or fight.damage_immune(target.owner):
                continue
            if instance.graph.index == HACK and self.body_barrier(target) is not None:
                continue
            script = getattr(target.owner, "body_script", None)
            if script is None:
                continue
            # Original0200 recipient invocation replaces the previous graph,
            # including its start-entry lifetime, before submitting this request.
            for prior in tuple(script.component.instances.values()):
                if prior.graph.index == definition.index:
                    script.component.destroy(prior)
            application = self.invoke(script.component, definition, request)
            if application is not None and definition.index == EFFECT:
                duration = expr.to_float(application.evaluate(application.graph.node(96).field("m_value")))
                if math.isfinite(duration) and duration > 0:
                    locks = self.locks.setdefault(target.entity, [])
                    locks[:] = [lock for lock in locks if lock.application is not application]
                    pulse = instance if instance.graph.index == PULSE else None
                    locks.append(Lock(application, fight.now + duration, pulse, (fight.tick, application.start)))
                    self.silence_body(script.component)
                    self.remember_lock(application, duration)
                    held = getattr(script, "buttons", 0) & SKILL_INPUT
                    if held:
                        self.held[target.entity] = self.held.get(target.entity, 0) | held
                    if isinstance(target.owner, bots.Bot):
                        from ow174.game import assassin, heavy_assault

                        assassin.stop(target.owner)
                        heavy_assault.stop(script.component)
                    self.interrupt(target.owner)
                    fight.breathers.retire(target.entity)
            if script.component is not instance.component:
                script.component.run_now()
        return True

    def drain_barriers(self, caster, instance, definition):
        """Apply the barrier branch to visible geometry already hosted by combat."""
        fight = self.fight
        radius = expr.to_float(instance.evaluate(instance.graph.node(40).field("m_radius")))
        config = definition.node(3).field("m_modifyHealth")
        amount = expr.to_float(instance.evaluate(config["m_amount"]))
        if not all(math.isfinite(value) and value > 0 for value in (radius, amount)):
            return
        origin = fight._eye(caster)
        from ow174.game import sigma_barrier

        for manager, actors, closest in (
            (fight.barriers.world, fight.barriers.world.enemies(caster), fight.barriers.world.nearest_shell),
            (fight.orisa_barriers, fight.orisa_barriers.opponents(caster), fight.orisa_barriers.closest),
            (fight.photon_barriers, fight.photon_barriers.opponents(caster), fight.photon_barriers.closest),
        ):
            for barrier in tuple(actors):
                point = closest(barrier, origin)
                if point is not None and self.emp_reaches(caster, origin, point, radius):
                    manager.damage(barrier, amount)
        for barrier in tuple(sigma_barrier.enemies(fight, caster)):
            if barrier.closest_visible_distance(fight, caster, origin, radius) is not None:
                sigma_barrier.damage_barrier(fight, barrier, amount)
        raised = fight.barriers.reinhardt
        for barrier in tuple(raised.enemies(caster)):
            if barrier.near(origin, radius, lambda point: self.emp_reaches(caster, origin, point, radius)):
                raised.damage(barrier, amount)
        for barrier, centre, _, right, up in fight.brigitte._barrier_faces(caster):
            offset = tuple(origin[k] - centre[k] for k in range(3))
            horizontal = max(-1, min(1, sum(a * b for a, b in zip(offset, right, strict=True))))
            vertical = max(-1.1, min(1.1, sum(a * b for a, b in zip(offset, up, strict=True))))
            point = tuple(centre[k] + horizontal * right[k] + vertical * up[k] for k in range(3))
            if self.emp_reaches(caster, origin, point, radius):
                fight.brigitte.damage_barrier(barrier, amount)

    def emp_reaches(self, caster, origin, point, radius):
        delta = tuple(end - start for start, end in zip(origin, point, strict=True))
        distance = math.hypot(*delta)
        return distance <= radius and (not distance or self.clear(
            caster, origin, tuple(axis / distance for axis in delta), distance, barriers=False,
        ))

    def drain(self, state):
        fight = self.fight
        entity = state.instance.component.entity
        target = next((target for target in fight._targets() if target.entity == entity), None)
        if target is None:
            return
        vitals = fight.vitals_of(target)
        if vitals.dead:
            return
        amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth")["m_amount"], watch=False))
        if not math.isfinite(amount) or amount <= 0:
            return
        from ow174.game import barriers

        barrier = self.body_barrier(target)
        if isinstance(barrier, barriers.Barrier):
            barriers.damage(fight, barrier, amount)
            return
        if isinstance(barrier, barriers.Bubble):
            fight.barriers.damage(barrier, amount)
            return
        if barrier is not None:
            barrier.health = max(0.0, barrier.health - amount)
            return
        from ow174.game.combat import health_tags

        caster = self.player(state.instance.source_entity())
        if caster is None:
            return
        script = caster.body_script
        frame = script.component.frame if script is not None else fight.tick
        fight._damage(caster, target, amount, False, None, frame, world_tick=fight.tick,
                      source_scaled=True, shield_drain=True, award_ult=False,
                      hit_tags=health_tags(state, state.node.field("m_modifyHealth")))
