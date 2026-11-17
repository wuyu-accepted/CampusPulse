"""Offline loader and integrity checker for the baseline data handoff."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def rows(path):
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def load_world():
    """Uses no evaluation directory or completed outputs."""
    return json.loads((ROOT/'world/fixed-world.json').read_text('utf-8'))


def paired_inputs():
    """Returns the manifest-bound frozen, past-visible resident observations."""
    yield from rows(ROOT/'inputs/paired-observations.jsonl.gz')


def expected_count():
    manifest = json.loads((ROOT/'manifest.json').read_text('utf-8'))
    count = manifest['runtime_counts']['paired_observations']
    if type(count) is not int or count <= 0:
        raise ValueError('invalid observation count')
    return count


def verify():
    import numpy as np
    manifest = json.loads((ROOT/'manifest.json').read_text('utf-8'))
    for name, expected in manifest['files'].items():
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT.resolve()):
            raise ValueError('unsafe manifest path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('file checksum mismatch: '+name)
    world = load_world()
    ids = [a['agent_id'] for a in world['agents']]
    if len(ids)!=24 or len(set(ids))!=24:
        raise ValueError('population mismatch')
    profiles = json.loads((ROOT/'world/semantic-llm-forum-agent-population-v2.json').read_text('utf-8'))
    if [a['agent_id'] for a in profiles['agents']] != ids:
        raise ValueError('profile identity mismatch')
    source_ids = set(ids)
    for edge in world['relationships']['edges']:
        if edge['agent_a'] not in source_ids or edge['agent_b'] not in source_ids:
            raise ValueError('relationship references absent Agent')
    with np.load(ROOT/'world/initial-particles.npz',allow_pickle=False) as asset:
        if asset['values'].shape!=(24,10,12) or list(asset['agent_ids'])!=ids:
            raise ValueError('particle layout mismatch')
        if not np.allclose(asset['conditional_weights'].sum(axis=1),1):
            raise ValueError('conditional weights mismatch')
        if not np.isclose(asset['design_weights'].sum(),1):
            raise ValueError('population weights mismatch')
    panel = list(paired_inputs())
    expected = expected_count()
    if len(panel)!=expected or len({r['case_id'] for r in panel})!=expected:
        raise ValueError('paired observations are incomplete')
    for row in panel:
        if row['agent_id'] not in source_ids:
            raise ValueError('unknown panel Agent')
        if 'output' in row or 'expression_stance' in row:
            raise ValueError('evaluation output leaked into input')
    ours_path=ROOT/'evaluation-only/ours-decisions.jsonl.gz'
    if ours_path.exists():
        ours=list(rows(ours_path))
        expected_by_id={r['case_id']:r for r in panel}
        if len(ours)!=expected or len({r['case_id'] for r in ours})!=expected:
            raise ValueError('CampusPulse paired results incomplete')
        for result in ours:
            original=expected_by_id.get(result['case_id'])
            if original is None or any(result[k]!=original[k] for k in ('agent_id','branch','tick')):
                raise ValueError('CampusPulse result identity mismatch')
            if not isinstance(result.get('output'),dict) or not isinstance(result['output'].get('visible_text'),str):
                raise ValueError('CampusPulse result contract missing')
    print(json.dumps({'verified':True,'agents':24,'particles':240,'paired_inputs':expected,
                      'files':len(manifest['files']),'corpus':manifest['corpus_counts']},ensure_ascii=False))


def validate_outputs(path):
    expected = {r['case_id']:r for r in paired_inputs()}
    seen = set()
    for row in rows(path):
        case = expected.get(row.get('case_id'))
        if not case or row['case_id'] in seen:
            raise ValueError('unknown or repeated case ID')
        seen.add(row['case_id'])
        if any(row[k]!=case[k] for k in ('agent_id','branch','tick')):
            raise ValueError('case identity changed')
        if not isinstance(row.get('visible_text'),str) or not row.get('method'):
            raise ValueError('method and visible_text are required')
        if row.get('action') not in {'ignore','read','post','reply','quote','repost','verify','correct','seek_help','report_risk'}:
            raise ValueError('unknown action')
    if seen != set(expected):
        raise ValueError(f'incomplete paired outputs: {len(seen)}/{len(expected)}')
    print(json.dumps({'valid':True,'records':len(seen)}))


def state_metrics(path):
    """Reuse original NumPy statistical operators and particle loader."""
    sys.path.insert(0,str(ROOT/'reference-src'))
    from envs.campus_pulse_governance.reddit.particle_model import load_particle_snapshot
    from envs.campus_pulse_governance.reddit.evaluation import population_state_metrics
    print(json.dumps(population_state_metrics(load_particle_snapshot(path),load_world()),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('verify','example','validate-outputs','state-metrics'))
    parser.add_argument('--file',type=Path)
    args=parser.parse_args()
    if args.command=='verify': verify()
    elif args.command=='example':
        row=next(paired_inputs())
        print(json.dumps({'agent_id':row['agent_id'],'branch':row['branch'],'tick':row['tick'],
                          'input_keys':list(row['input']),'world_population':len(load_world()['agents'])}))
    elif args.command=='validate-outputs':
        if not args.file: parser.error('--file required')
        validate_outputs(args.file)
    else:
        if not args.file: parser.error('--file required')
        state_metrics(args.file)
