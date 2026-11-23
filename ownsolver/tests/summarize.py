"""Summarise race.jsonl: per solver decided counts, wins, PAR-2 time; and a markdown table of every case.
Usage: python3 summarize.py [race.jsonl] [--table]"""
import json
import sys

path = next((a for a in sys.argv[1:] if not a.startswith('--')), 'race.jsonl')
rows = [json.loads(l) for l in open(path)]
by = {}
for r in rows:
    by.setdefault(r['name'] or r['spec'], {})[r['solver']] = r
solvers = ['own', 'cpsat', 'kissat']
stat = {s: dict(found=0, inf=0, unk=0, bad=0, err=0, wins=0, par2=0.0, conflict=0) for s in solvers}
conflicts = []
for name, d in by.items():
    decided = {s: r for s, r in d.items() if r['status'] in ('FOUND', 'INFEASIBLE')}
    if len({r['status'] for r in decided.values()}) > 1:
        conflicts.append((name, {s: r['status'] for s, r in d.items()}))
    best = min(decided.items(), key=lambda kv: kv[1]['time'])[0] if decided else None
    for s, r in d.items():
        st = stat[s]
        k = dict(FOUND='found', INFEASIBLE='inf', UNKNOWN='unk', BADFIND='bad').get(r['status'], 'err')
        st[k] += 1
        st['par2'] += r['time'] if k in ('found', 'inf') else 2 * r['timeout']
        if s == best:
            st['wins'] += 1
print('cases: %d   timeout per run: %s s' % (len(by), rows[0]['timeout'] if rows else '?'))
print('| solver | FOUND | INFEASIBLE | UNKNOWN | BADFIND | ERROR | fastest | PAR-2 s |')
print('|---|---|---|---|---|---|---|---|')
for s in solvers:
    st = stat[s]
    print('| %s | %d | %d | %d | %d | %d | %d | %.0f |' % (s, st['found'], st['inf'], st['unk'], st['bad'], st['err'],
                                                          st['wins'], st['par2']))
print('status conflicts between solvers:', conflicts or 'none')
if '--table' in sys.argv:
    print('\n| case | own | cpsat | kissat |\n|---|---|---|---|')
    for name, d in by.items():
        print('| %s | %s |' % (name, ' | '.join('%s %.1f' % (d[s]['status'], d[s]['time']) if s in d else '-'
                                              for s in solvers)))
