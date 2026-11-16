"""PPO for EconEnv (JAX policy, NumPy env), same recipe as Factoriax's baseline: shared MLP 256x256, masked
discrete actions, GAE. usage: python -m rl.ppo --steps 2e6 --envs 256 [--out runs/econ]"""
import argparse, json, os, time

import jax
import jax.numpy as jnp
import numpy as np
import optax

from .econ import NA, EconEnv
from . import macro as MACRO


def init(key, sizes):
    ps = []
    for a, b in zip(sizes[:-1], sizes[1:]):
        key, k = jax.random.split(key)
        ps.append((jax.random.normal(k, (a, b)) * np.sqrt(2.0 / a), jnp.zeros(b)))
    return ps


def mlp(ps, x):
    for w, b in ps[:-1]:
        x = jnp.tanh(x @ w + b)
    return x @ ps[-1][0] + ps[-1][1]


def net(params, obs, mask):
    h = obs
    for w, b in params['trunk']:
        h = jnp.tanh(h @ w + b)
    logits = h @ params['pi'][0] + params['pi'][1]
    logits = jnp.where(mask, logits, -1e9)
    v = (h @ params['v'][0] + params['v'][1])[:, 0]
    return logits, v


def make(obs_dim, key, hidden=256, na=NA):
    k1, k2, k3 = jax.random.split(key, 3)
    tr = init(k1, [obs_dim, hidden, hidden])
    pi = init(k2, [hidden, na])[0]
    pi = (pi[0] * 0.01, pi[1])
    v = init(k3, [hidden, 1])[0]
    return {'trunk': tr, 'pi': pi, 'v': v}


@jax.jit
def act(params, obs, mask, key):
    logits, v = net(params, obs, mask)
    a = jax.random.categorical(key, logits)
    logp = jax.nn.log_softmax(logits)[jnp.arange(a.shape[0]), a]
    return a, logp, v


def loss_fn(params, batch, clip=0.2, vcoef=0.5, ent=0.01):
    obs, mask, a, logp0, adv, ret = batch
    logits, v = net(params, obs, mask)
    lp = jax.nn.log_softmax(logits)
    logp = lp[jnp.arange(a.shape[0]), a]
    ratio = jnp.exp(logp - logp0)
    adv = (adv - adv.mean()) / (adv.std() + 1e-8)
    pg = -jnp.minimum(ratio * adv, jnp.clip(ratio, 1 - clip, 1 + clip) * adv).mean()
    vl = ((v - ret) ** 2).mean()
    p = jnp.exp(lp)
    entropy = -(jnp.where(mask, p * lp, 0)).sum(-1).mean()
    return pg + vcoef * vl - ent * entropy, (pg, vl, entropy)


def train(steps=2_000_000, envs=256, rollout=128, gamma=0.999, lam=0.95, lr=3e-4, epochs=4, minibatches=8,
          seed=0, out='runs/econ', init=None, ent=0.01, env_kind='econ', **envkw):
    os.makedirs(out, exist_ok=True)
    env = (MACRO.MacroEnv if env_kind == 'macro' else EconEnv)(batch=envs, seed=seed, **envkw)
    obs = env.reset()
    key = jax.random.PRNGKey(seed)
    params = make(obs.shape[1], key, na=MACRO.NM if env_kind == 'macro' else NA)
    mean, var, cnt = np.zeros(obs.shape[1]), np.ones(obs.shape[1]), 1e-4
    if init:
        import pickle
        d = pickle.load(open(init, 'rb'))
        params = jax.tree_util.tree_map(jnp.asarray, d['params'])
        mean, var, cnt = d['mean'], d['var'], 1e6          # keep the cloned policy's normalisation
    opt = optax.chain(optax.clip_by_global_norm(0.5), optax.adam(lr))
    ost = opt.init(params)
    grad = jax.jit(jax.value_and_grad(lambda p, b: loss_fn(p, b, ent=ent), has_aux=True))
    upd = jax.jit(lambda g, s, p: (lambda u, s2: (optax.apply_updates(p, u), s2))(*opt.update(g, s, p)))
    done_log = []
    t0, total, it = time.time(), 0, 0
    ep_ret = np.zeros(envs)
    while total < steps:
        buf = {k: [] for k in ('obs', 'mask', 'a', 'logp', 'v', 'r', 'd')}
        for _ in range(rollout):
            bm, bv, bc = obs.mean(0), obs.var(0), obs.shape[0]          # running obs normalisation
            d = bm - mean; tot = cnt + bc
            mean = mean + d * bc / tot
            var = (var * cnt + bv * bc + d ** 2 * cnt * bc / tot) / tot; cnt = tot
            o = ((obs - mean) / np.sqrt(var + 1e-8)).clip(-10, 10).astype(np.float32)
            mask = env.action_mask()
            key, k = jax.random.split(key)
            a, logp, v = act(params, o, mask, k)
            a = np.asarray(a)
            obs, r, d, info = env.step(a)
            ep_ret += r
            for i in np.nonzero(d)[0]:
                done_log.append(dict(step=total, minutes=float(info['minutes'][i]), finished=bool(info['finished'][i]),
                                     items=int(np.isfinite((env.e if env_kind == 'macro' else env).first[i]).sum()),
                                     target=int((env.e if env_kind == 'macro' else env).target[i]),
                                     techs=int(env.done_t[i].sum()), ret=float(ep_ret[i])))
            ep_ret[d] = 0
            env.reset_where(d)
            obs = env.obs()
            for kk, vv in zip(buf, (o, mask, a, np.asarray(logp), np.asarray(v), r, d)):
                buf[kk].append(vv)
            total += envs
        o = ((obs - mean) / np.sqrt(var + 1e-8)).clip(-10, 10).astype(np.float32)
        _, _, last_v = act(params, o, env.action_mask(), key)
        V = np.stack(buf['v']); R = np.stack(buf['r']); D = np.stack(buf['d']).astype(np.float64)
        adv = np.zeros_like(R); g = 0
        nxt = np.asarray(last_v)
        for t in reversed(range(rollout)):
            delta = R[t] + gamma * nxt * (1 - D[t]) - V[t]
            g = delta + gamma * lam * (1 - D[t]) * g
            adv[t] = g; nxt = V[t]
        ret = adv + V
        flat = lambda x: np.asarray(x).reshape(rollout * envs, *np.asarray(x).shape[2:])
        data = [flat(np.stack(buf['obs'])), flat(np.stack(buf['mask'])), flat(np.stack(buf['a'])),
                flat(np.stack(buf['logp'])), flat(adv).astype(np.float32), flat(ret).astype(np.float32)]
        n = rollout * envs
        for _ in range(epochs):
            perm = np.random.permutation(n)
            for mb in np.array_split(perm, minibatches):
                (l, aux), gr = grad(params, [x[mb] for x in data])
                params, ost = upd(gr, ost, params)
        it += 1
        if it % 5 == 0:
            recent = done_log[-50:]
            fin = [e['minutes'] for e in recent if e['finished']]
            print('%9d steps %6.0f sps | eps %d | items %.1f techs %.1f | finished %d/%d best %.1f min | ent %.2f' % (
                total, total / (time.time() - t0), len(done_log),
                np.mean([e['items'] for e in recent]) if recent else 0, np.mean([e['techs'] for e in recent]) if recent else 0,
                len(fin), len(recent), min(fin) if fin else -1, float(aux[2])), flush=True)
            json.dump(done_log, open(os.path.join(out, 'episodes.json'), 'w'))
    return params, done_log


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--steps', type=float, default=2e6)
    ap.add_argument('--envs', type=int, default=256)
    ap.add_argument('--out', default='runs/econ')
    ap.add_argument('--max-minutes', type=float, default=600)
    ap.add_argument('--goal', default='first_et')
    ap.add_argument('--init', default=None)
    ap.add_argument('--ent', type=float, default=0.01)
    ap.add_argument('--lr', type=float, default=3e-4)
    ap.add_argument('--max-steps', type=int, default=10000)
    ap.add_argument('--env', default='econ')
    ap.add_argument('--rollout', type=int, default=128)
    ap.add_argument('--targets', default=None, help='comma list of item names for per-item speedruns')
    ap.add_argument('--milestone', type=float, default=1.0)
    ap.add_argument('--tech-bonus', type=float, default=0.2)
    a = ap.parse_args()
    train(int(a.steps), a.envs, rollout=a.rollout, out=a.out, max_minutes=a.max_minutes,
          **({} if a.targets else dict(goal=a.goal)), init=a.init, ent=a.ent, lr=a.lr,
          max_steps=a.max_steps, env_kind=a.env, milestone_bonus=a.milestone, tech_bonus=a.tech_bonus,
          **(dict(goal='items', targets=a.targets.split(',')) if a.targets else {}))
