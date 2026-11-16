"""Two corridors crossing: h rows (west-east) and w columns (north-south), arms A long, free mouths.
For a belts going east: the most belts that can go south through the same junction.

    python tools/routing/crossing.py h w A [secs]"""
import sys
sys.path.insert(0, __import__('os').path.dirname(__file__))
from region import Region  # noqa: E402


def plus(h, w, A):
    cells = {(x, y) for y in range(h) for x in range(-A, w + A)} | {(x, y) for x in range(w) for y in range(-A, h + A)}
    mouths = {'W': {(-A - 1, y) for y in range(h)}, 'E': {(w + A, y) for y in range(h)},
              'N': {(x, -A - 1) for x in range(w)}, 'S': {(x, h + A) for x in range(w)}}
    return Region(cells, mouths, ncls=2)


def best(h, w, A, a, secs=300):
    R = plus(h, w, A)
    m = R.m
    m.Add(R.flux_in('W', 0) == a)
    m.Add(R.flux_in('E', 0) == -a)
    m.Add(R.flux_in('N', 0) == 0)
    m.Add(R.flux_in('S', 0) == 0)
    m.Add(R.flux_in('W', 1) == 0)
    m.Add(R.flux_in('E', 1) == 0)
    b = R.flux_in('N', 1)
    m.Add(R.flux_in('S', 1) == -b)
    st, rows, obj, opt = R.solve(secs, maximize=b)
    return st, obj, opt, rows


if __name__ == '__main__':
    h, w, A = [int(v) for v in sys.argv[1:4]]
    secs = float(sys.argv[4]) if len(sys.argv) > 4 else 300
    for a in range(0, 2 * h + 1):
        st, obj, opt, rows = best(h, w, A, a, secs)
        print('cross h %d w %d arms %d: %d east -> %s south (%s%s)' % (h, w, A, a, None if obj is None else int(obj), st, '' if opt else ', not proven best'), flush=True)
        if st == 'INFEASIBLE':
            break
