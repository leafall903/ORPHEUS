"""Biotic Grenade's scoped 104319 blast and healing-effect constants."""

GRAPH = 0x0B97
PROJECTILE = 0x0400000000001426
HEAL = 100.0
AMPLIFICATION = 1.5
DURATION = 4.0  # 0267.053 -> 0C71 -> 0B99/0C72


def refresh(windows, now):
    """Merge overlapping grenade windows without stacking their scalar."""
    if windows and now <= windows[-1][1]:
        windows[-1] = (windows[-1][0], now + DURATION)
    else:
        windows.append((now, now + DURATION))


def healing_scale(vitals, now):
    if any(start <= now + 1e-9 < end for start, end in vitals.healing_block):
        return 0.0
    if any(start <= now + 1e-9 < end for start, end in vitals.healing_boost):
        return AMPLIFICATION
    return 1.0
