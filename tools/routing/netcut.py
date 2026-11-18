"""Sound quick filter for a network in an N:N box (step 2 of john's pipeline): forget geometry, keep only which
column every node sits in. Inputs enter east of column W-1, outputs leave west of column 0. Each tile holds at most
one node, so a column holds at most H nodes. Belts that cross a column line use one link there; per row a line has
one surface link and at most one westward and one eastward tunnel (opposite tunnels may overlap), and over any 8
consecutive lines h rows pass at most 13h westward and 19h links in all (channel-capacity proofs, branch jeu4n0:
floor(13h/8) one way, floor(19h/8) both ways). If no column assignment meets these, the network cannot fit.

    python tools/routing/netcut.py --nets ~/data/nets3b.jsonl --W 17 --H 3
"""
import argparse
import json
import os
from ortools.sat.python import cp_model


def edges_of(net):
    """[(from node, to node)] with nodes ('i', k) inputs, ('o', k) outputs, ('s', j), ('m', j)."""
    head, tail = {}, {}
    for k, a in enumerate(net['src']):
        head[a] = ('i', k)
    for k, b in enumerate(net['snk']):
        tail[b] = ('o', k)
    for j, (a, bs) in enumerate(net['split']):
        tail[a] = ('s', j)
        for x in bs:
            head[x] = ('s', j)
    for j, (as_, b) in enumerate(net['merge']):
        head[b] = ('m', j)
        for x in as_:
            tail[x] = ('m', j)
    return [(head[e], tail[e]) for e in range(net['edges'])]


def feasible(net, W, H, timeout=30, workers=1):
    m = cp_model.CpModel()
    E = edges_of(net)
    nodes = sorted({u for e in E for u in e if u[0] in 'sm'})
    x = {u: m.NewIntVar(0, W - 1, '') for u in nodes}
    pos = lambda u: W if u[0] == 'i' else (-1 if u[0] == 'o' else x[u])
    # column occupancy
    at = {}
    for u in nodes:
        for c in range(W):
            b = at[u, c] = m.NewBoolVar('')
            m.Add(x[u] == c).OnlyEnforceIf(b)
            m.Add(x[u] != c).OnlyEnforceIf(b.Not())
        m.AddExactlyOne(at[u, c] for c in range(W))
    for c in range(W):
        m.Add(sum(at[u, c] for u in nodes) <= H)
    # line l lies between column l (west) and column l-1... use: line l separates columns < l from columns >= l,
    # l = 0..W (line 0 is the west edge, line W the east edge)
    west, both = {}, {}
    for l in range(W + 1):
        wl, bl = [], []
        for u, v in E:
            pu, pv = pos(u), pos(v)
            # u east of line (>= l) and v west (< l): westward crossing; the reverse: eastward
            ge = lambda p: m.NewConstant(1) if isinstance(p, int) and p >= l else (
                m.NewConstant(0) if isinstance(p, int) else None)
            def side(p):
                if isinstance(p, int):
                    return 1 if p >= l else 0
                b = m.NewBoolVar('')
                m.Add(p >= l).OnlyEnforceIf(b)
                m.Add(p < l).OnlyEnforceIf(b.Not())
                return b
            su, sv = side(pu), side(pv)
            cw = m.NewBoolVar('')
            ce = m.NewBoolVar('')
            # cw = su and not sv ; ce = sv and not su
            for c, a, b in ((cw, su, sv), (ce, sv, su)):
                if isinstance(a, int) and isinstance(b, int):
                    m.Add(c == (1 if a == 1 and b == 0 else 0))
                    continue
                A = a if not isinstance(a, int) else m.NewConstant(a)
                B = b if not isinstance(b, int) else m.NewConstant(b)
                m.AddBoolAnd([A, B.Not()]).OnlyEnforceIf(c)
                m.AddBoolOr([A.Not(), B, c])
            wl.append(cw)
            bl.append(cw)
            bl.append(ce)
        west[l] = sum(wl)
        both[l] = sum(bl)
        m.Add(west[l] <= 2 * H)
        m.Add(both[l] <= 3 * H)
    for l in range(0, W + 1 - 7):
        m.Add(sum(west[l + i] for i in range(8)) <= 13 * H)
        m.Add(sum(both[l + i] for i in range(8)) <= 19 * H)
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = timeout
    sv.parameters.num_workers = workers
    st = sv.Solve(m)
    return sv.StatusName(st)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nets', required=True)
    ap.add_argument('--W', type=int, required=True)
    ap.add_argument('--H', type=int, required=True)
    a = ap.parse_args()
    cnt = {}
    for l in open(os.path.expanduser(a.nets)):
        d = json.loads(l)
        st = feasible(d['net'], a.W, a.H)
        cnt[st] = cnt.get(st, 0) + 1
    print(cnt)


if __name__ == '__main__':
    main()
