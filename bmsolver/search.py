"""Find the smallest layout for a Spec by trying window sizes in order of size.

Sizes are tried from the smallest up (by area, or by longest side for the most square layout). Each size is solved
with a time limit: INFEASIBLE means proven impossible, UNKNOWN means the time ran out. The first size that works is
the answer; it is proven smallest when every smaller size was proven impossible. Then the pieces are polished:
fewest pieces (belts preferred over splitters and tunnels), then fewest turns.
"""
import time

from . import model
from .check import check
from .design import Design


def metric(W, H, goal):
    return (W * H, max(W, H), H) if goal == 'area' else (max(W, H), W * H, H)


def sizes(spec, goal, max_side=60):
    """All (W, H) the ports allow, smallest first."""
    w0, h0 = spec.min_size()
    out = [(W, H) for W in range(w0, max_side + 1) for H in range(h0, max_side + 1)]
    return sorted(out, key=lambda s: metric(s[0], s[1], goal))


class Reporter:
    """Receives progress events. Subclass and override what you need (see live.py)."""

    def size_result(self, W, H, status, seconds):
        pass

    def solution(self, design, W, H, note):
        pass

    def message(self, text):
        pass


class PrintReporter(Reporter):
    def size_result(self, W, H, status, seconds):
        print('  %dx%d %s (%.1fs)' % (W, H, status.lower(), seconds), flush=True)

    def solution(self, design, W, H, note):
        print('  %dx%d %d pieces %s\n%s' % (W, H, len(design), note, design.to_text()), flush=True)

    def message(self, text):
        print(text, flush=True)


def find(spec, goal='area', symmetry=None, quasi=False, timeout=60, polish_timeout=60, time_limit=None,
         max_side=60, workers=4, reporter=None, known=None):
    """Smallest layout for spec. goal: 'area' or 'square'. symmetry: None, 'y' or 'x'; quasi: the middle row or
    column may break the symmetry. known: {(W, H): status} from earlier runs of the same spec and options, so
    'both' goals don't repeat work. Returns dict(design, W, H, proven, status_by_size, inputs, outputs, report) or
    None when nothing was found."""
    rep = reporter or Reporter()
    t0 = time.time()
    status = dict(known or {})
    all_failed_proven = True
    for W, H in sizes(spec, goal, max_side):
        if time_limit and time.time() - t0 > time_limit:
            rep.message('time limit reached')
            return None
        win = spec.window(W, H, symmetry, quasi)
        if win is None:
            continue
        st = status.get((W, H))
        if st is None or st == 'FOUND':
            def live(grid, obj, sec, W=W, H=H):
                rep.solution(Design(grid), W, H, 'first layout at this size')
            r = model.solve(win, timeout=timeout, workers=workers, on_solution=live)
            st = 'FOUND' if 'grid' in r else r['status']
            status[W, H] = st
            rep.size_result(W, H, st, r['time'])
        else:
            r = None
        if st == 'INFEASIBLE':
            continue
        if st == 'UNKNOWN':
            all_failed_proven = False
            continue
        if r is None:                    # found in an earlier run: solve again to get the layout
            r = model.solve(win, timeout=timeout, workers=workers)
            if 'grid' not in r:
                all_failed_proven = False
                continue
        best = r
        # polish: fewest pieces then fewest turns; the free port positions stay free
        win.hint = dict(r['grid'])
        def live(grid, obj, sec, W=W, H=H):
            rep.solution(Design(grid), W, H, 'polishing')
        r2 = model.solve(win, timeout=polish_timeout, workers=workers, objective=True, turn_weight=1,
                         on_solution=live)
        if 'grid' in r2 and r2['pieces'] <= best['pieces']:
            best = r2
        d = Design(best['grid'])
        ins, outs = spec.ports_used(win, best['options'])
        rep_ = check(d, ins, outs, rates=spec.rates, mix=spec.ncom > 1)
        if not rep_['ok']:
            raise RuntimeError('solver result failed the exact check (a bug, please report): %s' % rep_)
        note = 'proven smallest' if all_failed_proven else 'smaller sizes not all ruled out'
        rep.solution(d, W, H, 'best (%s)' % note)
        return dict(design=d, W=W, H=H, proven=all_failed_proven, status_by_size=status, inputs=ins, outputs=outs,
                    report=rep_, seconds=time.time() - t0)
    return None
