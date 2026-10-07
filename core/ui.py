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
</style></head><body>
<header><div><b>NEXUS</b> <span id="stats"></span></div><a href="/dashboard">Panel</a></header>
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
function busy(on) { document.querySelectorAll('button').forEach(b => b.disabled = on); }

async function ask() {
  const text = q.value.trim();
  if (!text) return;
  q.value = '';
  add('me', text);
  const wait = add('bot', 'Pensando...');
  busy(true);
  try {
    const r = await fetch('/ask', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question: text})});
    if (!r.ok) throw new Error('Error ' + r.status);
    const data = await r.json();
    wait.textContent = data.answer;
    if (!data.sources.length && pending > 0) wait.textContent += ' (Sigo procesando ' + pending + ' elemento(s): prueba de nuevo en un momento.)';
    if (data.sources && data.sources.length) {
      const det = document.createElement('details');
      const sum = document.createElement('summary');
      sum.textContent = 'Fuentes (' + data.sources.length + ')';
      det.appendChild(sum);
      data.sources.forEach(s => {
        const p = document.createElement('div');
        p.className = 'src';
        p.textContent = '[' + s.n + '] ' + s.filename + ' · distancia ' + s.distance + '\n' + s.snippet;
        det.appendChild(p);
      });
      wait.appendChild(det);
    }
  } catch (e) { wait.textContent = 'No pude responder: ' + e.message; }
  busy(false);
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
    add('sys', d.duplicate ? 'Ya lo sabía (duplicado).' : 'Guardado. Lo procesaré en unos segundos.');
  } catch (e) { add('sys', 'No pude guardar: ' + e.message); }
  busy(false);
  q.focus();
  stats();
}

async function upload(file) {
  busy(true);
  add('sys', 'Subiendo ' + file.name + '...');
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch('/inbox/file?source=web', {method: 'POST', body: fd});
    const d = await r.json();
    add('sys', d.duplicate ? file.name + ': ya lo tenía.' : file.name + ' guardado (' + d.kind + '). Se procesará en segundo plano.');
  } catch (e) { add('sys', 'No pude subir: ' + e.message); }
  busy(false);
  stats();
}

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

async function stats() {
  try {
    const g = await (await fetch('/growth')).json();
    pending = g.pendientes_de_digerir;
    document.getElementById('stats').textContent =
      '· ' + g.aprendido.elementos + ' elementos · racha ' + g.racha_dias + ' · por digerir ' + g.pendientes_de_digerir +
      (g.fallidos > 0 ? ' · fallidos ' + g.fallidos : '');
  } catch (e) {}
}

document.getElementById('f').addEventListener('submit', e => { e.preventDefault(); ask(); });
document.getElementById('remember').addEventListener('click', remember);
document.getElementById('mic').addEventListener('click', toggleMic);
document.getElementById('file').addEventListener('change', e => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = ''; });
document.body.addEventListener('dragover', e => e.preventDefault());
document.body.addEventListener('drop', e => { e.preventDefault(); [...e.dataTransfer.files].forEach(upload); });
stats();
setInterval(stats, 15000);
</script></body></html>"""
