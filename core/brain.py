"""Vista en vivo del cerebro: grafo de recuerdos y conceptos (/brain)."""

import json

from core.db import connect

MAX_NODES = 300
SIMILAR_MAX_DISTANCE = 0.85
SIMILAR_PER_NODE = 3
KNN_POOL = 12  # vecinos de trozo a revisar para encontrar 3 eventos distintos


def graph() -> dict:
    with connect() as conn:
        events = conn.execute(
            "SELECT e.id, e.kind, e.filename, e.status, e.error, e.created_at,"
            " (SELECT COUNT(*) FROM chunks c WHERE c.event_id = e.id AND c.position >= 0) AS trozos,"
            " d.summary, d.concepts"
            " FROM events e LEFT JOIN digests d ON d.event_id = e.id AND d.status = 'done'"
            " ORDER BY e.created_at DESC LIMIT ?",
            (MAX_NODES,),
        ).fetchall()

        # Los más recientes primero; cada evento entra con sus conceptos hasta llenar el cupo
        nodes: list[dict] = []
        concept_nodes: dict[str, dict] = {}
        links: list[dict] = []
        for e in events:
            concepts = json.loads(e["concepts"] or "[]")
            new = [c for c in concepts if f"c:{c.lower()}" not in concept_nodes]
            if len(nodes) + len(concept_nodes) + 1 + len(new) > MAX_NODES:
                break
            nodes.append(
                {
                    "id": e["id"],
                    "type": e["kind"],
                    "label": e["filename"],
                    "status": e["status"],
                    "error": e["error"] if e["status"] != "processed" else None,
                    "created_at": e["created_at"],
                    "trozos": e["trozos"],
                    "summary": e["summary"],
                }
            )
            for c in concepts:
                cid = f"c:{c.lower()}"
                if cid not in concept_nodes:
                    concept_nodes[cid] = {"id": cid, "type": "concept", "label": c}
                links.append({"source": e["id"], "target": cid, "kind": "concepto"})

        event_ids = {n["id"] for n in nodes}
        first_chunks = conn.execute(
            "SELECT c.event_id, c.id FROM chunks c WHERE c.position = 0"
        ).fetchall()
        seen: set[tuple[str, str]] = set()
        for fc in first_chunks:
            if fc["event_id"] not in event_ids:
                continue
            vec = conn.execute(
                "SELECT embedding FROM vec_chunks WHERE rowid = ?", (fc["id"],)
            ).fetchone()
            if not vec:
                continue
            neighbours = conn.execute(
                "SELECT v.distance, c.event_id FROM vec_chunks v"
                " JOIN chunks c ON c.id = v.rowid"
                " WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
                (vec["embedding"], KNN_POOL),
            ).fetchall()
            added: set[str] = set()
            for nb in neighbours:
                other = nb["event_id"]
                if len(added) >= SIMILAR_PER_NODE or nb["distance"] >= SIMILAR_MAX_DISTANCE:
                    break
                if other == fc["event_id"] or other not in event_ids or other in added:
                    continue
                added.add(other)
                key = tuple(sorted((fc["event_id"], other)))
                if key in seen:
                    continue
                seen.add(key)
                links.append(
                    {
                        "source": key[0],
                        "target": key[1],
                        "kind": "parecido",
                        "distance": round(nb["distance"], 4),
                    }
                )

    return {"nodes": nodes + list(concept_nodes.values()), "links": links}


PAGE = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEXUS · cerebro</title>
<style>
  :root{--bg:#0f1220;--card:#181c33;--txt:#e8eaf6;--mut:#8f96b8;--acc:#6c7bff}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);font-family:system-ui,sans-serif;overflow:hidden}
  header{position:fixed;top:0;left:0;right:0;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 16px;background:rgba(15,18,32,.85);border-bottom:1px solid #232850;z-index:2}
  header a{color:var(--acc);text-decoration:none;font-size:14px}
  #status{font-size:12px;color:var(--mut)}
  #status.on::before{content:"● ";color:#4caf50}
  #status.off::before{content:"● ";color:#ff7043}
  button{padding:6px 12px;border-radius:8px;border:0;background:var(--card);color:var(--txt);cursor:pointer;font-size:13px}
  canvas{display:block;width:100vw;height:100vh;cursor:grab}
  #legend{position:fixed;left:12px;bottom:12px;background:rgba(24,28,51,.9);padding:10px 12px;border-radius:10px;font-size:12px;line-height:1.8;z-index:2}
  #legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
  #tip{position:fixed;pointer-events:none;max-width:340px;background:rgba(24,28,51,.97);border:1px solid #2d3360;border-radius:10px;padding:10px 12px;font-size:12px;line-height:1.45;z-index:3}
  #tip b{font-size:13px}
  #tip .m{color:var(--mut)}
  #toast{position:fixed;right:12px;bottom:12px;background:rgba(24,28,51,.95);padding:8px 12px;border-radius:10px;font-size:12px;color:var(--mut);z-index:2;transition:opacity .6s}
  #sel{position:fixed;right:12px;top:58px;max-width:340px;background:rgba(24,28,51,.97);border:1px solid #2d3360;border-radius:10px;padding:10px 12px;font-size:12px;line-height:1.45;z-index:3}
  #sel .err{color:#ffab91;margin-top:6px;white-space:pre-wrap}
  #sel button{margin-top:8px;background:#2d3360}
  #empty{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;color:var(--mut);pointer-events:none}
</style></head><body>
<header>
  <div><b>NEXUS</b> · cerebro en vivo <span id="status" class="off">conectando…</span></div>
  <div><button id="pause">⏸ Pausa</button> &nbsp; <a href="/">Inicio</a> · <a href="/dashboard">Panel</a></div>
</header>
<canvas id="c"></canvas>
<div id="legend"></div>
<div id="tip" hidden></div>
<div id="sel" hidden></div>
<div id="toast" hidden></div>
<div id="empty" hidden>Aún no hay recuerdos. Enséñame algo y aparecerá aquí.</div>
<script>
const COLORS = {text:'#6c7bff', document:'#26a69a', audio:'#ffa726', image:'#66bb6a', video:'#ab47bc', concept:'#ec407a', other:'#8f96b8'};
const NAMES = {text:'Texto', document:'Documento', audio:'Audio', image:'Imagen', video:'Video', concept:'Concepto', other:'Otro'};
const RETRY = ['failed', 'unsupported', 'empty'];
const canvas = document.getElementById('c'), ctx = canvas.getContext('2d');
const tip = document.getElementById('tip'), statusEl = document.getElementById('status');
let W = 0, H = 0, DPR = 1;
let nodes = [], links = [], byId = new Map();
let alpha = 1, paused = false, running = false;
let view = {x: 0, y: 0, k: 1};
let hover = null, drag = null;

document.getElementById('legend').innerHTML = ['text','document','audio','image','video','concept']
  .map(t => '<div><i style="background:' + COLORS[t] + '"></i>' + NAMES[t] + '</div>').join('') +
  '<div class="m" style="color:var(--mut)">Tamaño = nº de trozos · tenue = con aviso (clic para ver)</div>';

function color(n) { return COLORS[n.type] || COLORS.other; }
function radius(n) {
  if (n.type === 'concept') return 4;
  return Math.min(4 + Math.sqrt(n.trozos || 0) * 2.5, 18);
}
const now = () => performance.now();

// ---------- datos ----------
function addNode(d, animate) {
  let n = byId.get(d.id);
  if (n) { Object.assign(n, d); return n; }
  const a = Math.random() * Math.PI * 2, r = 40 + Math.random() * 80;
  // Cerca de un vecino conocido si lo hay, si no cerca del centro
  n = Object.assign({x: Math.cos(a) * r, y: Math.sin(a) * r, vx: 0, vy: 0, born: animate ? now() : 0}, d);
  nodes.push(n); byId.set(n.id, n);
  return n;
}
function addLink(l) {
  const s = byId.get(l.source), t = byId.get(l.target);
  if (!s || !t) return;
  if (links.some(x => (x.s === s && x.t === t) || (x.s === t && x.t === s))) return;
  links.push({s, t, kind: l.kind});
}
function placeNear(n, other) {
  if (!other) return;
  n.x = other.x + (Math.random() - .5) * 40;
  n.y = other.y + (Math.random() - .5) * 40;
}

async function loadGraph() {
  const r = await fetch('/brain/graph');
  if (!r.ok) { toast('No pude cargar el cerebro (error ' + r.status + ')'); return; }
  const g = await r.json();
  const first = nodes.length === 0;
  g.nodes.forEach(d => addNode(d, false));
  g.links.forEach(addLink);
  if (first) { alpha = 1; }
  document.getElementById('empty').hidden = nodes.length > 0;
  wake(0.5);
}

// ---------- simulación de fuerzas ----------
function step() {
  const n = nodes.length;
  for (let i = 0; i < n; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < n; j++) {
      const b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy;
      if (d2 > 90000) continue;  // sin efecto más allá de 300 px
      if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1; }
      const f = 900 * alpha / d2;
      a.vx -= dx * f; a.vy -= dy * f; b.vx += dx * f; b.vy += dy * f;
    }
  }
  for (const l of links) {
    const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
    const rest = l.kind === 'concepto' ? 45 : 70;
    const f = (d - rest) / d * 0.04 * alpha;
    l.s.vx += dx * f; l.s.vy += dy * f; l.t.vx -= dx * f; l.t.vy -= dy * f;
  }
  for (const a of nodes) {
    a.vx -= a.x * 0.004 * alpha; a.vy -= a.y * 0.004 * alpha;  // gravedad al centro
    if (a === drag) { a.vx = a.vy = 0; continue; }
    a.vx *= 0.6; a.vy *= 0.6;
    a.x += a.vx; a.y += a.vy;
  }
  alpha *= 0.985;
}

// ---------- animaciones (solo eventos reales) ----------
const effects = [];  // {node, kind, start, dur}
function effect(node, kind, dur, delay) {
  if (!node) return;
  effects.push({node, kind, start: now() + (delay || 0), dur});
  wake(0);
}

function draw() {
  const t = now();
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.translate(W / 2 + view.x, H / 2 + view.y);
  ctx.scale(view.k, view.k);

  for (const l of links) {
    ctx.strokeStyle = l.kind === 'concepto' ? 'rgba(236,64,122,.18)' : 'rgba(143,150,184,.28)';
    ctx.lineWidth = 1 / view.k;
    ctx.beginPath(); ctx.moveTo(l.s.x, l.s.y); ctx.lineTo(l.t.x, l.t.y); ctx.stroke();
  }

  for (const n of nodes) {
    let r = radius(n), a = 1;
    if (n.born) {  // aparición al ingresar
      const p = Math.min((t - n.born) / 700, 1);
      r *= 0.3 + 0.7 * (1 - Math.pow(1 - p, 3));
      a = p;
      if (p >= 1) n.born = 0;
    }
    ctx.globalAlpha = RETRY.includes(n.status) ? 0.35 * a : a;
    ctx.fillStyle = color(n);
    ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill();
    if (n.processing) {  // procesándose: anillo punteado
      ctx.strokeStyle = color(n); ctx.lineWidth = 1.5 / view.k;
      ctx.setLineDash([3, 3]); ctx.lineDashOffset = -t / 60;
      ctx.beginPath(); ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
    }
    if (n.type === 'concept' && view.k > 0.7) {
      ctx.globalAlpha = 0.75 * a; ctx.fillStyle = '#e8eaf6';
      ctx.font = (10 / view.k) + 'px system-ui'; ctx.fillText(n.label, n.x + 6, n.y + 3);
    }
  }
  ctx.globalAlpha = 1;

  for (let i = effects.length - 1; i >= 0; i--) {
    const e = effects[i], p = (t - e.start) / e.dur;
    if (p < 0) continue;
    if (p >= 1) { effects.splice(i, 1); continue; }
    const n = e.node, r = radius(n);
    if (e.kind === 'pulse') {  // procesado: onda que se expande
      ctx.strokeStyle = color(n); ctx.globalAlpha = 1 - p; ctx.lineWidth = 2 / view.k;
      ctx.beginPath(); ctx.arc(n.x, n.y, r + p * 28, 0, Math.PI * 2); ctx.stroke();
    } else if (e.kind === 'recall') {  // recordado: brillo
      const g = ctx.createRadialGradient(n.x, n.y, r, n.x, n.y, r + 26);
      const k = p < 0.15 ? p / 0.15 : 1 - (p - 0.15) / 0.85;
      g.addColorStop(0, 'rgba(255,241,118,' + (0.9 * k) + ')');
      g.addColorStop(1, 'rgba(255,241,118,0)');
      ctx.globalAlpha = 1; ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(n.x, n.y, r + 26, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = 'rgba(255,248,200,' + k + ')';
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  if (hover) {
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2 / view.k;
    ctx.beginPath(); ctx.arc(hover.x, hover.y, radius(hover) + 3, 0, Math.PI * 2); ctx.stroke();
  }
}

function busy() {
  return effects.length > 0 || nodes.some(n => n.born || n.processing);
}
function frame() {
  if (!paused && alpha > 0.004) step();
  draw();
  // Bajo consumo: el bucle se detiene cuando todo está quieto
  if ((!paused && alpha > 0.004) || busy() || drag) requestAnimationFrame(frame);
  else running = false;
}
function wake(heat) {
  if (heat) alpha = Math.max(alpha, heat);
  if (!running) { running = true; requestAnimationFrame(frame); }
}

// ---------- eventos en vivo (SSE) ----------
let es = null, retry = 1000, needResync = false;
function connect() {
  es = new EventSource('/brain/stream');
  es.onopen = () => {
    statusEl.textContent = 'en vivo'; statusEl.className = 'on'; retry = 1000;
    // Tras una reconexión, sincroniza lo que pasó mientras tanto (sin animarlo)
    if (needResync) loadGraph().catch(() => {});
    needResync = true;
  };
  es.onmessage = m => { try { handle(JSON.parse(m.data)); } catch (e) {} };
  es.onerror = () => {
    statusEl.textContent = 'reconectando…'; statusEl.className = 'off';
    es.close();
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 30000);
  };
}

let resyncTimer = null;
function resyncSoon() {  // trae aristas de parecido nuevas tras procesar
  clearTimeout(resyncTimer);
  resyncTimer = setTimeout(() => loadGraph().catch(() => {}), 1500);
}

function handle(ev) {
  const n = ev.id ? byId.get(ev.id) : null;
  switch (ev.tipo) {
    case 'ingest': {
      const nn = addNode({id: ev.id, type: ev.kind, label: ev.filename, status: 'pending', trozos: 0}, true);
      document.getElementById('empty').hidden = true;
      wake(0.4);
      toast('Nuevo: ' + ev.filename);
      break;
    }
    case 'processing':
      if (n) { n.processing = true; wake(0); }
      break;
    case 'processed':
      if (n) { n.processing = false; n.status = 'processed'; n.trozos = ev.trozos; effect(n, 'pulse', 1200); wake(0.2); }
      resyncSoon();
      break;
    case 'processing_error':
      if (n) {
        n.processing = false; n.status = ev.estado; n.error = ev.error; wake(0);
        if (RETRY.includes(ev.estado)) toast(n.label + ': ' + (ev.error || ev.estado) + ' (clic en el punto para reintentar)');
      }
      break;
    case 'digested':
      if (n) {
        n.processing = false;
        for (const c of ev.conceptos || []) {
          const id = 'c:' + c.toLowerCase();
          const fresh = !byId.has(id);
          const cn = addNode({id, type: 'concept', label: c}, fresh);
          if (fresh) placeNear(cn, n);
          addLink({source: n.id, target: id, kind: 'concepto'});
        }
        effect(n, 'pulse', 1200); wake(0.3);
        resyncSoon();  // para traer el resumen al tooltip
      }
      break;
    case 'recall':
      (ev.ids || []).forEach((id, i) => effect(byId.get(id), 'recall', 2600, i * 280));
      toast((ev.origen === 'ask' ? 'Pregunta' : 'Búsqueda') + ': ' + ev.ids.length + ' recuerdo(s) usados');
      break;
    case 'digest_progress':
      if (n) { n.processing = true; wake(0); }
      if (ev.paso === 'secciones' && ev.total > 1)
        toast('Digiriendo ' + (n ? n.label : '') + ': sección ' + Math.min(ev.hecho + 1, ev.total) + ' de ' + ev.total);
      break;
    case 'backup':
      toast('Respaldo hecho' + (ev.eventos != null ? ' (' + ev.eventos + ' eventos)' : ''));
      break;
  }
}

let toastTimer = null;
function toast(text) {
  const el = document.getElementById('toast');
  el.textContent = text; el.hidden = false; el.style.opacity = 1;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.style.opacity = 0; setTimeout(() => el.hidden = true, 700); }, 3500);
}

// ---------- interacción ----------
function toWorld(px, py) {
  return {x: (px - W / 2 - view.x) / view.k, y: (py - H / 2 - view.y) / view.k};
}
function pick(px, py) {
  const p = toWorld(px, py);
  let best = null, bd = Infinity;
  for (const n of nodes) {
    const d = Math.hypot(n.x - p.x, n.y - p.y);
    if (d < radius(n) + 6 / view.k && d < bd) { best = n; bd = d; }
  }
  return best;
}
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function showTip(n, x, y) {
  if (!n) { tip.hidden = true; return; }
  let h = '<b>' + esc(n.label) + '</b><div class="m">' + NAMES[n.type in NAMES ? n.type : 'other'];
  if (n.type !== 'concept') {
    h += ' · ' + (n.trozos || 0) + ' trozo(s) · ' + esc(n.status || '') + '</div>';
    if (n.error) h += '<div style="margin-top:6px;color:#ffab91">' + esc(n.error) + '</div>';
    h += n.summary ? '<div style="margin-top:6px">' + esc(n.summary.length > 320 ? n.summary.slice(0, 320) + '…' : n.summary) + '</div>'
                   : '<div class="m" style="margin-top:6px">Sin resumen</div>';
  } else {
    h += ' · en ' + links.filter(l => l.t === n || l.s === n).length + ' recuerdo(s)</div>';
  }
  tip.innerHTML = h; tip.hidden = false;
  tip.style.left = Math.min(x + 14, innerWidth - 350) + 'px';
  tip.style.top = Math.min(y + 14, innerHeight - tip.offsetHeight - 10) + 'px';
}

// Clic en un recuerdo: detalle fijo, con reintento si tiene aviso
const sel = document.getElementById('sel');
function select(n) {
  if (!n || n.type === 'concept') { sel.hidden = true; return; }
  sel.innerHTML = '<b>' + esc(n.label) + '</b><div class="m">' + NAMES[n.type in NAMES ? n.type : 'other'] +
    ' · ' + esc(n.status || '') + ' · ' + (n.trozos || 0) + ' trozo(s)</div>' +
    (n.error ? '<div class="err">' + esc(n.error) + '</div>' : '') +
    (n.summary ? '<div style="margin-top:6px">' + esc(n.summary) + '</div>' : '');
  if (RETRY.includes(n.status)) {
    const b = document.createElement('button');
    b.textContent = 'Reintentar';
    b.onclick = async () => {
      b.disabled = true;
      try {
        const r = await fetch('/requeue/' + encodeURIComponent(n.id), {method: 'POST'});
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'error ' + r.status);
        n.status = 'pending'; n.error = null; wake(0);
        b.textContent = 'En cola de nuevo';
      } catch (e) { b.textContent = 'No se pudo: ' + e.message; }
    };
    sel.appendChild(b);
  }
  const c = document.createElement('button');
  c.textContent = 'Cerrar'; c.style.marginLeft = '6px'; c.onclick = () => sel.hidden = true;
  sel.appendChild(c);
  sel.hidden = false;
}

let panning = null, downAt = null;
canvas.addEventListener('mousedown', e => {
  downAt = {x: e.clientX, y: e.clientY};
  const n = pick(e.clientX, e.clientY);
  if (n) { drag = n; wake(0.1); } else panning = {x: e.clientX - view.x, y: e.clientY - view.y};
  canvas.style.cursor = 'grabbing';
});
addEventListener('mousemove', e => {
  if (drag) { const p = toWorld(e.clientX, e.clientY); drag.x = p.x; drag.y = p.y; wake(0.1); return; }
  if (panning) { view.x = e.clientX - panning.x; view.y = e.clientY - panning.y; wake(0); return; }
  if (e.target !== canvas) return;
  const n = pick(e.clientX, e.clientY);
  if (n !== hover) { hover = n; wake(0); }
  showTip(n, e.clientX, e.clientY);
});
addEventListener('mouseup', e => {
  if (downAt && e.target === canvas && Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) < 4) select(pick(e.clientX, e.clientY));
  downAt = null;
  drag = null; panning = null; canvas.style.cursor = 'grab'; });
canvas.addEventListener('mouseleave', () => { if (!drag) { hover = null; tip.hidden = true; wake(0); } });
canvas.addEventListener('wheel', e => {
  e.preventDefault();
  const k = Math.min(Math.max(view.k * Math.exp(-e.deltaY * 0.0015), 0.2), 4);
  const p = toWorld(e.clientX, e.clientY);
  view.k = k;
  view.x = e.clientX - W / 2 - p.x * k; view.y = e.clientY - H / 2 - p.y * k;
  wake(0);
}, {passive: false});

document.getElementById('pause').addEventListener('click', e => {
  paused = !paused;
  e.target.textContent = paused ? '▶ Seguir' : '⏸ Pausa';
  if (!paused) wake(0.3);
});

function resize() {
  DPR = window.devicePixelRatio || 1; W = innerWidth; H = innerHeight;
  canvas.width = W * DPR; canvas.height = H * DPR;
  wake(0);
}
addEventListener('resize', resize);
document.addEventListener('visibilitychange', () => { if (!document.hidden) wake(0); });

resize();
loadGraph().catch(() => { document.getElementById('empty').hidden = false; });
connect();
</script></body></html>"""
