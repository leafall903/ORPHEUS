"""Assemble the authored Junkenstein map and mode in a match's scene host."""

import hashlib
import json
import math
from collections import deque
from dataclasses import dataclass, field, replace
from functools import cache, partial

from ow174.game import (
    collision,
    combat,
    content,
    junkenstein_ai,
    junkenstein_dialogue,
    npc,
    npc_navigation,
    observers,
    pools,
    props,
    pve,
)
from ow174.game import world as wire
from ow174.game.match import PLAYER_FILTER, TEAM_BIT, Player, _rotation
from ow174.game.script import (
    controller_heroes,
    entity_control,
    expr,
    graph,
    message_receiver,
    message_state,
    modifiers,
    nodes,
    play_queue,
    runtime,
    scene_host,
    scopes,
)
from ow174.game.script.driver import WheelCallout
from ow174.paths import DATA_DIR

MODE_ENTITY = 0x04000000000016B2
MODE_VOICE_SET = 0x07A000000000001D
MODE_NARRATOR_FILTER = 0x80000
# The two encounter rows in 104319 resource 013B.054.
SCORE_THRESHOLDS = {
    0x0230000000000003: (3000.0, 6000.0, 10000.0, 15000.0, 20000.0),
    0x023000000000002A: (1500.0, 3000.0, 6000.0, 9000.0, 16000.0),
}
SCORE_LIMITS = {0x0D8000000000195E: 3, 0x0D8000000000195F: 4}
READY_GRAPH, READY_NODE, SELECTED_HERO = 0x0C90, 11, 2114
TEAM_CONTROLLER_GRAPH, TEAM_BODY_GRAPH = 0x0CA2, 0x0CA5
WAVE_RECEIVER_NODE = 22
COMMON_CORPSE_GRACE_MS = 8000
SUMMONER_BARRIER = 0x04000000000026B0
VENGEFUL_GHOST = 0x0400000000003C53
# The blue team's available-heroes list in 104319 ruleset 011D.0C0.
MYSTERY_SWAP_HEROES = tuple(0x02E0000000000000 | index for index in (
    0x13B, 0x200, 0x005, 0x042, 0x020, 0x221, 0x006, 0x195, 0x06E, 0x029, 0x003, 0x00A,
))
SUMMONER_BARRIER_MESH_SHA256 = 'd11a09fc7224c2b73819a06a40f9dbb1cb35ff681c373aa1891a987de6eedfe8'


@cache
def _summoner_barrier_mesh():
    raw = (DATA_DIR / 'junkenstein' / 'symmetra_collision_174.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != SUMMONER_BARRIER_MESH_SHA256:
        raise ValueError('Summoner barrier collision differs from the prepared 104319 model')
    data = json.loads(raw)
    return collision.RayWorld(data['triangles'], name='Photon Barrier 104319')


@dataclass
class PhotonBarrier:
    entity: int
    state: runtime.State
    controller: int
    team: int
    position: tuple[float, float, float]
    yaw: int
    health: float
    maximum: float

    def trace(self, origin, direction, length):
        angle = self.yaw * math.tau / 65536
        sine, cosine = math.sin(angle), math.cos(angle)
        x, y, z = (value - base for value, base in zip(origin, self.position, strict=True))
        dx, dy, dz = direction
        local = (x * cosine - z * sine, y, x * sine + z * cosine)
        toward = (dx * cosine - dz * sine, dy, dx * sine + dz * cosine)
        hit = _summoner_barrier_mesh().cast_ray(local, toward, length)
        return hit.distance if hit is not None else None


@dataclass
class VengefulGhost(combat.SceneHazard):
    position: tuple[float, float, float]
    yaw: int = 0
    actions: list[WheelCallout] = field(default_factory=list)


@dataclass
class PlayerBinding:
    player: Player
    controller: runtime.Component
    body: int | None = None
    sender: runtime.Instance | None = None


class JunkensteinSceneHost(scene_host.SceneHost):
    """Connect match player lifetimes to the encounter's team script branches."""

    def __init__(self, world, **options):
        super().__init__(world, **options)
        self.player_bindings: dict[int, PlayerBinding] = {}
        self.replicate_mode = False
        self.mode_projection = None
        self.mode_views = {}
        self.mode_actions = {}
        self.gate_team = None
        self.barriers: dict[int, PhotonBarrier] = {}
        self.ghost: VengefulGhost | None = None
        self.messages = message_state.Host(notify=message_receiver.notify)
        self.world.queue_mode_action = self._queue_mode_action
        self.world.queue_ghost_action = self._queue_ghost_action
        self.world.queue_mystery_swap = self._queue_mystery_swap
        self.pending_mystery_swaps: dict[int, int] = {}
        self.world.create_summoner_barrier = self._create_summoner_barrier
        self.world.create_vengeful_ghost = self._create_vengeful_ghost
        self.world.retire_vengeful_ghost = self._retire_vengeful_ghost
        self.world.update_vengeful_ghost = self._update_vengeful_ghost
        self.world.ghost_targets = self._ghost_targets
        self.world.ghost_contact_damage = self._ghost_contact_damage

    def _create_vengeful_ghost(self, definition, position):
        resource = graph.junkenstein_entity(definition)
        if definition != VENGEFUL_GHOST or self.ghost is not None or resource is None:
            raise ValueError('Vengeful Ghost requires its authored resource and a free lifecycle')
        fight = self.match.combat
        entity = fight.next_bot
        fight.next_bot += 1
        ghost = VengefulGhost(entity, 1, 'Vengeful Ghost', tuple(position))
        world = self.world
        binding = world.bind(entity, definition=definition, target_tags=resource['target_tags'],
                             position_reader=lambda: ghost.position, rotation_reader=lambda: _rotation(ghost.yaw))
        self.ghost = ghost
        try:
            world.set_entity_query_context(entity)
            component = self.add_spawned_entity_scripts(entity, junkenstein=True)
            self.configure_component(component)
            world.bind_data_flow_providers(entity)
            world.set_subgraph_scopes(entity, scopes.ForwardHost(component))
        except Exception:
            self._retire_vengeful_ghost(entity)
            raise
        return binding

    def _retire_vengeful_ghost(self, entity):
        if self.ghost is None or self.ghost.entity != entity:
            return False
        published = self.spawn_projection(entity) is not None
        self.retire_entity(entity)
        self.ghost = None
        if published and not self.closed:
            for client in self._spawned_entity_viewers():
                client.queue_bot_destroy(entity)
        return True

    def _update_vengeful_ghost(self, entity, position, yaw):
        ghost = self.ghost
        if (ghost is None or ghost.entity != entity or not isinstance(position, expr.Vec3)
                or not all(math.isfinite(axis) for axis in position)
                or not isinstance(yaw, (int, float)) or not math.isfinite(yaw)):
            return NotImplemented
        ghost.position = tuple(position)
        ghost.yaw = (round(yaw * 65536 / 360) + 32768) % 65536 - 32768
        return True

    def _ghost_targets(self, state):
        instance, node = state.instance, state.node
        ghost = self.ghost
        if ghost is None or ghost.entity != instance.component.entity:
            return NotImplemented
        radius = state.evaluate(node.field('m_radius'), watch=False)
        anchor = state.evaluate(node.field('m_8DCA77B6'), watch=False)
        if (anchor != expr.Entity(ghost.entity) or radius not in (2.5, 8)
                or expr.guid_of(node.field('m_762A56C0.m_hardPoint')) != 0x0DC0000000000004):
            return NotImplemented
        fight = self.match.combat
        # The 3AF3 model places the search hardpoint at the entity root.
        targets = []
        for target in fight._enemies(ghost):
            if not isinstance(target.owner, Player) or target.owner.gone:
                continue
            match = self.world._filter_entity(instance, target.entity, node.field('m_filter'))
            if match is NotImplemented:
                return NotImplemented
            if match and math.dist(ghost.position, target.feet) <= radius:
                targets.append(target.entity)
        return targets

    def _ghost_contact_damage(self, state, elapsed_ms):
        instance, node = state.instance, state.node
        ghost = self.ghost
        config = node.field('m_modifyHealth') or {}
        if (ghost is None or ghost.entity != instance.component.entity or config.get('m_93CDFD2F') != 1
                or state.evaluate(config.get('m_instigator'), watch=False) != expr.Entity(ghost.entity)
                or state.evaluate(node.field('m_95F1FB0B'), watch=False) != 1):
            return NotImplemented
        if not elapsed_ms:
            return True
        victims = state.evaluate(node.field('m_FA3ECC43'), watch=False)
        if not isinstance(victims, tuple):
            return NotImplemented
        hit = combat.hit_def(state, {'m_modifyHealth': config})
        damage = expr.f32(hit.amount * elapsed_ms / 1000)
        fight = self.match.combat
        frame = instance.component.frame
        for target in fight._enemies(ghost):
            if (expr.Entity(target.entity) in victims and isinstance(target.owner, Player)
                    and not target.owner.gone and math.dist(ghost.position, target.feet) <= 2.5):
                fight._damage(ghost, target, damage, False, hit.loadout, frame,
                              world_tick=frame, hit_tags=hit.tags)
        return True

    def _create_summoner_barrier(self, state):
        instance, node = state.instance, state.node
        component = instance.component
        registration = self.combatants.get(component.entity)
        definition = expr.guid_of(node.field('m_entityDefinition.m_entityDef'))
        if (registration is None or registration[0].hero != junkenstein_ai.SYMMETRA
                or self.combatant_component(registration[0]) is not component or definition != SUMMONER_BARRIER):
            component.unsupported(instance, 'Summoner barrier owner', node.cls, node)
        owner = registration[0]
        if owner.dead:
            state.request(runtime.ABORT)
            return None
        controller = expr.to_entity(state.evaluate(node.field('m_D749D7D8'), watch=False))
        if self.world.current_possessions.get(controller) != component.entity:
            component.unsupported(instance, 'Summoner barrier controller', node.cls, node)
        values = []
        for field in ('m_position', 'm_AD8E614B'):
            value = state.evaluate(node.field(field), watch=False)
            if isinstance(value, tuple) and len(value) == 1:
                value = value[0]
            if not isinstance(value, expr.Vec3) or not all(math.isfinite(axis) for axis in value):
                component.unsupported(instance, 'Summoner barrier transform', node.cls, node)
            values.append(value)
        position, axis = values
        resource = graph.junkenstein_entity(definition)
        sizes = pools.authored_pools(resource)
        if sizes is None or sizes[0] <= 0 or not math.hypot(axis.x, axis.z):
            component.unsupported(instance, 'Summoner barrier resource', node.cls, node)
        _summoner_barrier_mesh()
        health = sizes[0]
        fight = self.match.combat
        entity = fight.next_bot
        fight.next_bot += 1
        yaw = round(math.atan2(axis.x, axis.z) * 65536 / math.tau)
        barrier = PhotonBarrier(entity, state, controller, owner.team, tuple(position), yaw, health, health)
        world = self.world
        world.bind(entity, definition=definition, position=barrier.position,
                   target_tags=resource.get('target_tags'),
                   rotation_reader=lambda: _rotation(yaw), health=lambda: barrier.health,
                   maximum_health=lambda: health, normal_health=lambda maximum: health if maximum else barrier.health,
                   health_pool=lambda kind: barrier.health if kind == 0 else 0.0,
                   pool_capacity=lambda kind: health if kind == 0 else 0.0)
        try:
            owned = tuple(item.entity for item in self.barriers.values() if item.controller == controller)
            world.set_owned_deployables(controller, (*owned, entity))
            world.set_entity_query_context(entity)
            self.add_spawned_entity_scripts(entity, values={combat.OWNER_HEALTH: health}, junkenstein=True)
            world.bind_data_flow_providers(entity)
        except Exception:
            self.retire_entity(entity)
            raise
        self.barriers[entity] = barrier
        instance.write(expr.dynamic(node.field('m_B3019338')), expr.Entity(entity))

        def retire():
            if self.barriers.pop(entity, None) is barrier:
                published = self.spawn_projection(entity) is not None
                self.retire_entity(entity)
                if published and not self.closed:
                    for client in self._spawned_entity_viewers():
                        client.queue_bot_destroy(entity)

        return retire

    def _spawned_entity_viewers(self):
        return (player.client for player in self.match.players
                if player.spawned and not player.gone and player.client is not None)

    @staticmethod
    def _barrier_create(barrier):
        components = wire.health(barrier.health, barrier.maximum)
        return wire.EntityUpdate(barrier.entity, wire.OP_CREATE, lambda origin: wire.create(
            origin, SUMMONER_BARRIER, components, position=barrier.position, rotation=_rotation(barrier.yaw)))

    @staticmethod
    def _ghost_create(ghost):
        position, rotation = ghost.position, _rotation(ghost.yaw)
        return wire.EntityUpdate(ghost.entity, wire.OP_CREATE, lambda origin: wire.create(
            origin, VENGEFUL_GHOST, {}, position=position, rotation=rotation))

    def spawned_entity_updates(self):
        for barrier in self.barriers.values():
            if barrier.health > 0 and self.spawn_projection(barrier.entity) is not None:
                yield self._barrier_create(barrier)
        ghost = self.ghost
        if ghost is not None and self.spawn_projection(ghost.entity) is not None:
            yield self._ghost_create(ghost)

    def spawned_entity_movements(self, viewer):
        ghost = self.ghost
        if (ghost is not None and self.spawn_projection(ghost.entity) is not None
                and viewer.client is not None and viewer.client.bot_create_acked(ghost.entity)):
            # 3C53 has STU_E272A026, native component 28 consumed by channel 3.
            pose = wire.Movement(ghost.position, yaw=ghost.yaw, input_frame=wire.NO_INPUT)
            yield wire.EntityUpdate(ghost.entity, movement=pose)

    def _publish_spawned_script(self, entity, snapshot, _dead):
        for client in self._spawned_entity_viewers():
            client.queue_bot_live(entity, snapshot)

    def _publish_new_spawned_entities(self):
        spawned = [(barrier, self._barrier_create) for barrier in self.barriers.values()]
        if self.ghost is not None:
            spawned.append((self.ghost, self._ghost_create))
        for actor, make_create in spawned:
            entity = actor.entity
            if entity in self.body_projections:
                continue
            # Complete initial PlayScript requests before taking the first snapshot.
            actions = (lambda ghost=actor: ghost.actions) if actor is self.ghost else None
            projection = observers.SceneBody(self.units[entity].component, actions=actions)
            self.body_projections[entity] = projection, partial(self._publish_spawned_script, entity)
            for client in self._spawned_entity_viewers():
                client.queue_entities([make_create(actor)])

    def trace_barrier(self, shooter, origin, direction, length):
        nearest = None
        for barrier in self.barriers.values():
            if barrier.health <= 0 or barrier.team == shooter.team:
                continue
            distance = barrier.trace(origin, direction, length)
            if distance is not None and (nearest is None or distance < nearest[0]):
                nearest = distance, barrier
        return nearest

    def damage_barrier(self, barrier, amount, hit_tags=()):
        if self.barriers.get(barrier.entity) is not barrier or barrier.health <= 0 or amount <= 0:
            return
        damage = self.world.incoming_barrier_damage(barrier.entity, amount, hit_tags)
        barrier.health = expr.f32(max(0.0, barrier.health - damage))
        self.units[barrier.entity].component.set(combat.OWNER_HEALTH, barrier.health)
        if self.spawn_projection(barrier.entity) is not None:
            components = wire.health(barrier.health, barrier.maximum)
            for client in self._spawned_entity_viewers():
                client.queue_body_health(barrier.entity, components)
        if barrier.health == 0:
            barrier.state.request(runtime.ABORT)

    def _queue_mode_action(self, instance, node):
        component = instance.component
        if (component.entity != runtime.GAME_MODE_ENTITY
                or self.world.script_components.get(component.entity) is not component
                or component.instances.get(instance.id) is not instance or instance.stopped
                or not node.is_networked or node.remote < 0):
            component.unsupported(instance, "mode action source lifecycle", node.cls, node)
        if self.replicate_mode:
            for player in self.match.players:
                if player.spawned and not player.gone and player.client is not None:
                    self.mode_actions.setdefault(player.client, deque()).append((instance.id, node.pos))

    def _queue_ghost_action(self, instance, node):
        ghost = self.ghost
        component = instance.component
        if (ghost is None or ghost.entity != component.entity or instance.graph.index != 0x2B72
                or node.pos not in (68, 93) or not node.is_networked or node.remote < 0
                or component.instances.get(instance.id) is not instance or instance.stopped):
            component.unsupported(instance, 'ghost presentation action', node.cls, node)
        if self.replicate_mode:
            ghost.actions.append(WheelCallout(len(ghost.actions) + 1, instance.id, instance.start,
                                             node.pos, component.now, None, ()))

    def _queue_mystery_swap(self, instance, hero):
        component = instance.component
        binding = self.player_bindings.get(component.entity)
        if (self.match.card != content.JUNKENSTEIN_MYSTERY_SWAP or binding is None
                or binding.controller is not component or instance.parent is not None):
            component.unsupported(instance, 'Mystery Swap controller', instance.graph.node(26).cls,
                                  instance.graph.node(26))
        if not binding.player.has_body or binding.player.gone:
            return
        if not isinstance(hero, expr.Asset) or hero.guid not in MYSTERY_SWAP_HEROES:
            component.unsupported(instance, 'Mystery Swap hero assignment', instance.graph.node(26).cls,
                                  instance.graph.node(26))
        self.pending_mystery_swaps[component.entity] = hero.guid

    @property
    def client_ready(self):
        return self.mode_projection is not None and not self.closed and self.error is None

    def advance(self, frame):
        if not super().advance(frame, allow_skipped_frames=True):
            return False
        for controller, hero_id in tuple(self.pending_mystery_swaps.items()):
            binding = self.player_bindings.get(controller)
            if binding is None:
                del self.pending_mystery_swaps[controller]
                continue
            player = binding.player
            if player.gone or player.hero.guid == hero_id or self.match.switch_hero(player, content.heroes()[hero_id]):
                del self.pending_mystery_swaps[controller]
        self._apply_spawn_facing(frame)
        for actor, _ in tuple(self.combatants.values()):
            component = self.combatant_component(actor)
            if component is not None:
                if actor.hero in junkenstein_ai.BOSS_PRIMARY or actor.hero in (junkenstein_ai.MERCY, junkenstein_ai.SYMMETRA):
                    nodes.report_active_volleys(component)
                if component.shots:
                    self.match.combat.consume_scene_boss_shots(actor, component, frame)
        if not self.replicate_mode:
            return True

        self._publish_new_spawned_entities()
        owner = self.units[runtime.GAME_MODE_ENTITY].component
        if self.mode_projection is None:
            projection = observers.SceneBody(owner)
            if not any(item.graph.index == pve.MODE_UI for item in owner.instances.values()):
                raise ValueError('Junkenstein mode has not started its authored common UI')
            observers.full_frame(projection, projection.latest_live)  # validate before opening the entrance
            gate = 0x80000000 | content.JUNKENSTEIN_GATE
            if gate in self.units and self.world.entities[gate].definition == 0x04000000000016F6:
                gate_projection = observers.SceneBody(self.units[gate].component)
                self.body_projections[gate] = gate_projection, partial(self._publish_spawned_script, gate)
            self.mode_projection = projection

        clients = [player.client for player in self.match.players
                   if player.spawned and not player.gone and player.client is not None]
        active = set(clients)
        self.mode_views = {
            client: baseline for client, baseline in self.mode_views.items() if client in active
        }
        self.mode_actions = {
            client: actions for client, actions in self.mode_actions.items() if client in active
        }
        writable = [client for client in clients if client.scene_mode_writable]
        if not writable:
            return True
        projection = self.mode_projection
        current = observers.snapshot(projection)
        full = None
        for client in writable:
            baseline = self.mode_views.get(client) if client.player.mode_script.delivered else None
            if baseline is None:
                if full is None:
                    full = observers.full_frame(projection, current)
                client.queue_scene_mode_frame(full, full=True)
            else:
                pending = self.mode_actions.get(client)
                while pending and pending[0][0] not in current.instances:
                    pending.popleft()
                action = (client.player.mode_script.last + 1, *pending[0]) if pending else None
                delta = observers.acknowledged_delta(projection, baseline, current, action=action)
                if delta is None:
                    continue
                client.queue_scene_mode_frame(delta, full=False)
                if action is not None:
                    pending.popleft()
            self.mode_views[client] = current
        return True

    def _apply_spawn_facing(self, frame):
        for entity, (actor, _) in self.combatants.items():
            component = self.combatant_component(actor)
            if component is None:
                continue
            binding = self.world.entities[entity]
            if binding.facing_writer is None:
                continue
            locks = component.state_registries.get('STU_9935FBC8', ())
            if locks:
                binding.aim_writer(*locks[-1].aim, frame)
                continue
            winner = None
            for state, record in self.world.ai_facing_records[entity].items():
                if state.active and (winner is None or record.priority > winner.priority
                                     or (record.priority == winner.priority and record.prefer_later)):
                    winner = record
            if winner is not None:
                binding.facing_writer(winner.direction, frame)

    def bind_match(self, match, configure_component):
        self.match = match
        self.component_configurer = configure_component
        self.world.roadhog_hook_cast = self._roadhog_hook_cast
        self.world.symmetra_beam_cast = self._symmetra_beam_cast
        self.world.symmetra_beam_hit = self._symmetra_beam_hit
        self.world.shield_recharge = self._recharge_enemy_shields
        self.world.witch_staff_heal = self._witch_staff_heal
        self.world.witch_staff_targets = self._witch_staff_targets
        self.world.junkenstein_body_name = self._body_name
        self.world.roadhog_hook_targets = self._roadhog_hook_targets
        self.world.roadhog_hook_hit = self._roadhog_hook_hit
        self.world.roadhog_hook_target = self._roadhog_hook_target
        if self.world.contact_effect is None:
            self.world.contact_effect = self._project_enemy_contact
        if self.world.effect_entity_action is None:
            self.world.effect_entity_action = self._project_mode_audio
        previous_filter = self.world.array_filter_inputs
        previous_route = self.world.mission_message_route
        gate = 0x80000000 | content.JUNKENSTEIN_GATE
        gate_binding = self.world.entities.get(gate)
        if gate_binding is not None and gate_binding.definition == 0x04000000000016F6:
            def set_gate_team(entity, team):
                if entity != gate or self.world.entities.get(gate) is not gate_binding:
                    return NotImplemented
                self.gate_team = team
                return True

            if self.world.script_team_setter is None:
                self.world.script_team_setter = set_gate_team

        def filters(entity):
            if (barrier := self.barriers.get(entity)) is not None:
                return True, (TEAM_BIT << barrier.team) | 1 | (0x10 if barrier.health == 0 else 0)
            if (entity == gate and self.world.entities.get(gate) is gate_binding
                    and self.gate_team is not None):
                return True, TEAM_BIT << self.gate_team if self.gate_team >= 0 else 0
            binding = self.player_bindings.get(entity)
            if binding is not None and not binding.player.gone:
                return True, PLAYER_FILTER | binding.player.team_bit
            for binding in self.player_bindings.values():
                player = binding.player
                if binding.body == entity and player.body == entity and player.has_body and not player.gone:
                    vitals = match.combat.vitals.get(entity)
                    return True, player.team_bit | 1 | (0x10 if vitals is not None and vitals.dead else 0)
            inputs = previous_filter(entity) if callable(previous_filter) else NotImplemented
            if inputs is NotImplemented and entity == runtime.GAME_MODE_ENTITY:
                mode_owner = self.world.entities.get(entity)
                if mode_owner is not None and mode_owner.definition == MODE_ENTITY:
                    # 0CBD collects the encounter narrator alongside session candidates.
                    return True, MODE_NARRATOR_FILTER
            return inputs

        def route(index, node, message):
            if previous_route is not None:
                targets = previous_route(index, node, message)
                if targets is not NotImplemented:
                    return targets
            live = (binding for binding in self.player_bindings.values() if not binding.player.gone)
            if (message == 0x02400000000006DE
                    and ((index, node) == (0x0C8C, 125) or (index == 0x0CE0 and node in (52, 53, 54)))):
                return tuple(expr.Entity(binding.body) for binding in live if binding.body is not None)
            if (index, node, message) == (0x0CE0, 2, 0x02400000000006AC):
                return tuple(expr.Entity(binding.body) for binding in live if binding.body is not None)
            if (message == 0x02400000000014D4
                    and ((index == 0x0CB3 and 181 <= node <= 188) or (index, node) == (0x0CC0, 38))):
                return tuple(expr.Entity(binding.controller.entity) for binding in live)
            return NotImplemented

        self.world.array_filter_inputs = filters
        self.world.mission_message_route = route
        self.world.junkenstein_voice_recipients = self._voice_recipients

    def _project_mode_audio(self, instance, node):
        if (instance.graph.index, node.pos) == (0x0C8C, 227):
            effect = node.field('m_AC2333F7') or {}
            if (node.field('m_7A46FA0F') != 0 or effect.get('$') != 'STUConfigVarEffect'
                    or effect.get('m_resourceKey') is not None
                    or graph.guid(effect.get('m_effect')) != 0x0300000000002035):
                return NotImplemented
            # 2035.00D contains a single sound, 443A.02C, with no child effects.
            self._queue_mode_action(instance, node)
            return True
        if (instance.graph.index != 0x0CB3 or node.pos not in (126, 127, 128, 129, 130, 165)
                or node.field('m_7A46FA0F') != 0):
            return NotImplemented
        effect = node.field('m_AC2333F7') or {}
        operands = effect.get('m_configVars') or ()
        if (effect.get('$') != 'STU_919FD47C' or len(operands) != 2 or operands[0].get('$') != 'STU_7FC33EF6'
                or (effect.get('m_expression') or {}).get('m_opcodes') != [14, 0, 4, 6, 14, 1, 0, 4, 6, 0, 0]
                or operands[1].get('$') != 'STUConfigVarEffect'
                or operands[1].get('m_resourceKey') is not None
                or graph.guid(operands[1].get('m_effect')) != 0x0300000000001B29):
            return NotImplemented
        # The remote action evaluates the authored presentation guard on each client.
        self._queue_mode_action(instance, node)
        return True

    def _project_enemy_contact(self, state, entity, contact_set, targets):
        instance = state.instance
        actor = self.combatants.get(entity)
        if actor is None:
            return NotImplemented
        explosion = ((instance.graph.index, state.node.pos) == (0x0C89, 49)
                     and contact_set == 0x0120000000000015)
        hook = (instance.graph.index == 0x01C8 and state.node.pos in (28, 78, 89, 97)
                and contact_set == 0x012000000000017D and actor[0].hero == junkenstein_ai.ROADHOG)
        if not (explosion or hook):
            return NotImplemented
        projection = self.combatant_projection(actor[0])
        if (projection is None or projection.component is not instance.component
                or not state.node.is_networked or state.node.remote < 0):
            return NotImplemented
        # The body observer frame carries the contact state and its authored target array.
        slot = instance.find_var(runtime.INSTANCE, 375, create=False)
        if slot is None or slot.value() != targets:
            return NotImplemented
        return True

    def configure_component(self, component):
        if self.component_configurer is not None:
            self.component_configurer(self, component)
        if self.world.message_state_context(component.entity, component=component) is not NotImplemented:
            return

        def priority(state, resource):
            if not resource:
                return None
            if resource >> 48 == 0x0C40:
                authored = expr.STACK_PRIORITIES.get(resource & 0xFFFFFFFFFFFF)
                if authored is not None:
                    value, prefer_later = authored
                    return message_state.Priority(value, bool(prefer_later))
            return NotImplemented

        sender = message_state.Policy(False, False, False)
        receiver = message_receiver.Policy(
            mode=False, instance_flag3=False, state_mode=False, local=False, world_a=False, world_b=False,
            authoritative=True, instance_flag19=False, skip_application=False, deferred_begin=False,
        )
        self.world.set_message_state_context(component.entity, message_state.Context(
            component, self.messages, policy=lambda _: sender, frame=lambda _: component.frame,
            priority=priority, receiver_policy=lambda _: receiver,
            receiver_generation=lambda _: component.frame + 1,
        ))

    def _roadhog_hook_cast(self, component, request, *, walls_only):
        registration = self.combatants.get(component.entity)
        if registration is None or self.combatant_component(registration[0]) is not component:
            return None
        actor = registration[0]
        if actor.hero != junkenstein_ai.ROADHOG:
            return None
        targets = () if walls_only else self.match.combat._enemies(actor)
        return self._combat_cast(request, targets)

    def _symmetra_beam_cast(self, instance, request):
        component = instance.component
        registration = self.combatants.get(component.entity)
        if registration is None or self.combatant_component(registration[0]) is not component:
            return None
        actor = registration[0]
        if actor.hero != 0x02E0000000000016:
            return None
        targets = (target for target in self.match.combat._enemies(actor)
                   if self.world._filter_entity(instance, target.entity, request.filter_config) is True)
        return self._combat_cast(request, targets)

    def _recharge_enemy_shields(self, state, elapsed_ms):
        component, node = state.instance.component, state.node
        registration = self.combatants.get(component.entity)
        if registration is None or self.combatant_component(registration[0]) is not component:
            return NotImplemented
        actor = registration[0]
        config = node.field('m_modifyHealth') or {}
        owner = expr.Entity(component.entity)
        if (actor.hero not in junkenstein_ai.BOSSES or node.field('m_DD792FF0') != 3
                or node.field('m_7B2B3F5C') != 1 or config.get('m_93CDFD2F') != 0
                or state.evaluate(node.field('m_95F1FB0B'), watch=False) != 1
                or state.evaluate(node.field('m_FA3ECC43'), watch=False) != owner
                or state.evaluate(config.get('m_724CBA57'), watch=False) != owner):
            return NotImplemented
        fight = self.match.combat
        vitals = fight.vitals[actor.entity]
        if not elapsed_ms or vitals.dead:
            return True
        amount = state.evaluate(config.get('m_amount'), watch=False)
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
            return NotImplemented
        pool = combat.SHIELDS_POOL
        current = vitals.now[pool]
        restored = expr.f32(min(vitals.most[pool], current + amount * elapsed_ms / 1000))
        if restored > current:
            vitals.now[pool] = restored
            vitals.changed.add(pool)
            fight._sync_body_health(actor, vitals)
        return True

    def _body_name(self, entity):
        registration = self.combatants.get(entity)
        if registration is not None:
            return registration[0].name
        controller = self.world.primary_possessor(entity)
        binding = self.player_bindings.get(controller)
        if binding is not None and binding.body == entity and not binding.player.gone:
            return binding.player.name
        return None

    def _witch_staff_targets(self, state):
        instance, node = state.instance, state.node
        caster = instance.component.entity
        source = self.combatants.get(caster)
        if source is None or source[0].hero != junkenstein_ai.MERCY:
            return NotImplemented
        world = self.world
        controller = world.linked_controllers.get(caster)
        entity = world._lookup_ai_linked_target(controller, 1)
        if entity is None:
            return ()
        # 006B/7 maintains the selected target from /6, which is slot 1 on the NPC.
        # The unconditional ally filter on /121 carries the same live-body masks.
        config = instance.graph.node(121 if node.pos == 7 else node.pos).field('m_filter')
        if world._filter_entity(instance, entity, config) is not True:
            return ()
        radius = state.evaluate(node.field('m_radius'), watch=False)
        if type(radius) not in (int, float) or not math.isfinite(radius) or radius <= 0:
            return ()
        fight = self.match.combat
        origin = source[0].position
        target = next((row for row in fight._targets() if row.entity == entity), None)
        if (target is not None and math.dist(origin, target.feet) <= radius
                and npc_navigation.visible_aim(fight._collision(), origin, target) is not None):
            return (entity,)
        return ()

    def _witch_staff_heal(self, state, elapsed_ms):
        instance = state.instance
        component, node = instance.component, state.node
        source = self.combatants.get(instance.source_entity())
        recipient = self.combatants.get(component.entity)
        if source is None or recipient is None:
            return True
        caster, target = source[0], recipient[0]
        if (caster.hero != junkenstein_ai.MERCY or caster is target or caster.team != target.team
                or self.combatant_component(caster) is None or self.combatant_component(target) is not component):
            return NotImplemented
        if not elapsed_ms or caster.dead or target.dead:
            return True
        config = node.field('m_modifyHealth') or {}
        amount = state.evaluate(config.get('m_amount'), watch=False)
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
            return NotImplemented
        fight = self.match.combat
        vitals = fight.vitals[target.entity]
        if vitals.heal(expr.f32(amount * elapsed_ms / 1000)) > 0:
            fight._sync_body_health(target, vitals)
        return True

    def _symmetra_beam_hit(self, state, elapsed_ms):
        component = state.instance.component
        registration = self.combatants.get(component.entity)
        if registration is None or self.combatant_component(registration[0]) is not component:
            return NotImplemented
        actor = registration[0]
        node = state.node
        if (actor.hero != 0x02E0000000000016 or node.field('m_DD792FF0') != 3
                or state.evaluate(node.field('m_95F1FB0B'), watch=False) != 1):
            return NotImplemented
        if not elapsed_ms or actor.dead:
            return True
        victim = state.evaluate(node.field('m_FA3ECC43'), watch=False)
        if not isinstance(victim, expr.Entity):
            return True
        fight = self.match.combat
        target = next((target for target in fight._enemies(actor) if target.entity == victim.id), None)
        if target is not None:
            hit = combat.hit_def(state, {'m_modifyHealth': node.field('m_modifyHealth')})
            damage = expr.f32(hit.amount * (elapsed_ms / 1000.0))
            fight._damage(actor, target, damage, False, hit.loadout, component.frame,
                          world_tick=component.frame, hit_tags=hit.tags)
        return True

    def _combat_cast(self, request, targets):
        collision = self.match.collision_world
        if collision is None:
            return None
        origin, direction, length = tuple(request.origin), tuple(request.direction), request.length
        wall = collision.cast_ray(origin, direction, length)
        closest = length if wall is None else wall.distance
        victim = segment = None
        for target in targets:
            if target.entity in request.ignored_entities:
                continue
            a, b = target.segment()
            distance = combat.ray_capsule(origin, direction, a, b, target.radius + request.radius)
            if distance is not None and distance <= length and (distance < closest or (
                    distance == closest and wall is None and victim is None)):
                closest, victim, segment = distance, target, (a, b)
        if victim is not None:
            point = expr.Vec3(*combat._along(origin, direction, closest))
            a, b = segment
            normal = (point.x - a[0], point.y - max(a[1], min(b[1], point.y)), point.z - a[2])
            magnitude = sum(axis * axis for axis in normal) ** 0.5
            normal = tuple(axis / magnitude for axis in normal) if magnitude else tuple(-v for v in direction)
            return runtime.RaycastResult(True, expr.Entity(victim.entity), point, expr.Vec3(*normal))
        if wall is not None:
            return runtime.RaycastResult(True, None, expr.Vec3(*wall.point), expr.Vec3(*wall.normal))
        return runtime.RaycastResult(False, None, None, None)

    def _roadhog_hook_targets(self, state):
        component = state.instance.component
        registration = self.combatants.get(component.entity)
        if registration is None or self.combatant_component(registration[0]) is not component:
            return NotImplemented
        actor = registration[0]
        collision = self.match.collision_world
        tip = state.evaluate(state.node.field('m_05BE62DD'), watch=False)
        radius = state.evaluate(state.node.field('m_radius'), watch=False)
        if actor.hero != junkenstein_ai.ROADHOG or collision is None or not isinstance(tip, expr.Vec3):
            return NotImplemented
        if type(radius) not in (int, float) or not math.isfinite(radius) or radius <= 0:
            return NotImplemented
        origin = combat._along(actor.position, (0, 1, 0), combat.EYE)
        closest = None
        for target in self.match.combat._enemies(actor):
            component.consume_work('Junkenstein hook target')
            if target.team != 0 or target.entity not in self.world.entities:
                continue
            if not junkenstein_ai.hook_target_allowed(self.world, target.entity):
                continue
            a, b = target.segment()
            distance = combat.segment_distance(tuple(tip), a, b)
            candidate = (distance, target.entity)
            if (distance <= radius + target.radius and (closest is None or candidate < closest)
                    and combat.capsule_visible(collision, origin, a, b, target.radius)):
                closest = candidate
        return () if closest is None else (closest[1],)

    def _roadhog_hook_hit(self, state):
        component = state.instance.component
        registration = self.combatants.get(component.entity)
        if registration is None or self.combatant_component(registration[0]) is not component:
            return NotImplemented
        actor = registration[0]
        if actor.hero != junkenstein_ai.ROADHOG:
            return NotImplemented
        victim = state.evaluate(state.node.field('m_FA3ECC43'), watch=False)
        if not isinstance(victim, expr.Entity):
            return NotImplemented
        fight = self.match.combat
        target = next((target for target in fight._enemies(actor) if target.entity == victim.id), None)
        if target is None:
            return True
        hit = combat.hit_def(state, {'m_modifyHealth': state.node.field('m_modifyHealth')})
        fight._damage(actor, target, hit.amount, False, hit.loadout, component.frame,
                      world_tick=component.frame, hit_tags=hit.tags)
        return True

    def _roadhog_hook_target(self, entity):
        if entity in self.world.entities:
            return next((target for target in self.match.combat._targets() if target.entity == entity), None)
        return None

    def _bound_player_body(self, player):
        binding = self.player_bindings.get(player.entity)
        if binding is None or binding.player is not player or binding.body != player.body:
            return None
        return self.world.script_components.get(player.body)

    def player_aim_lock(self, player):
        component = self._bound_player_body(player)
        if component is not None:
            locks = component.state_registries.get('STU_9935FBC8')
            if locks:
                return locks[-1].aim
        return None

    def hook_victim(self, player):
        component = self._bound_player_body(player)
        if component is not None:
            states = component.state_registries.get('STU_4773DA56')
            if states:
                return states[-1]
        return None

    def step_hook_victim(self, player, command, seconds, tick):
        registration = self.hook_victim(player)
        if registration is None:
            return False
        instance = registration.instance
        move = instance.states.get(0)
        neutral = replace(command, forward=0, right=0, buttons=0, action=0)
        if move is not None and move.active and not any(
            event.state is move and event.op in (runtime.FINISH, runtime.ABORT) for event in instance.queue
        ):
            entity = expr.to_entity(move.evaluate(move.node.field('m_destinationEntity'), watch=False))
            body = self.world.entities.get(entity)
            feet = self.world.position(entity)
            if body is not None and feet is not None and body.rotation_reader is not None:
                offset = move.evaluate(move.node.field('m_865F3D01'), watch=False)
                speed = move.evaluate(move.node.field('m_speed'), watch=False)
                if (isinstance(offset, expr.Vec3) and all(map(math.isfinite, offset))
                        and type(speed) in (float, int) and math.isfinite(speed) and speed > 0):
                    destination = props.place(tuple(offset), tuple(feet), (1, 1, 1), body.rotation_reader())
                    outcome = player.mover.step_pose(neutral, seconds, tick, move_to=(destination, speed))
                    if outcome is not None:
                        move.request(runtime.FINISH if outcome else runtime.ABORT)
                    player._advance_body_frame(neutral, posed=False)
                    return True
            move.request(runtime.ABORT)
        player.mover.step_pose(neutral, seconds, tick)
        player._advance_body_frame(neutral, posed=False)
        return True

    def _retire_interrupted_hooks(self, player, component):
        dead = self.match.combat.is_dead(player)
        for instance in tuple(component.instances.values()):
            if instance.graph.index != 0x01C9:
                continue
            slot = instance.find_var(runtime.INSTANCE, 40, create=False)
            caster = slot.value() if slot is not None else None
            source = self.world.script_components.get(caster.id) if isinstance(caster, expr.Entity) else None
            caster_gone = isinstance(caster, expr.Entity) and (source is None or source.owner_dead)
            if dead or caster_gone:
                if dead and source is not None and not caster_gone:
                    runtime.send_message(source, 0x02400000000001B3, {}, component.entity)
                move = instance.states.get(0)
                driving = move is not None and move.active and player.mover.driven
                component.destroy(instance)
                if driving:
                    player.mover.velocity = (0.0, 0.0, 0.0)
                    player.mover.driven = False

    def _voice_recipients(self, instance):
        """Supply live defender bodies for authored encounter voice messages."""
        recipients = []
        for binding in self.player_bindings.values():
            instance.component.consume_work('Junkenstein voice recipient')
            player, body = binding.player, binding.body
            component = self.world.script_components.get(body)
            if (not player.gone and player.team == 0 and player.has_body and body == player.body
                    and component is not None and component.fault is None
                    and not self.match.combat.is_dead(player)):
                recipients.append(expr.Entity(body))
        return tuple(recipients)

    def _player_component(self, entity):
        component = self._start_live_component(entity, {'$': 'STUStatescriptComponent'})
        self.configure_component(component)
        return component

    def _player_graph(self, component, index):
        model = self.graph_loader(index)
        if model is None:
            raise ValueError(f'Junkenstein player graph {index:04X} is missing')
        instance = component.create(model, init=False)
        instance.initialized = True
        return instance

    def mount_player_intro_receiver(self, script):
        instance = self._player_graph(script.component, TEAM_BODY_GRAPH)
        # The boss body's 0CE0/21 sends 06AB to defenders on clients.
        # Mount the authored camera receiver in the local player's owner VM.
        instance.node_state(instance.graph.node(33)).request(runtime.BEGIN)
        script.component.run_now()

    def before_units(self):
        world = self.world
        for entity, binding in tuple(self.player_bindings.items()):
            if binding.player.gone:
                if binding.body is not None:
                    self.retire_entity(binding.body)
                self.retire_entity(entity)
                del self.player_bindings[entity]
        for player in self.match.players:
            if player.gone or player.team != 0:
                continue
            binding = self.player_bindings.get(player.entity)
            if binding is None:
                world.bind(player.entity)
                world.set_possessor(player.entity, True)
                world.set_entity_query_context(player.entity)
                controller = self._player_component(player.entity)
                wave = self._player_graph(controller, TEAM_CONTROLLER_GRAPH)
                wave.node_state(wave.graph.node(WAVE_RECEIVER_NODE)).request(runtime.BEGIN)
                binding = self.player_bindings[player.entity] = PlayerBinding(player, controller)
                if self.match.card == content.JUNKENSTEIN_MYSTERY_SWAP:
                    owner = controller_heroes.Controller(player.entity)
                    world.set_controller_hero_context(player.entity, controller_heroes.Context(
                        owner.entity, True, True,
                        lookup=lambda entity, owner=owner: owner if entity == owner.entity else None,
                        catalog=lambda: MYSTERY_SWAP_HEROES,
                        available=lambda hero, _owner: hero in content.heroes(),
                        watch_environment=None,
                    ))
                    world.set_subgraph_scopes(player.entity, scopes.ForwardHost(controller))
                    wave.node_state(wave.graph.node(15)).request(runtime.BEGIN)
            if not player.has_body or binding.body == player.body:
                continue
            world.bind(player.body, definition=player.hero.body, hero=player.hero.guid,
                       target_tags=graph.junkenstein_player_tags(player.hero.body),
                       position_reader=lambda player=player: tuple(player.mover.position),
                       velocity=lambda player=player: tuple(player.mover.velocity),
                       health=lambda player=player, body=player.body: self.match.combat.body_vitals(body, player).total,
                       scale_reader=lambda: 1.0,
                       movement_reader=player.mover.snapshot,
                       rotation_reader=lambda player=player: _rotation(player.mover.yaw))
            world.set_entity_query_context(player.body)
            body = self._player_component(player.body)
            if world.subgraph_scopes(body.entity) is NotImplemented:
                world.set_subgraph_scopes(body.entity, scopes.ForwardHost(body))
            if world.property_modifiers(body.entity) is NotImplemented:
                world.set_property_modifiers(body.entity, modifiers.NumericModifiers(self.frame))
            if world.data_flow_context(body.entity) is NotImplemented:
                world.bind_data_flow_providers(body.entity)
            junkenstein_ai.bind_body_team(self, self.match, body.entity)
            self._player_graph(body, TEAM_BODY_GRAPH)
            world.set_primary_possessor(player.body, player.entity)
            world.set_current_possession(player.entity, player.body)
            world.set_linked_controller(player.body, player.entity)
            if binding.body is not None:
                self.retire_entity(binding.body)
            binding.body = player.body
            if binding.sender is None:
                binding.sender = self._player_graph(binding.controller, READY_GRAPH)
                binding.sender.node_state(binding.sender.graph.node(READY_NODE)).request(runtime.BEGIN)
            binding.sender.write((runtime.INSTANCE, SELECTED_HERO), expr.Asset(player.hero.guid))

        for binding in self.player_bindings.values():
            backend = world.property_modifiers(binding.body)
            if type(backend) is modifiers.NumericModifiers:
                backend.source_frame = self.frame
                backend.records[:] = [record for record in backend.records if record.end > self.frame]
            component = world.script_components.get(binding.body)
            if component is not None:
                self._retire_interrupted_hooks(binding.player, component)

        # Poll previously registered voice states before ordinary per-component updates.
        for context in tuple(world.voice_contexts.values()):
            if context is not None:
                context.poll()
        for actor, _ in self.combatants.values():
            backend = world.property_modifiers(actor.entity)
            if type(backend) is modifiers.NumericModifiers:
                backend.source_frame = self.frame
                if backend.records:
                    backend.records[:] = [record for record in backend.records if record.end > self.frame]
            if actor.dead and actor.hero in (junkenstein_ai.ZOMNIC, junkenstein_ai.ZOMBARDIER):
                # 0C89 has timed removal paths for special actors and self-destruction,
                # but no listener for an ordinary kill. Give those corpses a bounded lifetime.
                vitals = self.match.combat.vitals[actor.entity]
                if (self.frame - vitals.death_tick) * self.frame_ms >= COMMON_CORPSE_GRACE_MS:
                    component = self.combatant_component(actor)
                    world.queue_enemy_retirement(component, actor.entity)
            if actor.hero in junkenstein_ai.BOSS_PRIMARY:
                component = self.combatant_component(actor)
                if component is not None:
                    junkenstein_ai.update_boss_combat(self, actor, component)
            elif actor.hero == junkenstein_ai.MERCY:
                component = self.combatant_component(actor)
                if component is not None:
                    junkenstein_ai.update_witch_combat(self, actor, component)
            elif actor.hero == junkenstein_ai.SYMMETRA:
                component = self.combatant_component(actor)
                if component is not None:
                    junkenstein_ai.update_symmetra_combat(self, actor, component)

    def close(self):
        super().close()
        self.messages.retire()
        self.barriers.clear()
        self.ghost = None
        self.player_bindings.clear()
        self.pending_mystery_swaps.clear()
        self.mode_views.clear()
        self.mode_actions.clear()
        self.mode_projection = None


def runtime_factory(game_map):
    if game_map.map_guid != content.JUNKENSTEIN_MAP or game_map.mode_guid not in content.JUNKENSTEIN_MODES:
        return None
    directory = DATA_DIR / 'junkenstein'
    if not directory.is_dir():
        return None
    return partial(_runtime_host, directory=directory)


def _runtime_host(match, *, directory):
    map_data = json.loads((directory / 'junkenstein-map-world.json').read_text(encoding='utf-8'))
    definitions = tuple(npc.load_catalog(directory / 'junkenstein-npcs.json').values())
    return create_host(match, map_data=map_data, definitions=definitions,
                       conversation_path=directory / 'junkenstein-conversations-104319.json')


def create_host(match, *, map_data, definitions, configure_world=None, configure_component=None,
                conversation_path=None):
    """Mount the authored encounter with optional overrides for isolated replays."""
    game_map = match.game_map
    if (game_map.map_guid != content.JUNKENSTEIN_MAP or game_map.mode_guid not in content.JUNKENSTEIN_MODES
            or not isinstance(map_data, dict) or graph.guid(map_data.get('map')) != game_map.map_guid):
        raise ValueError('Junkenstein scene requires the matching encounter map and mode')
    callbacks = (configure_world, configure_component)
    if any(callback is not None and not callable(callback) for callback in callbacks):
        raise ValueError('Junkenstein scene overrides must be callable')

    mode = graph.mode_scripts(game_map.mode_guid)
    mode_definition = graph.entity_script_component(MODE_ENTITY, junkenstein=True)
    if (mode is None or not mode.get('pve') or not isinstance(mode_definition, dict)
            or mode_definition.get('m_B634821A')):
        raise ValueError('Junkenstein scene requires the prepared mode entity and mode scripts')
    team = mode.get('teams', {}).get('0', {})
    team_scripts = tuple(graph.guid(team.get(key)) for key in ('controller', 'body'))
    if team_scripts != (0x0580000000000CA2, 0x0580000000000CA5):
        raise ValueError('Junkenstein scene requires the authored player team scripts')

    def acquire(guid):
        definition = graph.graph(guid & 0xFFFFFFFFFFFF)
        return play_queue.Load('ready', definition) if definition is not None else None

    roots = [mode.get(key) for key in ('shared_graph', 'graph')]
    for root in roots:
        guid = graph.guid(root)
        if guid is None or guid >> 48 != graph.GRAPH_TYPE or acquire(guid) is None:
            raise ValueError('Junkenstein mode root is missing or unavailable')
    mode_definition = {**mode_definition, 'm_B634821A': [
        {'$': 'STUStatescriptGraphWithOverrides', 'm_graph': root} for root in roots
    ]}

    entry = content._map_entries()[game_map.map_guid]
    host = JunkensteinSceneHost.from_map(
        map_data, entity_start=entry['first_entity'], server_placeables=game_map.placeables,
        kept_identifiers=props.mode_identifiers(game_map.mode_guid), mode=game_map.mode_guid,
        graph_loader=graph.graph,
    )
    world = host.world
    host.replicate_mode = mode.get('scripted_loading') is False
    try:
        world.bind(runtime.GAME_MODE_ENTITY, definition=MODE_ENTITY)
        component = host.add(runtime.GAME_MODE_ENTITY, mode_definition,
                             values=graph.mode_variables(game_map.mode_guid))
        world.set_subgraph_scopes(component.entity, scopes.ForwardHost(component))
        world.set_possessions()
        world.outline_context_available = False
        world.local_combat_context_available = False
        world.tips_context_available = False
        world.ux_presenter_available = False
        if configure_world is not None:
            configure_world(match, host)
        if world.property_modifiers(component.entity) is NotImplemented:
            # The authored 16B2 mode owner has no body property component.
            world.set_property_modifiers(component.entity, None)
        if world.voice_set(component.entity) is None:
            world.bind_voice_set(component.entity, MODE_VOICE_SET)
        if conversation_path is not None and world.voice_context(component.entity) is NotImplemented:
            conversations = junkenstein_dialogue.load(conversation_path)
            playback = junkenstein_dialogue.Playback(match, conversations, host.seed)
            world.set_voice_context(component.entity, playback.context(component))
        if world.voice_category_callback is NotImplemented:
            world.voice_category_callback = None  # This host has no native voice-category manager.
        if world.engine_settings is None:
            # Typed values consumed by 0CBB, captured from build 104319.
            world.engine_settings = {0x0FE1E03E: False, 0xE1C1E402: 128, 0x6CE29BAB: 1.0}
        if world.for_code_transport is None:
            world.for_code_transport = False  # Captured 262C010 slot +8.
        if world.mode_score_thresholds is None:
            world.mode_score_thresholds = {world.mode: (SCORE_THRESHOLDS[world.mode], dict(SCORE_LIMITS))}
        host.bind_match(match, configure_component)
        for unit in host.units.values():
            host.configure_component(unit.component)
        gate = 0x80000000 | content.JUNKENSTEIN_GATE
        gate_unit = host.units.get(gate)
        is_encounter_gate = gate_unit is not None and world.entities[gate].definition == 0x04000000000016F6
        if is_encounter_gate and world.voice_context(gate) is NotImplemented:
            junkenstein_dialogue.bind_gate_voice(
                match, gate_unit.component, junkenstein_ai.ENCOUNTER_VOICE_SET)
        if is_encounter_gate and world.entity_control_context(gate) is NotImplemented:
            model = junkenstein_ai.EncounterModelControl((0x0CA7, 36))
            world.set_entity_control_context(gate, entity_control.Context(
                gate_unit.component, False, within_distance=model.within_distance,
                write_entity=model.write, model_ready=lambda: True,
                write_model=model.write_phase_flag, route=None, extra_test=None,
            ))
        host.configure_play_requests(acquire)
        junkenstein_ai.install_enemy_creator(match, host, definitions, host.configure_component)
    except Exception:
        host.close()
        raise
    return host
