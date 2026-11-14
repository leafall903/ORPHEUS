"""Physical-time feet for ordinary Soldier contact with a routed NPC."""

from ow174.game.content import SOLDIER
from ow174.game.sombra_replay import world_frame


def position(owner, command, bot, active, values):
    current = tuple(bot.position)
    script = owner.body_script
    if (script is None or script.hero != SOLDIER or script.retired
            or script.component.entity != owner.body or active is not None or not bot.route):
        return current
    from ow174.game.combat import EYE, TICK, Target

    world_ms = round(TICK * 1000)
    if script.frame_ms != world_ms or owner.quanta != world_ms * 1000:
        return current
    tick = world_frame(script, command.frame, world_ms)
    fight = owner.match.combat
    history = fight.history.get(bot.entity)
    # Do not extrapolate missing poses or substitute a new body's history.
    if tick is None or tick >= owner.match.tick or not history or not history[0][0] <= tick <= history[-1][0]:
        return current
    target = Target(bot.entity, bot.team, current, values.stand_height, values.stand_radius, EYE, bot)
    shown = fight.seen(target, tick)
    return current if shown is None else shown.feet
