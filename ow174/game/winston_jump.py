"""Server movement and radial damage for Winston's authored Jump Pack cast."""

import math

from ow174.game.script import expr, nodes, runtime
from ow174.game.winston_barrier import HERO

LOADOUT = 0x0B9000000000002D


class ImpactContext(runtime.InstanceContext):
    def __init__(self, instance, target):
        super().__init__(instance, None)
        self.target = expr.Entity(target)

    def param(self, var):
        return self.target if var == 92 else super().param(var)


class JumpPack:
    """Reconstructed physics driven by accepted authored cooldown activations.

    010A's accepted Ability writes its six/two-second cooldown even though
    unimplemented native actions leave the movement subtree alive forever.
    The cooldown restart timestamp identifies the accepted cast; client input
    cannot create an extra launch without passing that ordinary ability gate.
    Native launch-angle interpretation, impact falloff and impulses remain
    inferred. No strict native VM behavior is changed by this bridge.
    """

    def __init__(self, combat):
        self.combat = combat
        self.seen = {}
        self.flights = {}

    def retire(self, body):
        self.seen.pop(body, None)
        self.cancel(body)

    def cancel(self, body):
        flight = self.flights.pop(body, None)
        if flight is not None:
            self.stop(flight[0])

    @staticmethod
    def stop(instance):
        state = instance.states.get(2)
        if state is not None and state.active:
            state.end(False)
            instance.component.run_now()

    def command(self, player, command):
        if (player.hero.guid != HERO or player.body_script.component.strict
                or self.combat.match.ended):
            return
        component = player.body_script.component
        flight = self.flights.get(player.body)
        if flight is not None and command.frame > flight[1] and player.mover.landed:
            self.impact(player, flight[0], command.frame, landing=True)
            self.cancel(player.body)
        for instance in component.instances.values():
            if instance.graph.index != 0x010A:
                continue
            ability = instance.states.get(19)
            if not isinstance(ability, nodes.Ability) or ability.cur <= 0:
                continue
            token = (instance.id, ability.last)
            if self.seen.get(player.body) == token:
                continue
            self.seen[player.body] = token
            old = self.flights.pop(player.body, None)
            if old is not None:
                self.stop(old[0])
            self.launch(player, instance, command)
            self.flights[player.body] = (instance, command.frame)
            break

    def launch(self, player, instance, command):
        from ow174.game.mover import FELL, JUMPED, REAL_JUMP

        state = instance.state(2)
        if not state.active:
            # An accepted air recast can have reentered the old active subtree.
            # After retiring that flight, give this cast a fresh effect lifetime.
            state.begin()
            instance.component.run_now()
        # Resource node85 supplies 90/25 degree bounds; legacy initialization
        # retains speed30 in var1262. Interpret bounds as elevation above ground.
        config = instance.graph.node(85).field('m_value')['m_configVars'][0]
        lower = expr.to_float(instance.evaluate(config['m_E26E5E09']))
        upper = expr.to_float(instance.evaluate(config['m_5023B9B0']))
        speed = expr.to_float(instance.find_var(0, 1262).value())
        yaw, pitch = command.aim()
        elevation = math.radians(max(lower, min(upper, -pitch * 360 / 65536)))
        heading = yaw * 2 * math.pi / 65536
        horizontal = speed * math.cos(elevation)
        mover = player.mover
        mover.velocity = (math.sin(heading) * horizontal, speed * math.sin(elevation),
                          math.cos(heading) * horizontal)
        mover.flags_word = mover.flags_word & ~FELL | JUMPED | REAL_JUMP
        mover.air_ticks = 0
        mover.landed = False
        self.impact(player, instance, command.frame, landing=False)

    def impact(self, player, instance, frame, *, landing):
        from ow174.game import combat, sigma

        search = instance.state(4 if landing else 6)
        damage = instance.state(3 if landing else 7)
        radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
        modify = damage.node.field("m_modifyHealth")
        loadout = damage.evaluate(modify["m_loadout"], watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        if radius <= 0:
            return
        fight = self.combat
        collision = fight._collision()
        centre = tuple(player.mover.position)
        origin = (centre[0], centre[1] + 0.1, centre[2])
        barrier_damage = {}
        victims = []
        for target in fight._enemies(player):
            a, b = target.segment()
            distance = max(0.0, combat.segment_distance(centre, a, b) - target.radius)
            if distance > radius or not combat.capsule_visible(collision, origin, a, b, target.radius):
                continue
            context = ImpactContext(instance, target.entity)
            applied = expr.to_float(expr.evaluate(modify["m_amount"], context))
            if applied <= 0:
                continue
            point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
            offset = combat._sub(point, origin)
            reach = math.sqrt(combat._dot(offset, offset))
            hit = None
            if reach > target.radius:
                ray = tuple(v / reach for v in offset)
                hit = sigma.barrier_intersection(fight, player, origin, ray, reach - target.radius)
            if hit is None:
                victims.append((target, applied))
            else:
                field = hit[1]
                _, prior = barrier_damage.get(field.entity, (field, 0.0))
                barrier_damage[field.entity] = (field, max(prior, applied))

        for field, applied in barrier_damage.values():
            sigma.damage_barrier(fight, field, applied)
        knockback = sigma.projectile_knockback(damage, modify)
        for target, applied in victims:
            vitals = fight.vitals_of(target)
            previous = vitals.total
            fight._damage(player, target, applied, False, loadout, frame)
            if vitals.total < previous:
                sigma.apply_impact_impulse(fight, (0, 0, 0), target, knockback, centre)
