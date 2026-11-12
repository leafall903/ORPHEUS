"""Original023B passive cadence and eligible hosted damage credit."""
from ow174.game import bots

HERO = 0x02E000000000023B
PASSIVE_FULL_CHARGE_SECONDS = 1960 / 5
DAMAGE_CHARGE_LOADOUTS = frozenset((0x0B9000000000028C, 0x0B9000000000028A, 0x0B900000000002F5))


def damage_script(fight, shooter, target, loadout):
    """Select the current living Sigma source; health accounting stays in Vitals."""
    if loadout not in DAMAGE_CHARGE_LOADOUTS:
        return None
    if (isinstance(shooter, bots.Bot) or shooter is target.owner or fight.match.ended
            or not fight.match._shown(shooter) or fight.is_dead(shooter)
            or shooter.hero.guid != HERO):
        return None
    if isinstance(target.owner, bots.Bot) and target.owner.spawner.get("summoned"):
        return None
    if not fight.match.game_map.free_for_all and shooter.team == target.team:
        return None
    script = shooter.body_script
    if script is None or script.component.strict or script.component.entity != shooter.body:
        return None
    return script
