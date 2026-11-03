"""Live view: a small local web page that shows the best layout so far while the solver runs.

    reporter = LiveView(port=8765)     # prints the address; open it in a browser
    search.find(spec, reporter=reporter)

The page polls the solver every half second: the current layout (drawn as tiles with arrows), every window size
tried so far (proven impossible / ran out of time / found) and a log. Nothing leaves your computer.
"""
import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .search import Reporter, PrintReporter


class LiveView(Reporter):
    def __init__(self, port=8765, open_browser=True, title='Builderment layout solver', echo=True):
        self.state = dict(title=title, design=None, W=0, H=0, note='', sizes=[], log=[], started=time.time(),
                          done=False, version=0)
        self.lock = threading.Lock()
        self.echo = PrintReporter() if echo else None
        view = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith('/state'):
                    with view.lock:
                        body = json.dumps(view.state).encode()
                    ctype = 'application/json'
                else:
                    body, ctype = PAGE.encode(), 'text/html; charset=utf-8'
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Cache-Control', 'no-store')
                self._cors()
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):           # lets the web site (another origin) read state.json
                self.send_response(204)
                self._cors()
                self.end_headers()

            def _cors(self):
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Headers', '*')
                self.send_header('Access-Control-Allow-Private-Network', 'true')

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:%d/' % self.server.server_address[1]
        print('live view: %s' % self.url, flush=True)
        if open_browser:
            try:
                webbrowser.open(self.url)
            except Exception:
                pass

    def _update(self, **kw):
        with self.lock:
            self.state.update(kw)
            self.state['version'] += 1

    def size_result(self, W, H, status, seconds):
        with self.lock:
            self.state['sizes'].append([W, H, status, round(seconds, 1)])
            self.state['version'] += 1
        if self.echo:
            self.echo.size_result(W, H, status, seconds)

    def solution(self, design, W, H, note):
        d = design.normalized()
        self._update(design=[[x, y, c] for (x, y), c in d.tiles.items()], W=W, H=H, note=note, pieces=len(d))
        if self.echo and note.startswith('best'):
            self.echo.solution(design, W, H, note)

    def message(self, text):
        with self.lock:
            self.state['log'].append('%6.1fs  %s' % (time.time() - self.state['started'], text))
            self.state['log'] = self.state['log'][-200:]
            self.state['version'] += 1
        if self.echo:
            self.echo.message(text)

    def finish(self, keep_open=True):
        self._update(done=True)
        if keep_open:
            print('done; the live view stays open until you press Ctrl+C', flush=True)
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
        self.server.shutdown()


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Layout solver</title>
<style>
:root{--bg:#f6f5f2;--fg:#1d1d1b;--mute:#6b6a66;--card:#fff;--line:#d9d7d0;--belt:#e8b04a;--split:#4a8fe8;--ug:#9b6be8;
--ok:#2e9d58;--bad:#d0453a;--unk:#a3a199}
@media (prefers-color-scheme:dark){:root{--bg:#1b1b1a;--fg:#ecebe6;--mute:#9a9893;--card:#262624;--line:#3b3a37}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px;display:grid;gap:16px;grid-template-columns:minmax(0,2fr) minmax(0,1fr)}
@media (max-width:760px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
h1{font-size:18px;margin:0 0 4px} .mute{color:var(--mute)} svg{width:100%;height:auto;display:block}
#sizes{display:flex;flex-wrap:wrap;gap:4px} .sz{font:12px ui-monospace,monospace;padding:2px 6px;border-radius:5px;color:#fff}
pre{font:12px ui-monospace,monospace;white-space:pre-wrap;max-height:300px;overflow:auto;margin:0}
.legend span{display:inline-block;margin-right:10px} .dot{width:10px;height:10px;border-radius:2px;display:inline-block;margin-right:4px;vertical-align:-1px}
</style></head><body><main>
<div class="card"><h1 id="title"></h1><div id="info" class="mute"></div><div id="draw"></div>
<div class="legend mute"><span><i class="dot" style="background:var(--belt)"></i>belt</span><span><i class="dot" style="background:var(--split)"></i>splitter</span><span><i class="dot" style="background:var(--ug)"></i>underground in/out</span></div></div>
<div style="display:grid;gap:16px;align-content:start">
<div class="card"><b>Sizes tried</b> <span class="mute">green found, red impossible, grey out of time</span><div id="sizes"></div></div>
<div class="card"><b>Log</b><pre id="log"></pre></div></div>
</main><script>
const ANG={0:-90,1:180,2:90,3:0};let ver=-1;
function tile(x,y,c){const k=c[0],d=+c[1],cx=x*20+10,cy=y*20+10;
 const col={B:'var(--belt)',S:'var(--split)',I:'var(--ug)',O:'var(--ug)'}[k];
 let s=`<g transform="translate(${cx},${cy}) rotate(${ANG[d]})">`;
 if(k==='B') s+=`<rect x="-9" y="-9" width="18" height="18" rx="3" fill="${col}" opacity=".35"/><path d="M-6,-5 L4,0 L-6,5" fill="none" stroke="${col}" stroke-width="3"/>`;
 if(k==='S') s+=`<rect x="-9" y="-9" width="18" height="18" rx="3" fill="${col}"/><path d="M-3,-4 L3,0 L-3,4 M-4,-8 L0,-4 M-4,8 L0,4" fill="none" stroke="#fff" stroke-width="2"/>`;
 if(k==='I') s+=`<path d="M-9,-9 H2 A9,9 0 0 1 2,9 H-9 Z" fill="${col}"/><path d="M-5,-4 L1,0 L-5,4" fill="none" stroke="#fff" stroke-width="2"/>`;
 if(k==='O') s+=`<path d="M9,-9 H-2 A9,9 0 0 0 -2,9 H9 Z" fill="${col}"/><path d="M-1,-4 L5,0 L-1,4" fill="none" stroke="#fff" stroke-width="2"/>`;
 return s+'</g>';}
async function poll(){try{const r=await fetch('state.json');const s=await r.json();
 if(s.version!==ver){ver=s.version;document.getElementById('title').textContent=s.title;
  document.getElementById('info').textContent=s.design?`${s.W} x ${s.H}, ${s.pieces} pieces, ${s.note}`+(s.done?' (finished)':''):'searching...';
  if(s.design){let W=s.W,H=s.H,g=`<svg viewBox="-2 -2 ${W*20+4} ${H*20+4}"><rect x="0" y="0" width="${W*20}" height="${H*20}" fill="none" stroke="var(--line)" stroke-dasharray="3 3"/>`;
   for(const [x,y,c] of s.design) g+=tile(x,y,c); document.getElementById('draw').innerHTML=g+'</svg>';}
  const C={FOUND:'var(--ok)',INFEASIBLE:'var(--bad)',UNKNOWN:'var(--unk)'};
  document.getElementById('sizes').innerHTML=s.sizes.map(([w,h,st,t])=>`<span class="sz" title="${st} ${t}s" style="background:${C[st]||'var(--unk)'}">${w}x${h}</span>`).join('');
  document.getElementById('log').textContent=s.log.join('\n');}}catch(e){}
 setTimeout(poll,500);}
poll();
</script></body></html>"""
