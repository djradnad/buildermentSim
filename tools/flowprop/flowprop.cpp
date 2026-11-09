// flowprop: CaDiCaL with a native flow propagator for Builderment layout skeletons (see bmsolver/lazy.py).
//
// The skeleton CNF holds every pure-Boolean rule of the layout model. This program adds, through CaDiCaL's
// external propagator interface, a necessary flow condition checked on PARTIAL assignments: with every link that
// is not yet ruled out at capacity D, and every port option not yet ruled out active, the inputs must be able to
// send each commodity's full amount to the outputs (max flow >= need). When not, the false link / port literals
// across the min cut form a conflict clause. Exact splitter shares are checked outside (CP-SAT, by the Python
// driver) on finished layouts and come back as clauses over stdin.
//
//   flowprop skeleton.cnf graph.txt
//   graph.txt:  "D <D> <ncom> <ncells>", "N <need_0> ... <need_nc-1>", "L <u> <v> <var>",
//               "F <cell> <0=in|1=out> <vec...>", "O <var> <cell> <0|1> <vec...>"
//   stdout: "SAT <v1> <v2> ... 0" (true variables) then waits for a stdin line "OK" or "C <lits> 0";
//           "UNSAT"; stats on stderr.
#include "cadical.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

struct Edge { int to, rev; long long cap; };

struct MaxFlow {
  int n = 0;
  std::vector<std::vector<Edge>> g;
  std::vector<int> lvl, it;
  void reset (int n_) {
    n = n_;
    g.assign (n, {});
  }
  void add (int u, int v, long long c) {
    g[u].push_back ({v, (int) g[v].size (), c});
    g[v].push_back ({u, (int) g[u].size () - 1, 0});
  }
  bool bfs (int s, int t) {
    lvl.assign (n, -1);
    std::vector<int> q{s};
    lvl[s] = 0;
    for (size_t i = 0; i < q.size (); i++)
      for (auto &e : g[q[i]])
        if (e.cap > 0 && lvl[e.to] < 0) lvl[e.to] = lvl[q[i]] + 1, q.push_back (e.to);
    return lvl[t] >= 0;
  }
  long long dfs (int u, int t, long long f) {
    if (u == t) return f;
    for (int &i = it[u]; i < (int) g[u].size (); i++) {
      Edge &e = g[u][i];
      if (e.cap > 0 && lvl[e.to] == lvl[u] + 1) {
        long long d = dfs (e.to, t, std::min (f, e.cap));
        if (d) {
          e.cap -= d;
          g[e.to][e.rev].cap += d;
          return d;
        }
      }
    }
    return 0;
  }
  long long run (int s, int t, long long limit) {
    long long flow = 0;
    while (flow < limit && bfs (s, t)) {
      it.assign (n, 0);
      while (long long f = dfs (s, t, limit - flow)) flow += f;
    }
    bfs (s, t); // lvl >= 0 marks the source side of a min cut
    return flow;
  }
};

struct Port { int var, cell, kind; std::vector<long long> vec; };  // var 0 = fixed
struct Link { int u, v, var; };

struct FlowProp : CaDiCaL::ExternalPropagator {
  long long D = 0;
  int nc = 0, ncells = 0;
  std::vector<long long> need;
  std::vector<Link> links;
  std::vector<Port> ports;
  std::vector<signed char> val;            // by variable: 0 unassigned, 1 true, -1 false
  std::vector<std::vector<int>> trail{{}}; // assigned observed vars per decision level
  bool dirty = true;
  std::vector<int> pending;
  size_t pend_pos = 0;
  long long checks = 0, conflicts = 0, clause_lits = 0;
  MaxFlow mf;

  int value (int v) const { return v ? val[v] : 1; }

  // returns true if fine; else fills pending with a conflict clause
  bool check () {
    int S = ncells, T = ncells + 1;
    std::vector<int> commodities;
    for (int c = 0; c < nc; c++) commodities.push_back (c);
    if (nc > 1) commodities.push_back (-1);
    for (int c : commodities) {
      auto amt = [&] (const std::vector<long long> &v) {
        if (c >= 0) return v[c];
        long long s = 0;
        for (auto x : v) s += x;
        return s;
      };
      long long want = 0;
      if (c >= 0) want = need[c];
      else for (auto x : need) want += x;
      if (!want) continue;
      mf.reset (ncells + 2);
      for (auto &l : links)
        if (val[l.var] >= 0) mf.add (l.u, l.v, D);
      for (auto &p : ports) {
        long long a = amt (p.vec);
        if (!a || value (p.var) < 0) continue;
        if (p.kind == 0) mf.add (S, p.cell, a);
        else mf.add (p.cell, T, a);
      }
      checks++;
      long long f = mf.run (S, T, want);
      if (f < want) {
        auto &side = mf.lvl;
        pending.clear ();
        for (auto &l : links)
          if (val[l.var] < 0 && side[l.u] >= 0 && side[l.v] < 0) pending.push_back (l.var);
        for (auto &p : ports) {
          if (!p.var || value (p.var) >= 0 || !amt (p.vec)) continue;
          bool crosses = p.kind == 0 ? side[p.cell] < 0 : side[p.cell] >= 0;
          if (crosses) pending.push_back (p.var);
        }
        pending.push_back (0);
        pend_pos = 0;
        conflicts++;
        clause_lits += pending.size () - 1;
        return false;
      }
    }
    return true;
  }

  void notify_assignment (const std::vector<int> &lits) override {
    for (int l : lits) {
      int v = std::abs (l);
      val[v] = l > 0 ? 1 : -1;
      trail.back ().push_back (v);
      if (l < 0) dirty = true;
    }
  }
  void notify_new_decision_level () override { trail.push_back ({}); }
  void notify_backtrack (size_t new_level) override {
    while (trail.size () > new_level + 1) {
      for (int v : trail.back ()) val[v] = 0;
      trail.pop_back ();
    }
  }
  bool cb_check_found_model (const std::vector<int> &model) override {
    for (int l : model) val[std::abs (l)] = l > 0 ? 1 : -1;
    return check ();
  }
  int cb_propagate () override {
    if (dirty && pending.empty ()) {
      dirty = false;
      check ();
    }
    return 0;
  }
  int cb_add_reason_clause_lit (int) override { return 0; }
  bool cb_has_external_clause (bool &forgettable) override {
    forgettable = false;
    return !pending.empty ();
  }
  int cb_add_external_clause_lit () override {
    int l = pending[pend_pos++];
    if (!l) pending.clear ();
    return l;
  }
};

int main (int argc, char **argv) {
  if (argc < 3) {
    fprintf (stderr, "usage: flowprop skeleton.cnf graph.txt [--noprop]\n");
    return 1;
  }
  bool prop = !(argc > 3 && !strcmp (argv[3], "--noprop"));
  CaDiCaL::Solver solver;
  solver.set ("quiet", 1);
  int nv = 0;
  const char *err = solver.read_dimacs (argv[1], nv, 1);
  if (err) {
    fprintf (stderr, "flowprop: %s\n", err);
    return 1;
  }
  FlowProp fp;
  fp.val.assign (nv + 1, 0);
  std::ifstream g (argv[2]);
  std::string line;
  std::vector<int> observed;
  while (std::getline (g, line)) {
    std::istringstream ss (line);
    std::string t;
    ss >> t;
    if (t == "D") ss >> fp.D >> fp.nc >> fp.ncells;
    else if (t == "N") {
      fp.need.resize (fp.nc);
      for (auto &x : fp.need) ss >> x;
    } else if (t == "L") {
      Link l;
      ss >> l.u >> l.v >> l.var;
      fp.links.push_back (l);
      observed.push_back (l.var);
    } else if (t == "F" || t == "O") {
      Port p;
      p.var = 0;
      if (t == "O") ss >> p.var;
      ss >> p.cell >> p.kind;
      p.vec.resize (fp.nc);
      for (auto &x : p.vec) ss >> x;
      fp.ports.push_back (p);
      if (p.var) observed.push_back (p.var);
    }
  }
  if (prop) {
    solver.connect_external_propagator (&fp);
    for (int v : observed) solver.add_observed_var (v);
  }
  for (;;) {
    int res = solver.solve ();
    if (res == 20) {
      printf ("UNSAT\n");
      fflush (stdout);
      break;
    }
    if (res != 10) {
      printf ("UNKNOWN\n");
      fflush (stdout);
      break;
    }
    std::string out = "SAT";
    for (int v = 1; v <= nv; v++)
      if (solver.val (v) > 0) out += " " + std::to_string (v);
    printf ("%s 0\n", out.c_str ());
    fflush (stdout);
    if (!std::getline (std::cin, line) || line.rfind ("OK", 0) == 0) break;
    std::istringstream ss (line);
    std::string t;
    ss >> t;
    int l;
    while (ss >> l) solver.add (l);
  }
  fprintf (stderr, "flowprop: checks %lld conflicts %lld avg clause %.1f\n", fp.checks, fp.conflicts,
           fp.conflicts ? (double) fp.clause_lits / fp.conflicts : 0.0);
  return 0;
}
