PAGE = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEXUS</title>
<style>
  :root{--bg:#0f1220;--card:#181c33;--txt:#e8eaf6;--mut:#8f96b8;--acc:#6c7bff}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font-family:system-ui,sans-serif;height:100vh;display:flex;flex-direction:column}
  header{display:flex;justify-content:space-between;align-items:center;padding:12px 20px;border-bottom:1px solid #232850}
  header a{color:var(--acc);text-decoration:none;font-size:14px}
  #stats{color:var(--mut);font-size:13px}
  .nube{margin-left:8px;padding:1px 6px;border-radius:6px;background:#d32f2f;color:#fff;font-size:10px;font-weight:700;letter-spacing:.05em;vertical-align:middle}
  #cloud{margin-left:6px;padding:2px 8px;border-radius:6px;background:#d32f2f;color:#fff;font-size:11px;font-weight:700;letter-spacing:.05em}
  #log{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px}
  .msg{max-width:780px;padding:12px 14px;border-radius:12px;white-space:pre-wrap;line-height:1.45}
  .me{align-self:flex-end;background:var(--acc)}
  .bot{align-self:flex-start;background:var(--card)}
  .sys{align-self:center;color:var(--mut);font-size:13px}
  details{margin-top:8px;color:var(--mut);font-size:13px}
  .src{margin:6px 0;padding-left:8px;border-left:2px solid #2d3360}
  form{display:flex;gap:8px;padding:12px 20px;border-top:1px solid #232850}
  input[type=text]{flex:1;padding:12px;border-radius:10px;border:1px solid #2d3360;background:var(--card);color:var(--txt);font-size:15px}
  button,label.btn{padding:12px 14px;border-radius:10px;border:0;background:var(--card);color:var(--txt);cursor:pointer;font-size:14px}
  button.primary{background:var(--acc)}
  button:disabled{opacity:.5;cursor:wait}
  #inboxBtn{padding:4px 10px;font-size:13px;margin-right:8px}
  #inboxBtn.warn{background:#7a3b14}
  #panel{position:fixed;top:49px;right:0;bottom:0;width:min(420px,100vw);background:#13172b;border-left:1px solid #232850;overflow-y:auto;padding:12px 14px;z-index:5}
  #panel h3{margin:4px 0 10px;font-size:15px;display:flex;justify-content:space-between;align-items:center}
  .item{padding:9px 10px;margin-bottom:8px;border-radius:10px;background:var(--card);font-size:13px;line-height:1.4}
  .item .n{font-weight:600;word-break:break-all}
  .item .m{color:var(--mut);font-size:12px}
  .item .err{color:#ffab91;font-size:12px;margin-top:4px;white-space:pre-wrap}
  .item button{padding:5px 10px;font-size:12px;margin-top:6px;background:#2d3360}
  .st{display:inline-block;padding:0 6px;border-radius:6px;font-size:11px;margin-left:4px}
  .st-processed{background:#1b5e20}.st-pending{background:#455a64}.st-procesando{background:#1565c0}
  .st-failed{background:#b71c1c}.st-unsupported{background:#6d4c41}.st-empty{background:#5d4037}
</style></head><body>
<header><div><b>NEXUS</b> <span id="stats"></span><span id="cloud" title="La última respuesta salió de la nube (solo pruebas)" hidden>NUBE</span></div><nav><button type="button" id="inboxBtn" title="Lo que entró y su estado">Bandeja</button><a href="/brain">Cerebro</a> · <a href="/dashboard">Panel</a></nav></header>
<aside id="panel" hidden><h3>Bandeja <button type="button" id="closePanel" title="Cerrar">✕</button></h3><div id="items" class="m">Cargando…</div></aside>
<div id="log"><div class="msg sys">Pregúntame algo, o usa «Recordar» para enseñarme. También puedes arrastrar archivos aquí.</div></div>
<form id="f">
  <label class="btn" title="Adjuntar archivo">📎<input id="file" type="file" hidden></label>
  <button type="button" id="mic" title="Grabar voz">🎙</button>
  <input id="q" type="text" placeholder="Escribe aquí..." autocomplete="off" autofocus>
  <button type="button" id="remember">Recordar</button>
  <button class="primary" id="ask">Preguntar</button>
</form>
<script>
const log = document.getElementById('log'), q = document.getElementById('q');
let pending = 0;

function add(cls, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}
function busy(on) { document.querySelectorAll('form button').forEach(b => b.disabled = on); }

async function ask() {
  const text = q.value.trim();
  if (!text) return;
  q.value = '';
  add('me', text);
  const wait = add('bot', 'Pensando...');
  busy(true);
  try {
    const r = await fetch('/ask', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question: text})});
    if (!r.ok) {
      let msg = 'Error ' + r.status;
      try { msg = (await r.json()).detail || msg; } catch (e) {}
      throw new Error(msg);
    }
    const data = await r.json();
    wait.textContent = data.answer;
    if (data.llm && data.llm.nube) {
      const b = document.createElement('span');
      b.className = 'nube';
      b.textContent = 'NUBE';
      b.title = 'Respondió ' + data.llm.proveedor + ' (' + data.llm.modelo + ', ' + data.llm.key + '): solo pruebas';
      wait.appendChild(b);
    }
    if (!data.sources.length && pending > 0) wait.textContent += ' (Sigo procesando ' + pending + ' elemento(s): prueba de nuevo en un momento.)';
    if (data.sources && data.sources.length) {
      const det = document.createElement('details');
      const sum = document.createElement('summary');
      sum.textContent = 'Fuentes (' + data.sources.length + ')';
      det.appendChild(sum);
      data.sources.forEach(s => {
        const p = document.createElement('div');
        p.className = 'src';
        p.textContent = '[' + s.n + '] ' + (s.cita || s.filename) + (s.distance != null ? ' · distancia ' + s.distance : '') + '\n' + s.snippet;
        det.appendChild(p);
      });
      wait.appendChild(det);
    }
  } catch (e) { wait.textContent = 'No pude responder: ' + e.message; }
  busy(false);
  cloud();
  q.focus();
}

async function remember() {
  const text = q.value.trim();
  if (!text) return;
  q.value = '';
  busy(true);
  try {
    const r = await fetch('/inbox/text', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: text, source: 'web'})});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Error ' + r.status);
    add('sys', d.duplicate ? 'Ya lo sabía (duplicado).' : 'Guardado. Lo procesaré en unos segundos.');
  } catch (e) { add('sys', 'No pude guardar: ' + e.message); }
  busy(false);
  q.focus();
  stats();
  inbox();
}

async function upload(file) {
  busy(true);
  add('sys', 'Subiendo ' + file.name + '...');
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch('/inbox/file?source=web', {method: 'POST', body: fd});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Error ' + r.status);
    add('sys', d.duplicate ? file.name + ': ya lo tenía.' : file.name + ' guardado (' + d.kind + '). Se procesará en segundo plano; su estado está en «Bandeja».');
  } catch (e) { add('sys', 'No pude subir ' + file.name + ': ' + e.message); }
  busy(false);
  stats();
  inbox();
}

// ---------- bandeja: estado de lo que entró, errores y reintento ----------
const LABEL = {processed: 'listo', pending: 'en cola', procesando: 'procesando', failed: 'falló',
               unsupported: 'no soportado', empty: 'sin texto'};
const DIGEST = {done: 'resumido', failed: 'resumen falló', retry: 'resumen en cola'};
const panel = document.getElementById('panel');
let inboxTimer = null;
function ago(iso) {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  return s < 60 ? 'hace ' + Math.round(s) + ' s' : s < 3600 ? 'hace ' + Math.round(s / 60) + ' min'
       : s < 86400 ? 'hace ' + Math.round(s / 3600) + ' h' : new Date(iso).toLocaleDateString();
}
async function retry(id, btn) {
  btn.disabled = true; btn.textContent = 'Reintentando…';
  try {
    const r = await fetch('/requeue/' + encodeURIComponent(id), {method: 'POST'});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Error ' + r.status);
  } catch (e) { btn.textContent = 'No se pudo: ' + e.message; return; }
  inbox();
}
async function retryDigests(btn) {
  btn.disabled = true;
  try { await fetch('/requeue/digests', {method: 'POST'}); } catch (e) {}
  inbox();
}
async function inbox() {
  clearTimeout(inboxTimer);
  let list = [];
  try {
    const r = await fetch('/inbox?limit=30');
    if (!r.ok) throw new Error('Error ' + r.status);
    list = await r.json();
  } catch (e) {
    document.getElementById('items').textContent = 'No pude leer la bandeja: ' + e.message;
    inboxTimer = setTimeout(inbox, 15000);
    return;
  }
  const problems = list.filter(e => e.reintentable || e.digest === 'failed').length;
  const working = list.filter(e => e.status === 'pending' || e.procesando).length;
  const btn = document.getElementById('inboxBtn');
  btn.textContent = 'Bandeja' + (working ? ' · ' + working + ' en curso' : '') + (problems ? ' · ' + problems + ' con aviso' : '');
  btn.className = problems ? 'warn' : '';
  if (!panel.hidden) {
    const box = document.getElementById('items');
    box.textContent = list.length ? '' : 'Todavía no entró nada.';
    list.forEach(e => {
      const st = e.procesando ? 'procesando' : e.status;
      const it = document.createElement('div');
      it.className = 'item';
      const n = document.createElement('div'); n.className = 'n'; n.textContent = e.filename;
      const b = document.createElement('span'); b.className = 'st st-' + st; b.textContent = LABEL[st] || st;
      n.appendChild(b);
      const m = document.createElement('div'); m.className = 'm';
      m.textContent = e.kind + ' · ' + e.source + ' · ' + ago(e.created_at) + (e.private ? ' · privado' : '') +
        (e.attempts ? ' · intentos ' + e.attempts : '') + (e.digest && DIGEST[e.digest] ? ' · ' + DIGEST[e.digest] : '');
      it.append(n, m);
      if (e.status === 'pending' && !e.procesando && Date.now() - Date.parse(e.created_at) > 10 * 60000) {
        const w = document.createElement('div'); w.className = 'err';
        w.textContent = 'Lleva más de 10 min en cola: revisa el panel o docker logs nexus-core.';
        it.appendChild(w);
      }
      if (e.error && e.status !== 'processed') {
        const er = document.createElement('div'); er.className = 'err'; er.textContent = e.error; it.appendChild(er);
      }
      if (e.reintentable) {
        const r = document.createElement('button'); r.textContent = 'Reintentar';
        r.onclick = () => retry(e.id, r); it.appendChild(r);
      } else if (e.digest === 'failed') {
        const r = document.createElement('button'); r.textContent = 'Reintentar resúmenes fallidos';
        r.onclick = () => retryDigests(r); it.appendChild(r);
      }
      box.appendChild(it);
    });
  }
  inboxTimer = setTimeout(inbox, working ? 4000 : 20000);
}
document.getElementById('inboxBtn').addEventListener('click', () => { panel.hidden = !panel.hidden; inbox(); });
document.getElementById('closePanel').addEventListener('click', () => { panel.hidden = true; });

let rec = null, chunks = [];
async function toggleMic() {
  const btn = document.getElementById('mic');
  if (rec) { rec.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    rec = new MediaRecorder(stream);
    chunks = [];
    rec.ondataavailable = e => chunks.push(e.data);
    rec.onstop = () => {
      stream.getTracks().forEach(t => t.stop());
      btn.textContent = '🎙';
      const blob = new Blob(chunks, {type: rec.mimeType || 'audio/webm'});
      rec = null;
      upload(new File([blob], 'voz-' + Date.now() + '.webm', {type: blob.type}));
    };
    rec.start();
    btn.textContent = '⏹ Grabando...';
  } catch (e) { add('sys', 'No pude usar el micrófono: ' + e.message); }
}

async function cloud() {
  try {
    const h = await (await fetch('/health')).json();
    const c = document.getElementById('cloud');
    c.hidden = !h.nube;
    if (h.llm && h.llm.ultimo) c.title = 'La última respuesta salió de la nube: ' + h.llm.ultimo.proveedor + ' (solo pruebas)';
  } catch (e) {}
}

async function stats() {
  try {
    const g = await (await fetch('/growth')).json();
    pending = g.pendientes_de_digerir;
    let dig = '';
    try {
      const w = await (await fetch('/worker')).json();
      const d = w.digiriendo;
      if (d) dig = ' · digiriendo ' + (d.archivo || '') + (d.total ? ' (' + d.hecho + '/' + d.total + ')' : '');
    } catch (e) {}
    document.getElementById('stats').textContent =
      '· ' + g.aprendido.elementos + ' elementos · racha ' + g.racha_dias + ' · por digerir ' + g.pendientes_de_digerir +
      (g.fallidos > 0 ? ' · fallidos ' + g.fallidos : '') + dig;
  } catch (e) {}
}

document.getElementById('f').addEventListener('submit', e => { e.preventDefault(); ask(); });
document.getElementById('remember').addEventListener('click', remember);
document.getElementById('mic').addEventListener('click', toggleMic);
document.getElementById('file').addEventListener('change', e => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = ''; });
document.body.addEventListener('dragover', e => e.preventDefault());
document.body.addEventListener('drop', e => { e.preventDefault(); [...e.dataTransfer.files].forEach(upload); });
stats();
cloud();
inbox();
setInterval(stats, 15000);
setInterval(cloud, 15000);
</script></body></html>"""
