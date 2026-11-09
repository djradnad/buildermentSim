"""Check a layout someone submitted and add it to the catalogue when it is a true, new best.

    python site/tools/submit.py CODE AUTHOR      # CODE: a game blueprint code, AUTHOR: who gets the credit

Steps: download the blueprint from the blueprint service, keep its belts, splitters and undergrounds (anything
else is refused), then rebuild the catalogue with it as make_catalogue does: exact flow check (every output gets
an equal share), inputs side by side and outputs side by side, symmetry rule, and the obsolete rule (it must beat
every layout of its ratio on at least one of width, height, symmetry, mixing or the clean-up scores).

Accepted: the design is appended to catalogue_extra.jsonl and site/catalogue.json is rewritten; exit code 0.
Refused: nothing changes; exit code 2. Either way one line of JSON on stdout says why (the GitHub Action posts it on
the submission issue).
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import blueprints                                          # noqa: E402

CATALOGUE = os.environ.get('CATALOGUE', os.path.join(HERE, '..', 'catalogue.json'))
EXTRA = os.environ.get('CATALOGUE_EXTRA', os.path.join(HERE, 'catalogue_extra.jsonl'))
BELTS = (3000, 3001, 3002, 3003)


def result(ok, text, **kw):
    print(json.dumps(dict(ok=ok, text=text, **kw)))
    sys.exit(0 if ok else 2)


def main(code, author):
    if not re.fullmatch(r'[0-9a-z]{4,12}', code):
        result(False, 'That does not look like a blueprint code.')
    author = re.sub(r'[^A-Za-z0-9_-]', '', author)[:39] or 'someone'
    try:
        rows = blueprints.download(code)
    except Exception as ex:
        result(False, 'Could not download blueprint %s (%s).' % (code, ex))
    other = [r for r in rows if r[0] not in BELTS]
    if other:
        result(False, 'The blueprint has %d buildings that are not belts, splitters or undergrounds; the catalogue '
                      'only takes those.' % len(other))
    if not rows:
        result(False, 'The blueprint is empty.')
    old = json.load(open(CATALOGUE))['designs']
    if any(d.get('id') == code for d in old):
        result(False, 'Blueprint %s is already in the catalogue.' % code)
    name = 'submitted by %s (%s)' % (author, code)
    entry = dict(name=name, source=author, id=code, rows=[r[:4] for r in rows])
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, 'submission.jsonl')
        with open(src, 'w') as f:
            f.write(json.dumps(entry) + '\n')
        out = os.path.join(tmp, 'catalogue.json')
        with open(out, 'w') as f:
            p = subprocess.run([sys.executable, os.path.join(HERE, 'make_catalogue.py'), CATALOGUE, src],
                               stdout=f, stderr=subprocess.PIPE, text=True)
        log = p.stderr.splitlines()
        if p.returncode:
            result(False, 'The catalogue build failed: ' + ' / '.join(log[-3:]))
        new = json.load(open(out))['designs']
    mine = [d for d in new if d.get('id') == code and d.get('source') == author]
    if not mine:
        why = [l for l in log if name in l]
        reason = why[-1] if why else ''
        if reason.startswith('not balanced'):
            text = 'The layout does not balance: with exact flows, not every output gets an equal share.'
        elif reason.startswith('ports not together'):
            text = 'The inputs (or outputs) are not side by side. Catalogue layouts keep them next to each other, ' \
                   'since they sit on a bus.'
        elif reason.startswith('obsolete'):
            text = 'It works, but the catalogue already has a layout of this ratio that is at least as good on ' \
                   'every score: ' + reason.split('(beaten by ', 1)[-1][:-1] + '.'
        elif reason.startswith('merger left out'):
            text = 'The catalogue only takes splitters and balancers, not N:1 mergers.'
        elif reason.startswith('skip'):
            text = 'The blueprint could not be read as a layout: ' + reason
        else:
            text = 'The same layout is already in the catalogue.'
        result(False, text)
    d = mine[0]
    before = {(e['n'], e['m'], tuple(e['grid'])) for e in old}
    if (d['n'], d['m'], tuple(d['grid'])) in before:
        result(False, 'The same layout is already in the catalogue.')
    gone = [e for e in old if (e['n'], e['m']) == (d['n'], d['m']) and
            (e['n'], e['m'], tuple(e['grid'])) not in {(x['n'], x['m'], tuple(x['grid'])) for x in new}]
    with open(EXTRA, 'a') as f:
        f.write(json.dumps(entry) + '\n')
    with open(CATALOGUE, 'w') as f:
        json.dump({'version': 1, 'designs': new}, f, separators=(',', ':'))
    text = 'Added: %d:%d %s %dx%d, %d pieces, footprint %d, %d gold.' % (
        d['n'], d['m'], {'sym': 'symmetric', 'quasi': 'quasi-symmetric', 'asym': 'asymmetric'}[d['mode']],
        d['W'], d['H'], d['pieces'], d['footprint'], d['gold'])
    if gone:
        text += ' It replaces %s.' % ', '.join('%dx%d %s' % (e['W'], e['H'], e['name']) for e in gone)
    result(True, text, ratio='%d:%d' % (d['n'], d['m']))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1].strip().lower(), sys.argv[2])
