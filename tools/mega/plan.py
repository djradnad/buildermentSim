"""The 2CCJhI production plan as data: for every item, how many belts (480/min) each consumer recipe takes.
Parsed from the belts table of the ET plan (mega/belts_and_valves.md).   python tools/mega/plan.py [table.md]"""
import json
import re
import sys


def parse(path='/mnt/project-files/builderment/mega/belts_and_valves.md'):
    plan = {}
    for line in open(path):
        cols = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cols) != 4 or cols[0] in ('Item', '---') or not re.match(r'^[0-9.]+$', cols[1]):
            continue
        use = {}
        for part in cols[2].split(','):
            m = re.match(r'\s*([A-Za-z_]+) (\d+)', part)
            if m:
                use[m.group(1)] = use.get(m.group(1), 0) + int(m.group(2))
        for m in re.finditer(r'([A-Za-z_]+) ([0-9.]+)', cols[3]):
            use[m.group(1)] = use.get(m.group(1), 0) + float(m.group(2))
        plan[cols[0]] = dict(belts=float(cols[1]), use={k: round(v, 6) for k, v in use.items()})
    return plan


if __name__ == '__main__':
    plan = parse(*sys.argv[1:2])
    json.dump(plan, open('data/plan.json', 'w'), indent=1)
    bad = 0
    for item, p in plan.items():
        tot = sum(p['use'].values())
        flag = '' if abs(tot - p['belts']) < 0.02 else '   <-- uses add to %.2f' % tot
        bad += bool(flag)
        print('%-26s %7.2f belts = %6.0f/min -> %s%s' % (item, p['belts'], p['belts'] * 480, ', '.join('%s %.0f' % (k, v * 480) for k, v in p['use'].items()), flag))
    print(len(plan), 'items,', bad, 'rows whose uses do not add up')
