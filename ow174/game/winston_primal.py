"""Host Winston's authored Primal Rage health and fist states."""

import math

from ow174.game import defenses, deflection
from ow174.game.script import expr
from ow174.game.winston_weapons import GRAPH as WEAPON_GRAPH
from ow174.game.winston_weapons import HERO
from ow174.game.world import HEALTH_POOL

GRAPH = 0x0109
HEALTH = 4
CLOCK_NODE = 25
WINDUPS = (20, 21)


def active_health(component):
    instance = next((i for i in component.instances.values() if i.graph.index == GRAPH), None)
    state = instance.states.get(HEALTH) if instance is not None else None
    return state if state is not None and state.active else None


class Primal:
    def __init__(self, fight):
        self.fight = fight
        self.bonus = {}
        self.hits = {}

    def health_state(self, player):
        script = player.body_script
        if script is None or script.hero != HERO or script.retired or script.component.owner_dead:
            return None
        return active_health(script.component)

    def sync_health(self, player, *, dead=False):
        state = None if dead else self.health_state(player)
        extra = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False)) if state else 0.0
        old = self.bonus.get(player.body, 0.0)
        if old == extra:
            return
        target = next((t for t in self.fight._targets() if t.owner is player), None)
        if target is None:
            return
        vitals = self.fight.vitals_of(target)
        vitals.most[HEALTH_POOL] += extra - old
        vitals.now[HEALTH_POOL] = min(vitals.now[HEALTH_POOL], vitals.most[HEALTH_POOL])
        vitals.changed.add(HEALTH_POOL)
        if extra > old and not vitals.dead:
            # Transform refill follows the same-build health-pool policy.
            vitals.heal(sum(vitals.most.values()))
        if extra:
            self.bonus[player.body] = extra
        else:
            self.bonus.pop(player.body, None)
            self.hits.pop(player.body, None)
        self.fight._sync_body_health(player, vitals)

    def update(self, player):
        state = self.health_state(player)
        if state is not None:
            instance = state.instance
            chase = instance.states.get(instance.graph.node(CLOCK_NODE).state)
            now = self.fight.tick * instance.component.frame_ms
            if chase is not None and chase.active and now > chase.last:
                chase.tick(now)
                instance.component.run_now()
        if state is not None or player.body in self.bonus:
            self.sync_health(player, dead=self.fight.is_dead(player))

    def retire(self, body):
        self.bonus.pop(body, None)
        self.hits.pop(body, None)

    @staticmethod
    def interrupt(player):
        script = player.body_script
        if script is None or script.hero != HERO:
            return
        for instance in script.component.instances.values():
            if instance.graph.index == WEAPON_GRAPH:
                for index in WINDUPS:
                    state = instance.states.get(index)
                    if state is not None and state.active:
                        state.end(False)
        script.component.run_now()

    def punches(self, player, command):
        if self.health_state(player) is None:
            return
        for instance in player.body_script.component.instances.values():
            if instance.graph.index != WEAPON_GRAPH:
                continue
            for health_index, search_index in ((35, 18), (36, 17)):
                health, search = instance.states.get(health_index), instance.states.get(search_index)
                if health is None or search is None or not (health.active and search.active):
                    continue
                hit = self.hits.setdefault(player.body, {})
                key = (instance.id, health_index)
                if hit.get(key) == health.counter:
                    continue
                hit[key] = health.counter
                self.hit(player, command, health, search)

    def hit(self, player, command, health, search):
        from ow174.game.combat import aim, capsule_visible, hit_def, segment_pair_distance

        direction = aim(*command.aim())
        yaw = command.aim()[0] * 2 * math.pi / 65536
        right = (math.cos(yaw), 0.0, -math.sin(yaw))
        feet = tuple(player.mover.position)

        def point(field):
            local = search.evaluate(search.node.field(field), watch=False)
            return tuple(feet[k] + right[k] * local.x + direction[k] * local.z
                         + (local.y if k == 1 else 0.0) for k in range(3))

        a, b = point("m_05BE62DD"), point("m_B3A77CD0")
        radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
        definition = hit_def(health, {"m_modifyHealth": health.node.field("m_modifyHealth")})
        horizontal, vertical = (
            expr.to_float(health.evaluate(health.node.field(f"m_modifyHealth.{key}"), watch=False))
            for key in ("m_E33EFEE1", "m_583C208B")
        )
        origin = self.fight._eye(player)
        volumes = ((a, b, radius),)
        self.fight._melee_barriers(player, volumes, definition.amount)
        for target in self.fight._enemies(player):
            bottom, top = target.segment()
            if segment_pair_distance(a, b, bottom, top) > radius + target.radius:
                continue
            if (self.fight.walls.blocked(origin, bottom)
                    or not capsule_visible(self.fight._collision(), origin, bottom, top, target.radius)
                    or deflection.frontal(target, direction) is not None):
                continue
            affected = self.fight._damage(player, target, definition.amount, False, definition.loadout,
                                          command.frame, tags=definition.tags, credit_charge=False)
            if affected and not defenses.control_immune(target.owner):
                self.fight.radial_knockback(target, feet, horizontal, vertical, command.frame)
        self.fight.walls.swing(player, volumes, definition.amount, set())
