"""Rows for the viewer's quick-changes panel: pure logic, NO Qt import.

This is the same "what changed in the model / calibration surface" rollup the
``--arxml-only`` report gives -- which ARXML/A2L files were updated, and the
AUTOSAR-level changes underneath (port interfaces, software components, ports,
runnables, events, RTE access points, A2L objects) -- so the reviewer can see
the shape of the change without opening a report.

Kept Qt-free so it can be unit tested headless; the widget in summary.py only
walks the returned sections and paints them.
"""

from collections import namedtuple

from ..diff_engine import ruleset_for
from ..scanner import (summarize_a2l, summarize_ifaces, summarize_rte,
                       summarize_swcs)

# sign: '+' added, '−' removed, '~' changed. rel = file to jump to.
Row = namedtuple('Row', 'sign name detail rel')

_FILE_SIGN = {'real-change': '~', 'added': '+', 'deleted': '−'}


def _iface_kind(tag):
    return tag.replace('-INTERFACE', '')


def _item(swc, name):
    """'/Comp/Ctrl', 'In2' -> 'Ctrl.In2' (short SWC name keeps rows compact)."""
    return '{}.{}'.format(swc.rsplit('/', 1)[-1], name)


def _updated_files(results):
    rows = []
    for rel, r in sorted(results.items()):
        if ruleset_for(rel) not in ('arxml', 'a2l'):
            continue
        sign = _FILE_SIGN.get(r['status'])
        if not sign:
            continue
        if r['status'] != 'real-change':
            detail = ''
        elif r.get('binary'):
            detail = 'binary change'
        else:
            n_real = sum(1 for h in r['hunks'] if h['kind'] == 'real')
            n_moved = sum(1 for h in r['hunks'] if h['kind'] == 'moved')
            detail = '{} hunk(s){}'.format(
                n_real, ', {} moved'.format(n_moved) if n_moved else '')
        rows.append(Row(sign, rel, detail, rel))
    return rows


def summary_sections(results):
    """[(title, [Row, ...]), ...] -- only non-empty sections, in report order."""
    sections = []

    rows = _updated_files(results)
    if rows:
        sections.append(('Updated ARXML / A2L files', rows))

    added, removed = summarize_ifaces(results)
    rows = [Row('+', p, _iface_kind(t), rel) for rel, p, t in added]
    rows += [Row('−', p, _iface_kind(t), rel) for rel, p, t in removed]
    if rows:
        sections.append(('Port interfaces', rows))

    swcs = summarize_swcs(results)
    rows = [Row('+', s, '', rel) for rel, s in swcs['swcs']['added']]
    rows += [Row('−', s, '', rel) for rel, s in swcs['swcs']['removed']]
    if rows:
        sections.append(('Software components', rows))

    for cat, title in (('ports', 'Ports'), ('runnables', 'Runnables'),
                       ('events', 'Events')):
        rows = [Row('+', _item(s, n), d, rel) for rel, s, n, d in swcs[cat]['added']]
        rows += [Row('−', _item(s, n), d, rel) for rel, s, n, d in swcs[cat]['removed']]
        rows += [Row('~', _item(s, n), '{} → {}'.format(od, nd) if od != nd else nd, rel)
                 for rel, s, n, od, nd in swcs[cat]['changed']]
        if rows:
            sections.append((title, rows))

    added, removed = summarize_rte(results)
    rows = [Row('+', n, '', rel) for rel, n in added]
    rows += [Row('−', n, '', rel) for rel, n in removed]
    if rows:
        sections.append(('RTE access points', rows))

    added, removed = summarize_a2l(results)
    rows = [Row('+', n, k, rel) for rel, n, k in added]
    rows += [Row('−', n, k, rel) for rel, n, k in removed]
    if rows:
        sections.append(('A2L characteristics / measurements', rows))

    return sections
