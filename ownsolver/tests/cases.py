"""Known-answer cases (pinned ports, E inputs, W outputs unless noted). Each case: name -> window factory.
Reference answers are computed by ref.py with CP-SAT (1 worker) and stored in refs.json."""
from common import balancer_window, mixer32_window


def mid(k, H):
    a = (H - k) // 2
    return list(range(a, a + k))


def cases():
    out = {}
    def add(win):
        out[win.name] = win
    # 1:m splitters
    for m in (2, 3, 4, 5, 6):
        for H in sorted({m, m + 1}):
            for W in range(1, 5):
                add(balancer_window(1, m, W, H, mid(1, H), mid(m, H), mix=False))
    # n:n balancers, mixed (each output gets 1/n of each input), top-aligned and centred ports
    for n in (2, 3, 4):
        for H in range(n, n + 3):
            for W in range(2, 9 if n < 4 else 10):
                add(balancer_window(n, n, W, H, mid(n, H), mid(n, H), mix=True))
                if H > n:
                    add(balancer_window(n, n, W, H, list(range(n)), list(range(n)), mix=True))
    # n:m fans
    for n, m in ((2, 3), (3, 2), (2, 4), (4, 2), (3, 4)):
        for H in range(max(n, m), max(n, m) + 2):
            for W in range(2, 8):
                add(balancer_window(n, m, W, H, mid(n, H), mid(m, H), mix=True))
    # the 3:2 pure-leftover mixer
    for W in range(3, 10):
        add(mixer32_window(W, 4))
    return out
