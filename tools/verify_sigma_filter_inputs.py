"""Audit actual outgoing body filter inputs without inventing category/status defaults.

This checks a necessary raw-input prerequisite, not full native admission.
Native component construction/inheritance/replication can add semantics which
remain unverified; controllers are retained as diagnostics, never body aliases.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from test_sigma_combat import SigmaCombatTests  # noqa: E402
from test_sigma_symmetra_sentry import SigmaSentryTests  # noqa: E402
from verify_sigma_npc_static import AuditClient  # noqa: E402

from ow174.game import matrix, npc, world  # noqa: E402
from ow174.game.script import filter_bits, graph  # noqa: E402


def capture(builder):
    def create(_origin, definition, components, **fields):
        return {'definition': definition, 'components': components, **fields}

    with patch.object(world, 'create', side_effect=create):
        return builder()


def row(name, role, entity, record, config):
    bits, = record['components'][26]
    any_bits = config['m_84065FD1']
    any_relation = config['m_27CF71D5']
    variants = [filter_bits.project(0, bits, status10=status) for status in (False, True)]
    # This graph has no any-relation alternative. Do not equate this necessary
    # category check with full masks, hostility, tag, status or LOS acceptance.
    established = any_relation == 0 and all(bool(value & any_bits) for value in variants)
    return {'name': name, 'role': role, 'entity': f'{entity:08X}',
            'definition': f'{record["definition"]:016X}',
            'create_flags': record['flags'],
            'has_transform_flag': bool(record['flags'] & world.WITH_TRANSFORM),
            'outgoing_component_ids': sorted(record['components']),
            'outgoing_component51_pools': {
                index: part.hex() for index, part in enumerate(record['components'].get(51, ())[:48])
                if part is not None
            },
            'outgoing_component26': f'{bits:016X}', 'authored_any_bits': f'{any_bits:016X}',
            'authored_any_relation': f'{any_relation:016X}',
            'authored_excluded_bits': f'{config["m_4EA686DB"]:016X}',
            'declared_status10_projection_variants': [f'{value:016X}' for value in variants],
            'raw_required_category_established': established}


def sentry_filter_rows(config):
    fixture = SigmaCombatTests()
    run, _, owner, _ = fixture.arena(offset=0, distance=4)
    fixture.wall(run, sideways=False, offset=-3)
    sentry_case = SigmaSentryTests()
    sentry_case.prepare(run, owner)
    sentry = sentry_case.launch(run, owner, -32768)
    bot = sentry.bot
    tags = matrix.tags().get(bot.body)

    def observe(role):
        record = capture(lambda: run.match._bot_create_update(bot).build(0))
        evidence = row(bot.name, role, bot.entity, record, config)
        evidence['static_tags'] = None if tags is None else [f'{tag:016X}' for tag in sorted(tags)]
        return evidence

    flight = observe('summoned_body_in_flight')
    sentry_case.attach(run, sentry)
    return [flight, observe('summoned_body_attached')]


def audit(catalog, *, include_sentry=False):
    run, player, enemy, _health = SigmaCombatTests().arena(offset=0, distance=6)
    for actor in run.players:
        actor.client = AuditClient(actor)
    config = graph.graph(0x1E41).node(37).field('m_filter')
    rows = []
    for name, actor in (('Sigma', player), ('Soldier', enemy)):
        body = capture(lambda actor=actor: run.match._body_create(0, actor, None))
        rows.append(row(name, 'player_body', actor.body, body, config))
        controller = capture(lambda actor=actor: run.match._player_create(0, actor, own=False))
        rows.append(row(name, 'player_controller_diagnostic_only', actor.entity, controller, config))
    for definition in npc.load_catalog(catalog).values():
        bot = npc.spawn(run.match, definition, tuple(enemy.mover.position), team=1)
        body = capture(lambda bot=bot: run.match._bot_create_update(bot).build(0))
        rows.append(row(definition.name, 'npc_body', bot.entity, body, config))
    bodies = [item for item in rows if item['role'] in ('player_body', 'npc_body')]
    if include_sentry:
        rows.extend(sentry_filter_rows(config))
    return {'build': 104319, 'source_path': str(ROOT), 'static_acceptance_complete': False,
            'catalog': str(catalog), 'catalog_sha256': hashlib.sha256(catalog.read_bytes()).hexdigest(),
            'scope': 'Actual Match create arguments before serialization; flat fixture, fake transport; '
                     'status10 false/true are explicit alternatives, not observed actor statuses.',
            'body_prerequisites_established': sum(
                item['raw_required_category_established'] for item in bodies),
            'body_count': len(bodies), 'rows': rows,
            'not_verified': ['complete native component26 packet dispatch/later initialization',
                             'component25 category inheritance',
                             'actual status10 and other status/category sources',
                             'controller-to-body target routing; controller diagnostics are not substitutes',
                             'complete filter/TrackTargets/body services and1DBC effects',
                             'NPC body VM readiness, incoming attacks and AI',
                             'original-client acceptance']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--include-sentry', action='store_true',
                        help='Add actual 2658 flight and attachment create diagnostics')
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error('generated report must remain outside the source repository')
    result = audit(args.catalog, include_sentry=args.include_sentry)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print('Body raw-category prerequisites:',
          f"{result['body_prerequisites_established']}/{result['body_count']}")
    print('Complete native packet dispatch/later initialization/inheritance/routing remain unverified; '
          'full static acceptance is OPEN.')
    if result['body_prerequisites_established'] != result['body_count']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
