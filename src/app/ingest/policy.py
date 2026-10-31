"""Thresholds are numeric predicates, never invented confidence probabilities."""
from copy import deepcopy
import math
from app.ingest import store

GATES = {
 'inventory.probe_errors': {'max': 0}, 'inventory.files': {'min': 1},
 'identity.title_known': {'min': 1}, 'identity.author_known': {'min': 1}, 'identity.conflicts': {'max': 0},
 'metadata.original_work_link': {'min': 1}, 'metadata.work_families': {'min': 2}, 'metadata.critical_conflicts': {'max': 0},
 'recording.score': {'min': .9}, 'recording.runtime_error': {'max': .02}, 'recording.narrator_agrees': {'min': 1},
 'recording.language_agrees': {'min': 1}, 'recording.abridgement_known': {'min': 1}, 'recording.ambiguous': {'max': 0},
 'alignment.coverage': {'min': .995}, 'alignment.max_gap_seconds': {'max': .5}, 'alignment.unmatched_fraction': {'max': .005},
 'alignment.missing_tracks': {'max': 0}, 'alignment.unresolved_representations': {'max': 0},
 'quality.complete': {'min': 1}, 'quality.decode_errors': {'max': 0}, 'quality.true_peak': {'max': 0}, 'quality.phase_loss_db': {'max': 3},
 'selection.ambiguous': {'max': 0}, 'planning.warning_count': {'max': 0},
}
DEFAULT = dict(auto_approve=False, auto_process=False, providers=['audible','audnexus','audiobookdb','hardcover','openlibrary','applebooks'], allowed_kinds=['book'], gates=GATES)


def validate(value):
    if not isinstance(value, dict) or set(value) - {'auto_approve','auto_process','providers','allowed_kinds','gates','revision'}: raise ValueError('unknown policy setting')
    out = deepcopy(DEFAULT); out.update({k:v for k,v in value.items() if k != 'revision'})
    if type(out['auto_approve']) is not bool or type(out['auto_process']) is not bool: raise ValueError('automation settings must be booleans')
    if out['auto_process'] and not out['auto_approve']: raise ValueError('auto_process requires auto_approve')
    if not isinstance(out['providers'], list) or not out['providers'] or any(n not in ('audible','audnexus','audiobookdb','hardcover','openlibrary','applebooks','googlebooks') for n in out['providers']): raise ValueError('invalid providers')
    if not isinstance(out['allowed_kinds'], list) or any(k not in ('book','radio','story','standup','episode') for k in out['allowed_kinds']): raise ValueError('invalid allowed_kinds')
    if not isinstance(out['gates'], dict) or set(out['gates']) != set(GATES): raise ValueError('every known gate is required')
    for name, gate in out['gates'].items():
        if not isinstance(gate, dict) or not gate or set(gate) - {'min','max'}: raise ValueError(f'invalid gate {name}')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in gate.values()): raise ValueError(f'non-finite gate {name}')
        if gate.get('min', -math.inf) > gate.get('max', math.inf): raise ValueError(f'inverted gate {name}')
    return out


def current(): return store.policy_latest() or dict(revision=0, **deepcopy(DEFAULT))


def save(value):
    out = validate(value); revision = store.policy_save(out); return dict(revision=revision, **out)


def evaluate(metrics, kind, value=None):
    p = value or current(); checks = []
    for name, gate in p['gates'].items():
        v = metrics.get(name); known = type(v) in (int,float) and math.isfinite(v)
        passed = known and ('min' not in gate or v >= gate['min']) and ('max' not in gate or v <= gate['max'])
        checks.append(dict(metric=name, value=v if known else None, threshold=gate, passed=passed, reason='' if passed else 'missing measurement' if not known else 'outside threshold'))
    allowed = kind in p['allowed_kinds']; eligible = allowed and all(c['passed'] for c in checks)
    return dict(eligible=eligible, kind_allowed=allowed, checks=checks, policy=p, auto_approve=eligible and p['auto_approve'], auto_process=eligible and p['auto_process'])
