"""La web de Isabelle (Fase 5): login, chat transparente sobre las neuronas vivas y «Lo guardado».

Todo en HTML, CSS y JS puros, sin librerías ni CDN. Las neuronas se alimentan de datos reales
(GET /brain/graph: notas, archivos y conceptos, máx. 300, con conexiones por parecido y por
conceptos compartidos) y destellan con /brain/stream (llega algo nuevo, se busca, se responde).
Canvas con tope de cuadros, en pausa con la pestaña oculta y quieto con prefers-reduced-motion.
"""

ESTILO_BASE = r"""
:root{--bg:#02040d;--txt:#e6f0ff;--mut:#7f93bf;--acc:#3fb6ff;--glow:rgba(63,182,255,.55)}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
a,button.t{color:var(--acc);background:none;border:0;padding:0;font:inherit;cursor:pointer;text-decoration:none}
a:hover,button.t:hover{text-shadow:0 0 8px var(--glow)}
button.t:disabled{opacity:.4;cursor:wait}
.mut{color:var(--mut)}
input.linea{width:100%;background:transparent;border:0;border-bottom:1px solid rgba(63,182,255,.35);color:var(--txt);
  font:inherit;padding:10px 2px;outline:none;box-shadow:0 8px 18px -14px var(--glow);transition:border-color .3s,box-shadow .3s}
input.linea:focus{border-bottom-color:rgba(63,182,255,.8);box-shadow:0 10px 22px -12px var(--glow)}
"""

FONDO_LOGIN = "radial-gradient(ellipse at 50% 60%, #0b1f4a 0%, #050a1f 45%, #02040d 80%)"


def login(hay_clave: bool, cifrada: bool = True) -> str:
    aviso = ("" if hay_clave else
             '<p class="mut">Aún no hay contraseña. En la terminal: <code>docker compose exec core python -m core.auth</code></p>')
    if not cifrada:  # HTTP: la contraseña y la sesión viajan sin cifrar
        aviso += '<p class="sincifrar" role="note">Conexión sin cifrar</p>'
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Isabelle</title>
<style>{ESTILO_BASE}
body{{display:flex;align-items:center;justify-content:center;background:{FONDO_LOGIN}}}
main{{width:min(360px,86vw);text-align:center}}
h1{{font-weight:300;letter-spacing:.3em;font-size:22px;text-shadow:0 0 18px var(--glow)}}
#m{{min-height:1.5em;font-size:14px}}
.sincifrar{{color:#ffb86b;font-size:12px;letter-spacing:.06em;margin:-6px 0 14px}}
</style></head><body><main>
<h1>ISABELLE</h1>{aviso}
<form id="f"><input class="linea" id="c" type="password" autocomplete="current-password" placeholder="contraseña" autofocus>
<p><button class="t" id="b">entrar</button></p><p id="m" class="mut"></p></form>
</main><script>
const f=document.getElementById('f'),m=document.getElementById('m'),b=document.getElementById('b');
f.onsubmit=async e=>{{e.preventDefault();b.disabled=true;m.textContent='';
  const r=await fetch('/login',{{method:'POST',body:new URLSearchParams({{clave:document.getElementById('c').value}})}});
  if(r.ok){{location.href='/';return}}
  const d=await r.json().catch(()=>({{}}));m.textContent=d.detail||'No se pudo entrar.';b.disabled=false}};
</script></body></html>"""


PAGE = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Isabelle</title>
<style>""" + ESTILO_BASE + r"""
html,body{overflow:hidden}
canvas#red{position:fixed;inset:0;width:100vw;height:100vh;display:block;z-index:0}
#top{position:fixed;top:0;right:0;left:0;display:flex;justify-content:flex-end;gap:18px;padding:14px 18px;z-index:3;font-size:14px}
#top .yo{margin-right:auto;letter-spacing:.3em;font-weight:300;color:var(--mut)}
#panel{position:fixed;top:44px;right:18px;z-index:3;font-size:13px;text-align:right;line-height:1.7;text-shadow:0 0 6px #000}
#panel b{color:var(--txt);font-weight:500}
#chat{position:fixed;left:0;right:0;bottom:0;top:52px;display:flex;flex-direction:column;z-index:2;pointer-events:none}
#log{flex:1;overflow-y:auto;padding:12px max(16px,calc(50vw - 360px)) 8px;display:flex;flex-direction:column;gap:14px;
  pointer-events:auto;scrollbar-width:none;-webkit-mask-image:linear-gradient(to bottom,transparent 0,#000 64px)}
#log::-webkit-scrollbar{display:none}
.msg{white-space:pre-wrap;word-wrap:break-word;max-width:92%;text-shadow:0 0 10px #000,0 0 2px #000}
.jefe{align-self:flex-end;text-align:right;color:#b9c8e6}
.isa{align-self:flex-start;color:var(--txt)}
.isa .acc{margin-top:4px;display:flex;gap:16px;flex-wrap:wrap;font-size:14px}
.sys{align-self:center;color:var(--mut);font-size:13px;text-align:center}
#pie{padding:6px max(16px,calc(50vw - 360px)) max(14px,env(safe-area-inset-bottom));pointer-events:auto}
#fila{display:flex;align-items:center;gap:14px}
#fila .linea{flex:1}
#clip{font-size:20px;line-height:1}
#escribe{height:1.3em;font-size:13px}
#soltar{position:fixed;inset:0;z-index:5;display:none;align-items:center;justify-content:center;font-size:20px;
  letter-spacing:.1em;color:var(--acc);text-shadow:0 0 16px var(--glow);background:rgba(2,4,13,.55)}
body.arrastra #soltar{display:flex}
</style></head><body>
<canvas id="red" aria-hidden="true"></canvas>
<nav id="top"><span class="yo">ISABELLE</span><button class="t" id="vpanel" title="Panel">·</button>
<a href="/guardado">lo guardado</a><button class="t" id="salir">salir</button></nav>
<div id="panel" hidden></div>
<div id="chat"><div id="log" aria-live="polite"></div>
<div id="pie"><div id="escribe" class="mut"></div>
<form id="fila"><button type="button" class="t" id="clip" title="Adjuntar audio, foto, video o documento">📎</button>
<input class="linea" id="txt" autocomplete="off" placeholder="Escríbele a Isabelle…" enterkeyhint="send">
<button class="t" id="env">enviar</button>
<input type="file" id="archivos" multiple hidden accept="image/*,audio/*,video/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.csv,.json,.html,.eml,.zip,.epub"></form></div></div>
<div id="soltar">suelta para enviárselo a Isabelle</div>
<script>
"use strict";
const $=id=>document.getElementById(id);
const log=$('log'),txt=$('txt'),escribe=$('escribe');
const CANAL={channel:'web',chat_id:'web'};
async function api(url,opt={}){
  const r=await fetch(url,{credentials:'same-origin',...opt});
  if(r.status===401){location.href='/login';throw new Error('sin sesión')}
  const d=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(d.detail||('Error '+r.status));
  return d;
}
const post=(url,body)=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});

// ---------- chat ----------
function linea(texto,quien,botones){
  const d=document.createElement('div');d.className='msg '+quien;d.textContent=texto;
  if(botones&&botones.length){const acc=document.createElement('div');acc.className='acc';
    for(const fila of botones)for(const b of fila){const k=document.createElement('button');k.className='t';k.textContent=b.texto;
      k.onclick=()=>{acc.remove();boton(b)};acc.appendChild(k)}
    d.appendChild(acc)}
  log.appendChild(d);log.scrollTop=log.scrollHeight;return d;
}
let pensando=0;
function ocupado(n){pensando+=n;escribe.textContent=pensando>0?'Isabelle está escribiendo…':''}
async function responder(trabajo){
  ocupado(1);
  try{let r=await trabajo;
    while(true){if(r.texto)linea(r.texto,'isa',r.botones);Red.respuesta();
      if(!r.seguir)break;r=await post('/conversar/seguir',CANAL)}
  }catch(e){linea('⚠ '+e.message,'sys')}finally{ocupado(-1)}
}
async function enviar(texto){linea(texto,'jefe');Red.pulso();await responder(post('/conversar',{mensajes:[texto],...CANAL}))}
async function boton(b){linea('«'+b.texto+'»','jefe');await responder(post('/conversar/boton',{dato:b.dato,...CANAL}))}
$('fila').onsubmit=e=>{e.preventDefault();const t=txt.value.trim();if(!t)return;txt.value='';enviar(t)};
$('clip').onclick=()=>$('archivos').click();
$('archivos').onchange=e=>{subir([...e.target.files]);e.target.value=''};
async function subir(files){
  if(!files.length)return;
  const leyenda=txt.value.trim();txt.value='';
  linea((leyenda?leyenda+'\n':'')+files.map(f=>'📎 '+f.name).join('\n'),'jefe');
  ocupado(1);
  try{const items=[];
    for(const f of files){const fd=new FormData();fd.append('file',f,f.name);
      const r=await api('/inbox/file?source=web',{method:'POST',body:fd});
      items.push({id:r.id,filename:f.name,kind:r.kind||'',duplicate:!!r.duplicate})}
    await responder(post('/conversar/archivo',{archivos:items,leyenda,...CANAL}));
  }catch(e){linea('⚠ '+e.message,'sys')}finally{ocupado(-1)}
}
let arrastres=0;
addEventListener('dragenter',e=>{if([...e.dataTransfer.types].includes('Files')){arrastres++;document.body.classList.add('arrastra')}});
addEventListener('dragleave',()=>{if(--arrastres<=0){arrastres=0;document.body.classList.remove('arrastra')}});
addEventListener('dragover',e=>e.preventDefault());
addEventListener('drop',e=>{e.preventDefault();arrastres=0;document.body.classList.remove('arrastra');subir([...e.dataTransfer.files])});
$('salir').onclick=async()=>{await fetch('/logout',{method:'POST'});location.href='/login'};
$('vpanel').onclick=async()=>{const p=$('panel');p.hidden=!p.hidden;if(p.hidden)return;
  try{const d=await api('/web/panel');p.innerHTML='';
    for(const [k,v] of [['días de vida',d.dias_de_vida],['elementos',d.elementos],['gasto de hoy','$'+d.gasto_hoy_usd.toFixed(4)]]){
      const l=document.createElement('div');l.append(k+' ');const b=document.createElement('b');b.textContent=v;l.append(b);p.append(l)}
  }catch(e){p.textContent=e.message}};
api('/web/historial?n=30').then(h=>{if(!h.length)linea('Escríbeme como a una persona, o arrastra un archivo aquí.','sys');
  for(const m of h)linea(m.texto,m.rol==='jefe'?'jefe':'isa')}).catch(()=>{});

// ---------- neuronas: galaxia con datos reales ----------
const Red=(()=>{
  const cv=$('red'),cx=cv.getContext('2d');
  const quieto=matchMedia('(prefers-reduced-motion: reduce)').matches;
  const movil=matchMedia('(max-width: 700px)').matches;
  const FPS=movil?24:30,PASO=1000/FPS;
  const COLOR={text:'#7fd4ff',document:'#3fb6ff',image:'#8fa8ff',audio:'#5ce1e6',video:'#6f8cff',concept:'#c9ecff'};
  let W=0,H=0,R=0,dpr=1,nodos=new Map(),enlaces=[],estrellas=[],polvo=[],destellos=[],t0=performance.now(),ultimo=0,activo=true,vuelta=0;
  function hash(s){let h=2166136261;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)}
    h^=h>>>16;h=Math.imul(h,2246822507);h^=h>>>13;h=Math.imul(h,3266489909);h^=h>>>16;return (h>>>0)/4294967296}
  function azar(semilla){let a=semilla>>>0;return()=>{a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);
    t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296}}
  function medir(){dpr=Math.min(devicePixelRatio||1,2);W=innerWidth;H=innerHeight;R=Math.min(W,H)*(movil?.46:.42);
    cv.width=W*dpr;cv.height=H*dpr;cx.setTransform(dpr,0,0,dpr,0,0);
    const rnd=azar(20261104);
    estrellas=Array.from({length:movil?90:170},()=>({x:rnd()*W,y:rnd()*H,r:rnd()*1.1+.2,f:rnd()*6.28}));
    // Polvo de galaxia (ambiente, no son datos): tres brazos en espiral que giran despacio
    polvo=Array.from({length:movil?260:520},()=>{const r=Math.pow(rnd(),.7)*.98+.02,brazo=Math.floor(rnd()*3);
      return {r,a:brazo*2.094+r*4.2+(rnd()-.5)*(.9-.5*r),z:rnd()}});
    if(quieto)dibujar(performance.now())}
  // Posición en un brazo de espiral: los más nuevos cerca del centro, el tipo da el brazo
  function ubicar(n,orden){const h=hash(n.id),brazo=Math.floor(hash(n.id+'b')*3);
    const rr=n.type==='concept'?.25+.65*h:.12+.85*Math.sqrt(Math.min(1,orden/300)+.02*h);
    n.r=rr;n.a=brazo*2.094+rr*4.2+(h-.5)*.7;n.z=.6+hash(n.id+'z')*.4;n.t=n.t||performance.now()}
  function cargar(g){const viejos=nodos;nodos=new Map();
    g.nodes.forEach((n,i)=>{const v=viejos.get(n.id);const m={...n,...(v?{t:v.t}:{})};ubicar(m,i);nodos.set(n.id,m)});
    enlaces=g.links.filter(l=>nodos.has(l.source)&&nodos.has(l.target));
    if(quieto)dibujar(performance.now())}
  function pos(n,t){const giro=quieto?0:(t-t0)/1000*.018/(.35+n.r);const a=n.a+giro;
    return [W/2+Math.cos(a)*n.r*R,H/2+Math.sin(a)*n.r*R*.62]}
  function destello(id,fuerza=1){destellos.push({id,t:performance.now(),f:fuerza});if(quieto){dibujar(performance.now());setTimeout(()=>dibujar(performance.now()),1600)}}
  function dibujar(t){
    cx.clearRect(0,0,W,H);
    const g=cx.createRadialGradient(W/2,H/2,0,W/2,H/2,R*1.25);g.addColorStop(0,'rgba(60,140,255,.32)');g.addColorStop(.45,'rgba(20,60,170,.08)');g.addColorStop(1,'rgba(2,4,13,0)');
    cx.fillStyle=g;cx.fillRect(0,0,W,H);
    for(const s of estrellas){const a=quieto?.5:.35+.35*Math.sin(t/1400+s.f);cx.fillStyle=`rgba(170,210,255,${a})`;cx.fillRect(s.x,s.y,s.r,s.r)}
    for(const p of polvo){const [x,y]=pos(p,t);const s=1+p.z*1.2;cx.fillStyle=`rgba(110,185,255,${.22+.45*p.z*(1-p.r*.55)})`;cx.fillRect(x,y,s,s)}
    const P=new Map();for(const n of nodos.values())P.set(n.id,pos(n,t));
    const vivos=new Map();destellos=destellos.filter(d=>t-d.t<1800);
    for(const d of destellos)vivos.set(d.id,Math.max(vivos.get(d.id)||0,(1-(t-d.t)/1800)*d.f));
    cx.lineWidth=.6;
    for(const l of enlaces){const a=P.get(l.source),b=P.get(l.target);if(!a||!b)continue;
      const v=Math.max(vivos.get(l.source)||0,vivos.get(l.target)||0);
      cx.strokeStyle=l.kind==='concepto'?`rgba(120,190,255,${.07+.5*v})`:`rgba(63,182,255,${.12+.6*v})`;
      cx.beginPath();cx.moveTo(a[0],a[1]);cx.lineTo(b[0],b[1]);cx.stroke()}
    for(const n of nodos.values()){const [x,y]=P.get(n.id);const v=vivos.get(n.id)||0;const nuevo=Math.max(0,1-(t-n.t)/4000);
      const r=(n.type==='concept'?1.4:2.1+Math.min(2,(n.trozos||0)/20))*n.z+3*v+2*nuevo;
      const halo=.10+.3*(v+nuevo);cx.fillStyle=`rgba(80,180,255,${halo})`;cx.beginPath();cx.arc(x,y,r*(3.5+3*v),0,6.283);cx.fill()
      cx.fillStyle=v>.05||nuevo>.05?'#ffffff':(COLOR[n.type]||COLOR.document);
      cx.globalAlpha=.55+.45*n.z;cx.beginPath();cx.arc(x,y,r,0,6.283);cx.fill();cx.globalAlpha=1}
    if(vuelta>0){const k=(t-vuelta)/1400;if(k<1){cx.strokeStyle=`rgba(63,182,255,${.35*(1-k)})`;cx.lineWidth=1.5;
      cx.beginPath();cx.ellipse(W/2,H/2,R*k*1.1,R*k*.68,0,0,6.283);cx.stroke()}else vuelta=0}
  }
  function bucle(t){if(!activo)return;requestAnimationFrame(bucle);if(t-ultimo<PASO)return;ultimo=t;dibujar(t)}
  document.addEventListener('visibilitychange',()=>{activo=!document.hidden&&!quieto;if(activo){ultimo=0;requestAnimationFrame(bucle)}});
  addEventListener('resize',medir);
  let refresco=null;
  function refrescar(ms=3500){clearTimeout(refresco);refresco=setTimeout(()=>api('/brain/graph').then(cargar).catch(()=>{}),ms)}
  function nuevoNodo(id,type,label){if(!id||nodos.has(id))return;const n={id,type:type||'document',label,trozos:0,t:performance.now()};
    ubicar(n,0);nodos.set(id,n);destello(id,1.4)}
  function conectar(){const es=new EventSource('/brain/stream');
    es.onmessage=e=>{let m;try{m=JSON.parse(e.data)}catch{return}
      if(m.tipo==='ingest'){nuevoNodo(m.id,m.kind,m.filename);refrescar()}
      else if(m.tipo==='processed'||m.tipo==='digested'){destello(m.id,1.2);
        for(const c of m.conceptos||[]){const cid='c:'+c.toLowerCase();nuevoNodo(cid,'concept',c);enlaces.push({source:m.id,target:cid,kind:'concepto'})}
        refrescar()}
      else if(m.tipo==='recall'){(m.ids||[]).forEach((id,i)=>setTimeout(()=>destello(id,1),i*120))}
      else if(m.tipo==='olvido'){refrescar(500)}};
    es.onerror=()=>{if(es.readyState===2)setTimeout(conectar,5000)}}
  medir();api('/brain/graph').then(cargar).catch(()=>{});conectar();
  if(!quieto)requestAnimationFrame(bucle);else activo=false;
  return {
    pulso(){vuelta=performance.now();if(quieto)dibujar(vuelta)},
    respuesta(){const ids=[...nodos.keys()];for(let i=0;i<Math.min(5,ids.length);i++)destello(ids[Math.floor(Math.random()*ids.length)],.8);vuelta=performance.now()}
  };
})();
</script></body></html>"""


GUARDADO = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Isabelle · lo guardado</title>
<style>""" + ESTILO_BASE + r"""
body{background:radial-gradient(ellipse at 50% 0%,#0a1a40 0%,#02040d 60%) fixed}
main{max-width:780px;margin:0 auto;padding:18px 16px 60px}
nav{display:flex;justify-content:space-between;align-items:center;font-size:14px;margin-bottom:8px}
nav .yo{letter-spacing:.3em;font-weight:300;color:var(--mut)}
#tipos{display:flex;gap:16px;flex-wrap:wrap;font-size:14px;margin:12px 0 4px}
#tipos .on{color:var(--txt);text-shadow:0 0 8px var(--glow)}
.item{padding:12px 0;border-bottom:1px solid rgba(63,182,255,.12);cursor:pointer}
.item:hover .n{text-shadow:0 0 8px var(--glow)}
.n{word-break:break-all}
.m{font-size:13px;color:var(--mut)}
.d{font-size:14px;color:#b9c8e6;margin-top:2px}
#det{white-space:pre-wrap;font-size:14px;line-height:1.55}
#det h2{font-weight:400;font-size:18px;margin:6px 0;word-break:break-all;white-space:normal}
#det h3{font-weight:400;font-size:13px;color:var(--mut);letter-spacing:.08em;text-transform:uppercase;margin:18px 0 4px}
#det img{max-width:100%;border-radius:4px;margin-top:8px}
#mas{margin-top:16px;font-size:14px}
</style></head><body><main>
<nav><span class="yo">LO GUARDADO</span><a href="/">volver al chat</a></nav>
<div id="lista"><input class="linea" id="q" placeholder="buscar por nombre o contenido…" autocomplete="off">
<div id="tipos"></div><div class="m" id="total"></div><div id="items"></div><button class="t" id="mas" hidden>ver más</button></div>
<div id="det" hidden></div>
</main><script>
"use strict";
const $=id=>document.getElementById(id);
const TIPOS=[['','todo'],['notas','notas'],['documentos','documentos'],['fotos','fotos'],['audios','audios'],['videos','videos']];
const NOMBRE={text:'nota',document:'documento',image:'foto',audio:'audio',video:'video'};
let tipo='',q='',offset=0;const LIM=40;
async function api(url){const r=await fetch(url,{credentials:'same-origin'});if(r.status===401){location.href='/login';throw 0}
  const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||r.status);return d}
function kb(n){return n>1048576?(n/1048576).toFixed(1)+' MB':Math.max(1,Math.round(n/1024))+' KB'}
function fecha(s){return new Date(s).toLocaleString('es-PE',{dateStyle:'medium',timeStyle:'short'})}
function el(tag,cls,texto){const e=document.createElement(tag);if(cls)e.className=cls;if(texto!=null)e.textContent=texto;return e}
function pintarTipos(){const c=$('tipos');c.innerHTML='';for(const [k,v] of TIPOS){const b=el('button','t'+(k===tipo?' on':''),v);
  b.onclick=()=>{tipo=k;cargar(true)};c.append(b)}}
async function cargar(reset){if(reset){offset=0;$('items').innerHTML=''}pintarTipos();
  const d=await api(`/guardado/lista?limit=${LIM}&offset=${offset}`+(tipo?`&tipo=${tipo}`:'')+(q?`&q=${encodeURIComponent(q)}`:''));
  $('total').textContent=d.total+(d.total===1?' elemento':' elementos');
  for(const it of d.items){const div=el('div','item');div.append(el('div','n',it.filename||'(sin nombre)'));
    div.append(el('div','m',`${NOMBRE[it.kind]||it.kind} · ${fecha(it.created_at)} · ${kb(it.size)}`+(it.status!=='processed'?` · ${it.status}`:'')));
    const desc=it.descripcion||it.inicio;if(desc)div.append(el('div','d',desc.slice(0,160)));
    div.onclick=()=>{location.hash=it.id};$('items').append(div)}
  offset+=d.items.length;$('mas').hidden=offset>=d.total}
$('mas').onclick=()=>cargar(false);
let espera;$('q').oninput=e=>{clearTimeout(espera);espera=setTimeout(()=>{q=e.target.value.trim();cargar(true)},300)};
async function detalle(id){const det=$('det');det.innerHTML='';
  try{const d=await api('/guardado/'+encodeURIComponent(id));
    const v=el('button','t','← volver');v.onclick=()=>{location.hash=''};det.append(v);
    det.append(el('h2',null,d.filename||'(sin nombre)'));
    det.append(el('div','m',`${NOMBRE[d.kind]||d.kind} · ${fecha(d.created_at)} · ${kb(d.size)} · ${d.trozos} trozos`));
    if(['failed','unsupported','empty'].includes(d.status)){det.append(el('div','m',`${d.status}: ${d.error||''}`));
      const re=el('button','t','Reintentar');re.onclick=async()=>{re.disabled=true;
        const r=await fetch('/requeue/'+d.id,{method:'POST',credentials:'same-origin'});re.textContent=r.ok?'reencolado':'no se pudo'};det.append(re)}
    const a=el('a',null,'descargar el original');a.href=`/guardado/${d.id}/original`;a.setAttribute('download','');const pa=el('p');pa.append(a);det.append(pa);
    if(d.vista){const im=el('img');im.alt='';im.src=`/guardado/${d.id}/vista`;det.append(im)}
    const an=d.analisis;
    if(an){if(an.descripcion){det.append(el('h3',null,'lo que vi'));det.append(el('div',null,an.descripcion))}
      if(an.datos&&an.datos.length){det.append(el('h3',null,'datos leídos'+(an.confirmado?' (confirmados)':'')));
        det.append(el('div',null,an.datos.map(x=>`${x.que||x.tipo}: ${x.valor}`).join('\n')))}
      if(an.ilegible){det.append(el('h3',null,'no se leía bien'));det.append(el('div',null,an.ilegible))}
      if(an.notas&&an.notas.length){det.append(el('h3',null,'lo que me dijiste'));det.append(el('div',null,an.notas.map(n=>n.texto).join('\n')))}
      if(an.tipo==='video'){det.append(el('h3',null,'video'));det.append(el('div',null,'Los videos solo se guardan: no los veo ni los escucho.'))}
      if(an.tipo==='audio'&&an.estado!=='analizado'){det.append(el('h3',null,'audio'));det.append(el('div',null,'No se entendió con claridad.'))}}
    if(d.resumen){det.append(el('h3',null,'resumen'));det.append(el('div',null,d.resumen))}
    if(d.conceptos.length){det.append(el('h3',null,'conceptos'));det.append(el('div',null,d.conceptos.join(' · ')))}
    if(d.texto){det.append(el('h3',null,an&&an.tipo==='audio'?'transcripción':'texto'));det.append(el('div',null,d.texto+(d.texto_recortado?'\n…':'')))}
  }catch(e){det.append(el('div','m',String(e.message||e)))}}
function ruta(){const id=location.hash.slice(1);$('lista').hidden=!!id;$('det').hidden=!id;if(id)detalle(id);scrollTo(0,0)}
addEventListener('hashchange',ruta);
cargar(true).then(ruta).catch(e=>{$('total').textContent=String(e.message||e)});
</script></body></html>"""
