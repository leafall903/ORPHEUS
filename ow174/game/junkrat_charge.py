"""104319 charge graph with disclosed Match services and a world-clock passive host.

Ordinary configured combatants use reconstructed class1/component97 identities.
Owned tags come from external inputs;live additions/removals are explicit state.
Passive conditions/value are the original0B8B configs. Its owner-frame Wait is
replaced by a world deadline so input silence/catch-up cannot stop/double charge.
Damage012 uses actual pool loss and Combat loadout as its declared identifier;
native identifier/detail mapping and effective inherited tags remain unproved.
"""

import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import bots
from ow174.game.script import entity_filter, entity_hostility, entity_queries, entity_relation, entity_tags, expr, runtime
from ow174.game.script.entity_team import FilterBits

HERO = 0x02E0000000000065
MESSAGE = 0x0240000000000000
INPUT_ENV = 'ORPHEUS_CHARGE_ENTITY_INPUTS'
log = logging.getLogger('ow174.game')


def load_catalog(path):
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    if data.get('format_version') != 1 or data.get('build') != 104319:
        raise ValueError('charge inputs require104319 format1')
    rows = data.get('bodies')
    if not isinstance(rows, list) or not rows or data.get('count') != len(rows):
        raise ValueError('charge body input count mismatch')
    catalog = {}
    for row in rows:
        body = int(row['body'], 16)
        tags = row['owned_tags']
        if (body >> 48 != 0x400 or body in catalog or not isinstance(tags, list)
                or any(type(tag) is not int or not 0 <= tag <= 0xFFFFFFFF for tag in tags)
                or len(tags) != len(set(tags))
                or type(row.get('owned_tag_component_present')) is not bool):
            raise ValueError('invalid owned body tags')
        catalog[body] = tuple(tags)
    return catalog, hashlib.sha256(raw).hexdigest()


@dataclass
class ChargeActor:
    entity: int
    bits: FilterBits
    tags: entity_tags.Tags
    retired: bool = False


@dataclass
class ChargeFilterHost:
    binding: object
    retired: bool = False

    def retire(self):
        self.retired = True

    def lookup(self, entity):
        if self.retired or not self.binding.owned():
            return NotImplemented
        record = self.binding.record(entity)
        return (entity_filter.Target(entity, True, record[3].bits.bits)
                if record is not None else None)

    def source(self, instance):
        return self.lookup(self.binding.body)

    def relation(self, source, target):
        return entity_filter.RelationResult(source.entity == target.entity,
                                            self.binding.enemy(False, target, source))

    def tags(self, target):
        record = self.binding.record(target.entity)
        if record is None:
            return None
        tags = record[3].tags
        return entity_filter.TagSet(frozenset(tags.base), frozenset(tags.added), frozenset(tags.removed))

    def watch(self, *args):
        return None  # original binding declares polling, no dependency hub.

    def custom(self, target, cfg, instance):
        return 2  # original catalog declares the per-entity extension registry absent.

    def post(self, target, cfg, instance):
        return True  # original catalog declares the optional component4/list absent.


class ChargeSystem:
    def __init__(self, combat):
        self.combat = combat
        self.catalog = None
        self.catalog_hash = None
        self.bindings = {}
        self.tag_overrides = {}  # entity -> (added,removed);explicit server tag changes
        self.reserves = {}  # controller -> (hero,points) across a same-hero body handoff

    def attach(self, player):
        script = player.body_script
        if script is None or script.hero != HERO:
            return False
        saved = self.reserves.pop(player.entity, None)
        if saved is not None and saved[0] == script.hero:
            cost = expr.to_float(script.component.entity_var(338).value())
            script.set_ult_charge(min(cost, max(0.0, saved[1])))
            script.component.run_now()
        if self.catalog is None:
            self.catalog = {}
            path = os.environ.get(INPUT_ENV)
            try:
                if path is None:
                    raise ValueError(INPUT_ENV + ' is not configured')
                self.catalog, self.catalog_hash = load_catalog(path)
            except (OSError, ValueError, KeyError, TypeError) as error:
                log.warning('[game] Junkrat charge unavailable:%s', error)
        if player.hero.body not in self.catalog:
            return False
        binding = ChargeBinding(self, player, script)
        self.bindings[player.body] = binding
        # Only this passive gate is hosted by the world clock. Other graph
        # branches,including damage messages,continue in the original instance.
        state = binding.instance.state(binding.instance.graph.node(1).state)
        if state.active:
            state.request(runtime.ABORT)
            script.component.run_now()
        runtime.send_message(script.component, MESSAGE | 0x694, {}, player.body)
        script.component.run_now()
        binding.advance(self.combat.match.tick * 16)
        return True

    def context(self, body, name):
        binding = self.bindings.get(body)
        return getattr(binding, name) if binding is not None and binding.owned() else NotImplemented

    def prepare_replacement(self, player, hero):
        if player.hero.guid != HERO or hero.guid != HERO:
            self.reserves.pop(player.entity, None)
            return
        script = player.body_script
        if script is None or script.entity != player.body:
            return  # keep an already pending transfer while create/ACK is deferred
        var = script.component.entity_var(342, create=False)
        value = None if var is None else var.base if script.component.fault is not None else var.value()
        points = expr.to_float(value)
        self.reserves[player.entity] = (HERO, points if math.isfinite(points) else 0.0)

    def forget(self, player):
        for body, binding in list(self.bindings.items()):
            if binding.player is player:
                self.detach(player, body=body)
        self.reserves.pop(player.entity, None)
        self.tag_overrides.pop(player.body, None)

    def stop(self, player, *, body=None):
        entity = player.body if body is None else body
        binding = self.bindings.get(entity)
        if binding is not None:
            binding.enabled = False
            binding.next_ms = None

    def detach(self, player, *, body=None):
        entity = player.body if body is None else body
        self.stop(player, body=entity)
        binding = self.bindings.pop(entity, None)
        self.tag_overrides.pop(entity, None)
        if binding is not None:
            for context in (binding.hostility, binding.tags, binding.relation, binding.filtered):
                context.retire()
            for _owner, _generation, _definition, target, _tag_target in binding.targets.values():
                target.retired = True

    def set_tags(self, entity, *, added=(), removed=()):
        if type(entity) is not int or not 0 <= entity <= 0xFFFFFFFF:
            raise ValueError('tag owner must be a runtime uint32 entity')
        value = entity_tags.Tags((), tuple(added), tuple(removed))
        self.tag_overrides[entity] = (value.added, value.removed)

    def update(self):
        controllers = {p.entity for p in self.combat.match.players if not p.gone}
        for controller in set(self.reserves)-controllers:
            del self.reserves[controller]
        live = {p.body for p in self.combat.match.players if p.has_body and not p.gone}
        live.update(bot.entity for bot in self.combat.match.bots)
        for entity in set(self.tag_overrides)-live:
            del self.tag_overrides[entity]
        for body, binding in list(self.bindings.items()):
            if not binding.owned():
                self.detach(binding.player, body=body)
                continue
            for entity in set(binding.targets)-live:
                binding.targets.pop(entity)[3].retired = True
            binding.advance(self.combat.tick * 16)

    def damage(self, shooter, target, dealt, loadout, source=None):
        if getattr(getattr(shooter, "hero", None), "guid", None) != HERO:
            return
        if isinstance(shooter, bots.Bot) or target.construct is not None or target.owner is shooter:
            return
        if source is not None and source != shooter.body:
            return  # a projectile from a retired body cannot credit its replacement
        binding = self.bindings.get(shooter.body)
        if (binding is None or not binding.owned() or not binding.enabled
                or self.combat.junkrat.tire.busy(shooter.body)):
            return
        if not self.combat.match.game_map.free_for_all and shooter.team == target.team:
            return
        params = {0x13: float(dealt), 0x15: expr.Entity(target.entity),
                  0x3D: expr.Asset(loadout or 0), 0x624: ()}
        runtime.send_message(binding.script.component, MESSAGE | 0x12, params, shooter.body)
        binding.script.component.run_now()


class ChargeBinding:
    def __init__(self, system, player, script):
        self.system, self.player, self.script = system, player, script
        self.body = player.body
        self.instance = next(i for i in script.component.instances.values() if i.graph.index == 0x0B8B)
        self.enabled = True
        self.next_ms = None
        self.targets = {}
        self.hostility = entity_relation.Context(self.body, True, lookup=self.relation_actor,
            relate=self.relate, watch_context=None)
        self.tags = entity_tags.Context(self.body, True, lookup=self.tag_target, watch_tags=self.observe)
        self.relation_target = entity_queries.Target(self.body, possessor=entity_queries.Possession(player.entity))
        self.relation = entity_queries.Context(self.body, True, True, True,
            lookup=self.lookup_relation, watch_environment=None, watch_possessor=self.observe)
        self.filtered = ChargeFilterHost(self)

    def owned(self):
        player = self.player
        component = self.script.component
        return (not self.script.retired and not player.gone and player.has_body and player.body == self.body
                and player.body_script is self.script and player.match is self.system.combat.match
                and component.fault is None and not self.instance.stopped and not self.instance.teardown
                and component.instances.get(self.instance.id) is self.instance)

    def actor(self, entity):
        match = self.system.combat.match
        for player in match.players:
            if player.body == entity and player.has_body and not player.gone:
                return player, player.hero.body
        for bot in match.bots:
            if bot.entity == entity:
                return bot, bot.body
        return None

    def record(self, entity):
        found = self.actor(entity)
        if found is None or found[1] not in self.system.catalog:
            return None
        owner, definition = found
        generation = owner.body_script
        cached = self.targets.get(entity)
        if cached is None or cached[:3] != (owner, generation, definition):
            if cached is not None:
                cached[3].retired = True
            tags = entity_tags.Tags(self.system.catalog[definition])
            target = ChargeActor(entity, FilterBits(1), tags)
            cached = (owner, generation, definition, target, entity_tags.Target(entity, tags))
            self.targets[entity] = cached
        added, removed = self.system.tag_overrides.get(entity, ((), ()))
        tags = entity_tags.Tags(self.system.catalog[definition], added, removed)
        cached[3].tags = cached[4].tags = tags
        team = -1 if self.system.combat.match.game_map.free_for_all else owner.team
        cached[3].bits.bits = 1 | (1 << (23 + team) if 0 <= team <= 4 else 0)
        return cached

    def filter_target(self, entity):
        record = self.record(entity)
        return record[3] if record is not None else None

    def tag_target(self, entity):
        record = self.record(entity)
        return record[4] if record is not None else None

    def hostility_target(self, entity):
        found = self.actor(entity)
        if found is None:
            return None
        team = -1 if self.system.combat.match.game_map.free_for_all else found[0].team
        return entity_hostility.Target(entity, team, False, None)

    def relation_actor(self, entity):
        target = self.hostility_target(entity)
        return entity_relation.Target(entity, target) if target is not None else None

    def relate(self, target, source, flag, watcher):
        return entity_hostility.compare(target.payload if target is not None else None,
            source.payload if source is not None else None, flag, lookup=self.hostility_target,
            fallback_owner=self.no_owner, same_group=self.same_group)

    def lookup_relation(self, entity):
        return self.relation_target if entity == self.body and self.owned() else None

    def no_owner(self, actor):
        return None

    def same_group(self, first, second):
        return False

    def observe(self, *args):
        # Runtime's native polling plus world-clock condition evaluation supplies
        # dependency refresh;this server does not claim a native observer hub.
        return None

    def enemy(self, spectator, target, source):
        if spectator:
            return False  # this server's configured live combatants are not spectators
        return entity_hostility.compare(self.hostility_target(target.entity),
            self.hostility_target(source.entity), False, lookup=self.hostility_target,
            fallback_owner=self.no_owner, same_group=self.same_group)

    def advance(self, now_ms):
        instance = self.instance
        allowed = (self.enabled and not self.system.combat.junkrat.tire.busy(self.body)
                   and expr.truthy(instance.evaluate(instance.graph.node(1).field('m_condition')))
                   and not expr.truthy(instance.evaluate(instance.graph.node(63).field('m_condition')))
                   and not expr.truthy(instance.evaluate(instance.graph.node(64).field('m_condition'))))
        if not allowed:
            self.next_ms = None
            return
        interval = round(float(instance.evaluate(instance.graph.node(3).field('m_timeout'))) * 1000)
        if interval <= 0:
            raise ValueError('charge passive interval must be positive')
        if self.next_ms is None:
            self.next_ms = now_ms + interval
        while now_ms >= self.next_ms:
            before = self.script.component.entity_var(342).value()
            value = instance.evaluate(instance.graph.node(4).field('m_value'))
            self.script.set_ult_charge(value)
            self.script.component.run_now()
            self.next_ms += interval
            if value == before or value >= self.script.component.entity_var(338).value():
                self.next_ms = now_ms + interval
                break


SNAPSHOT_TYPES = (entity_hostility.Context, entity_hostility.Target,
    entity_tags.Context, entity_tags.Target, entity_tags.Tags,
    entity_queries.Context, entity_queries.Target, entity_queries.Possession,
    entity_filter.Target, FilterBits)
