"""Native DA8BD0/BDF100 empty-rule path, including actual Assassin13C2.

With neither inline rules nor a C1903771 registry-key expression, the candidate
test remains enum2. BDF420 never resolves a rule list for key0. This does not
infer an absent global registry or supply answers for nonempty/unknown rules.
"""


def empty_result(config):
    """Return the proved native enum or NotImplemented for other rule paths."""
    if config is None:
        return 2
    if type(config) is not dict or config.get("$") != "STUConfigVarFilter":
        raise ValueError("native callback filter requires STUConfigVarFilter")
    rules = config.get("m_93FA1642")
    if rules is not None and type(rules) not in (list, tuple):
        raise ValueError("native inline filter rules require an array or known absence")
    if rules or config.get("m_C1903771") is not None:
        return NotImplemented
    return 2
