"""Behaviour cloning warm start: record the scripted planner, fit the PPO policy to it (masked cross-entropy), save
params + obs normalisation for `python -m rl.ppo --init`. usage: python -m rl.bc --out runs/bc.pkl"""
import argparse, pickle

import jax
import jax.numpy as jnp
import numpy as np
import optax

from .baseline import run
from .ppo import make, net


def collect(worlds=('2CCJhI', '8QzA', '4RNkoS'), horizons=(6.0, 10.0), **kw):
    import rl.baseline as B
    data = []
    for w in worlds:
        for h in horizons:
            rec = []
            orig = B.plan_action
            B.plan_action = lambda env, b, _o=orig, _h=h: _o(env, b, horizon_min=_h)
            try:
                env, _ = run(w, 'first_et', record=rec, max_steps=10 ** 9, **kw)
            finally:
                B.plan_action = orig
            print(w, h, len(rec), 'steps, techs', int(env.done_t[0].sum()), flush=True)
            data += rec
    return data


def fit(data, epochs=30, lr=1e-3, seed=0):
    obs = np.stack([d[0] for d in data]); mask = np.stack([d[1] for d in data]); act = np.array([d[2] for d in data])
    mean, var = obs.mean(0), obs.var(0) + 1e-8
    o = ((obs - mean) / np.sqrt(var)).clip(-10, 10).astype(np.float32)
    params = make(o.shape[1], jax.random.PRNGKey(seed))
    opt = optax.adam(lr); st = opt.init(params)

    def loss(p, o, m, a):
        logits, _ = net(p, o, m)
        return -jax.nn.log_softmax(logits)[jnp.arange(a.shape[0]), a].mean()

    @jax.jit
    def step(p, st, o, m, a):
        l, g = jax.value_and_grad(loss)(p, o, m, a)
        u, st = opt.update(g, st, p)
        return optax.apply_updates(p, u), st, l

    n = len(act)
    for ep in range(epochs):
        perm = np.random.permutation(n); ls = []
        for mb in np.array_split(perm, max(1, n // 1024)):
            params, st, l = step(params, st, o[mb], mask[mb], act[mb]); ls.append(float(l))
        logits, _ = net(params, o, mask)
        acc = float((np.asarray(logits).argmax(1) == act).mean())
        print('epoch %d loss %.3f acc %.3f' % (ep, np.mean(ls), acc), flush=True)
    return params, mean, var


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='runs/bc.pkl')
    ap.add_argument('--minutes', type=float, default=900)
    a = ap.parse_args()
    data = collect(max_minutes=a.minutes)
    params, mean, var = fit(data)
    pickle.dump(dict(params=jax.tree_util.tree_map(np.asarray, params), mean=mean, var=var), open(a.out, 'wb'))
