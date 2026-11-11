"""Resource-driven personal and projected Zarya barrier interception."""

import math
from dataclasses import dataclass

from ow174.game import bots
from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000068
PETS = {0x0244: 2, 0x025C: 10}


def has_active_barrier(player):
    """Whether a live caster has an active particle barrier graph."""
    script = player.body_script
    return (player.hero.guid == HERO and script is not None and not script.retired
            and not script.component.owner_dead
            and any(_pet(instance) is not None for instance in script.component.instances.values()))


def _pet(instance):
    state = instance.states.get(PETS.get(instance.graph.index))
    return state if state is not None and state.active and not state.ending else None


@dataclass
class Barrier:
    activation: tuple
    remaining: float


class Barriers:
    def __init__(self, combat):
        self.combat = combat
        self.active: dict[int, Barrier] = {}
        self.projected: dict[int, Barrier] = {}

    def retire(self, body: int) -> None:
        self.active.pop(body, None)
        self.projected.pop(body, None)

    def single_replay_projection(self):
        """Identify the recipient of a single recorded projection without changing its pool."""
        if len(self.projected) != 1:
            return None
        [(body, pool)] = self.projected.items()
        casters = [player for player in self.combat.match.players if has_active_barrier(player)]
        if len(casters) != 1 or casters[0].body != body:
            return None
        [caster] = casters
        active = [
            pet for instance in caster.body_script.component.instances.values()
            if (pet := _pet(instance)) is not None
        ]
        if len(active) != 1 or active[0].instance.graph.index != 0x025C:
            return None
        [pet] = active
        if pool.remaining <= 0 or pool.activation != (pet.instance.id, pet.counter, pet.start):
            return None
        entity = pet.instance.find_var(0, 536).value()
        for player in self.combat.match.players:
            if (player.has_body and player.spawned and not player.gone and player.team == caster.team
                    and expr.Entity(player.body) == entity and not self.combat.is_dead(player)):
                return player
        return next((bot for bot in self.combat.match.bots if bot.retain_corpse and not bot.dead
                     and bot.team == caster.team and expr.Entity(bot.entity) == entity), None)

    def track_targets(self, state, angles):
        """Reconstructed ally selection for the two authored projected searches.

        Radius and base cone come from the resource. Missing cone modifier uses1.
        Rank by angular alignment then distance; require a live ally and static LOS.
        Other target States remain undeclared, rather than sharing this filter policy.
        """
        if state.instance.graph.index != 0x025C or state.node.pos not in (11, 18):
            return NotImplemented
        owner = next((p for p in self.combat.match.players
                      if p.body == state.instance.component.entity), None)
        if (owner is None or getattr(owner.hero, "guid", None) != HERO
                or self.combat.is_dead(owner) or self.combat.match.game_map.free_for_all):
            return ()
        # Import geometry lazily: combat owns this service and its geometric types.
        from ow174.game.combat import aim, ray_world
        from ow174.game.symmetra_portals import BODIES as TELEPORTER_BODIES

        origin, direction = self.combat._eye(owner), aim(*angles)
        reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        cone = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
        modifier = state.instance.component.entity_var(9595, create=False)
        if modifier is None or modifier.value() is None:
            cone = state.node.field("m_A30B383E")["m_expression"]["m_D99EF254"][0]
        best = None
        for target in self.combat._targets():
            ally = target.owner
            if ally is owner or target.team != owner.team:
                continue
            # Teleporter devices have combat pools but are not projected-barrier recipients.
            if isinstance(ally, bots.Bot) and ally.body in TELEPORTER_BODIES:
                continue
            if self.combat.vitals_of(target).dead:
                continue
            point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
            delta = tuple(point[k] - origin[k] for k in range(3))
            distance = math.sqrt(sum(v * v for v in delta))
            if distance <= 0 or distance > reach:
                continue
            alignment = sum(delta[k] * direction[k] for k in range(3)) / distance
            if alignment < math.cos(math.radians(cone)):
                continue
            wall = ray_world(self.combat._collision(), origin, tuple(v / distance for v in delta), distance)
            if wall is not None and wall < distance:
                continue
            score = (1 - alignment, distance, target.entity)
            if best is None or score < best[0]:
                best = score, target.entity
        return () if best is None else (best[1],)

    def contact(self, owner):
        if getattr(getattr(owner, "hero", None), "guid", None) == HERO:
            personal = self._active(owner, projected=False)
            if personal is not None:
                return personal
        if self.combat.match.game_map.free_for_all:
            return None
        entity = owner.entity if isinstance(owner, bots.Bot) else owner.body
        for caster in self.combat.match.players:
            if caster.team == owner.team and caster.hero.guid == HERO:
                projected = self._active(caster, projected=True, entity=entity)
                if projected is not None:
                    return projected
        return None

    def _active(self, caster, *, projected, entity=None):
        script = caster.body_script
        if caster.gone or not caster.has_body or script is None or script.component.owner_dead:
            return None
        index = 0x025C if projected else 0x0244
        instance = next((i for i in script.component.instances.values() if i.graph.index == index), None)
        if instance is None:
            return None
        pet = _pet(instance)
        if pet is None:
            return None
        if projected and instance.find_var(0, 536).value() != expr.Entity(entity):
            return None
        storage = self.projected if projected else self.active
        activation = (instance.id, pet.counter, pet.start)
        pool = storage.get(caster.body)
        if pool is None or pool.activation != activation:
            amount = graph.graph(0x0256).node(0).field("m_amount")
            capacity = expr.to_float(pet.evaluate(amount, watch=False))
            pool = storage[caster.body] = Barrier(activation, max(0.0, capacity))
        return (caster, pet, pool) if pool.remaining > 0 else None

    def intercept(self, owner, amount, source):
        contact = self.contact(owner)
        if contact is None:
            return False
        caster, pet, pool = contact
        absorbed = min(amount, pool.remaining)
        pool.remaining -= absorbed
        entry = graph.graph(0x0242).node(5)
        message = graph.guid(entry.field("m_gameMessage.m_gameMessage"))
        runtime.send_message(pet.instance.component, message,
                             {0x2A: absorbed, 0x12: expr.Entity(source)}, caster.body)
        if pool.remaining <= 0:
            pet.instance.state(11 if pet.instance.graph.index == 0x025C else 1).request_finish()
        return True
