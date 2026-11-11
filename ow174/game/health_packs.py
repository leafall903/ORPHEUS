"""Host placed 104319 health-pack script components in the server match."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game.script import expr, graph, play, runtime

TAG = 0x0D6000000000029C
REFILL_SCALE_SLOT = 9522
REFILL_SCALE_KEY = 1
STARTUP = {0x0400000000000FC2: 0x216, 0x0400000000000FC3: 0x217}
TOUCH = 0x0240000000000643
TOUCH_RADIUS = 0.65


@dataclass
class HealthPack:
    entity: int
    definition: int
    position: tuple
    tags: tuple
    component: runtime.Component


class PackWorld(runtime.World):
    def __init__(self, match, entity):
        super().__init__(mode=match.game_map.mode_guid)
        self.match = match
        self.entity = entity

    def play_body_script(self, instance, node):
        if instance.graph.index == 0x0B32:
            from ow174.game.script.driver import play_sombra_body_script

            play_sombra_body_script(self, instance, node)

    def entity_var(self, entity, var):
        if (entity, var) == (runtime.GAME_MODE_ENTITY, REFILL_SCALE_SLOT):
            return REFILL_SCALE_KEY
        player = next((p for p in self.match.players if p.has_body and p.body == entity), None)
        if player is not None and player.body_script is not None:
            variable = player.body_script.component.entity_var(var, create=False)
            return variable.value() if variable is not None else None
        return super().entity_var(entity, var)

    def body_modify_health(self, state):
        instance = state.instance
        if (instance.graph.index, state.node.pos) != (0x0046, 0):
            return
        entity = expr.to_entity(state.evaluate(state.node.field("m_FA3ECC43"), watch=False))
        amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
        fight = self.match.combat
        target = next((target for target in fight._targets() if target.entity == entity), None)
        if target is not None and target.owner in self.match.players:
            fight.heal_target(target, amount)
        state.request_finish(0)

    def ruleset(self, key):
        if key == REFILL_SCALE_KEY:
            return 1.0  # reconstructed ordinary-mode refill scale
        return super().ruleset(key)

    def native(self, cfg, instance):
        cls = cfg.get("$")
        if instance.graph.index == 0x0046 and cls == "STU_A3DEF368":
            return expr.Entity(runtime.GAME_MODE_ENTITY)
        if instance.graph.index == 0x0B1F:
            if cls == "STU_E72F6348":
                entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
                return entity == self.entity or any(
                    p.has_body and p.body == entity for p in self.match.players
                )
            if cls == "STU_AC829876":
                filt = cfg.get("m_filter") or {}
                tag = expr.guid_of((filt.get("m_B486603E") or {}).get("m_7D7C86A1"))
                entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
                pack = self.match.combat.health_packs.get(entity)
                if pack is not None and tag:
                    return tag in pack.tags
        return super().native(cfg, instance)


def mount(match, tick):
    path = os.environ.get("ORPHEUS_HEALTH_PACK_DATA")
    if not path:
        return {}, None
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    if data["schema"] != "ow174.health-pack-placements.v1" or data["build"] != "1.74.0.0.104319":
        raise ValueError("unsupported health-pack placement data")
    records = data["maps"].get(f"0x{match.game_map.map_guid:016X}")
    if records is None:
        return {}, None
    graphs = {record["index"]: graph.Graph(record) for record in data["graphs"]}
    packs = {}
    for row in records["placements"]:
        definition = int(row["definition"], 16)
        if int(row["identifier"], 16) != 0:
            raise ValueError("health-pack mode placement filter is not mounted")
        scripts = row["script_instance"]["m_6D10093E"]
        expected = STARTUP[definition]
        if (len(scripts) != 1 or int(scripts[0]["m_graph"], 16) & 0xFFFFFFFFFFFF != expected
                or scripts[0]["m_1EB5A024"] is not None):
            raise ValueError("unsupported health-pack placement script")
        tags = tuple(int(tag, 16) for tag in data["definitions"][row["definition"]]["tags"])
        entity = 0x80000000 | row["placeable_index"]
        if entity in packs or TAG not in tags:
            raise ValueError("invalid health-pack placement identity")
        component = runtime.Component(entity, 16, PackWorld(match, entity))
        component.frame, component.now = tick, tick * component.frame_ms
        pack = HealthPack(entity, definition, tuple(row["position"]), tags, component)
        packs[entity] = pack
        init = component.create(graphs[expected])
        node = next(node for node in init.graph.nodes if node.cls == "STUStatescriptActionPlayScript")
        resource = play.resolve_resource(init, node)
        context = play.InvocationContext(entity, entity, entity)
        request = play.resolve_request(
            init, node, resource, context_provider=lambda context=context: context,
        )
        if resource.guid != 0x0580000000000046 or request is None or request.targets != (entity,):
            raise ValueError("unexpected health-pack startup request")
        submit(component, graphs[0x46], resource, request)
    return packs, hashlib.sha256(raw).hexdigest()


def submit(component, definition, resource, request):
    application = component.play_loaded_graph(
        definition, source=request.context.caller, params=request.params,
        overrides=resource.overrides, policy=request.policy, instigator=request.context.instigator,
    )
    component.run_now()
    return application


def apply_hack(pack, definition, resource, request):
    component = pack.component
    previous = tuple(i for i in component.instances.values() if i.graph.index == 0x0B1F)
    applied = submit(component, definition, resource, request)
    if applied is not None:
        for instance in previous:
            if instance is not applied and component.instances.get(instance.id) is instance:
                component.destroy(instance)
        component.run_now()


def advance(fight, tick):
    if not fight.health_packs:
        return
    players = [target for target in fight._targets() if target.owner in fight.match.players]
    for pack in fight.health_packs.values():
        component = pack.component
        if tick <= component.frame:
            continue
        component.run_frame(tick)
        pickup = next(instance for instance in component.instances.values() if instance.graph.index == 0x0046)
        if expr.to_float(pickup.find_var(graph.INSTANCE, 467).value()) < 1:
            continue
        for target in players:
            if not touches(fight, pack, target.feet):
                continue
            vitals = fight.vitals_of(target)
            if vitals.dead or vitals.healing_block_until > tick:
                continue
            if not any(vitals.now[kind] < maximum for kind, maximum in vitals.most.items()):
                continue
            if not available_to(fight, pack, target.owner):
                continue
            runtime.send_instance_message(pickup, TOUCH, {}, target.entity)
            component.run_now()
            if expr.to_float(pickup.find_var(graph.INSTANCE, 467).value()) < 1:
                break


def touches(fight, pack, feet):
    delta = tuple(body - centre for body, centre in zip(feet, pack.position, strict=True))
    distance = math.hypot(*delta)
    if distance > TOUCH_RADIUS:
        return False
    if distance == 0:
        return True
    from ow174.game.combat import ray_world

    x, y, z = pack.position
    direction = tuple(axis / distance for axis in delta)
    return ray_world(fight.match.collision_world, (x, y + 0.35, z), direction, distance) is None


def available_to(fight, pack, player):
    """Keep pickup and re-Hack admission within the controlling team."""
    owner_id = expr.to_entity(pack.component.entity_var(6396).value())
    if not owner_id or owner_id == player.body:
        return True
    return not fight.match.game_map.free_for_all and any(
        p.has_body and p.body == owner_id and p.team == player.team for p in fight.match.players
    )
