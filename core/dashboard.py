from string import Template

PAGE = Template("""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>NEXUS</title>
<style>
  body{margin:0;background:#0f1220;color:#e8eaf6;font-family:system-ui,sans-serif;padding:24px}
  h1{margin:0 0 4px;font-size:28px}
  .sub{color:#8f96b8;margin-bottom:24px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}
  .card{background:#181c33;border-radius:12px;padding:16px}
  .num{font-size:30px;font-weight:700}
  .lbl{color:#8f96b8;font-size:13px}
  .bars{display:flex;align-items:flex-end;gap:6px;height:120px;background:#181c33;border-radius:12px;padding:16px}
  .bar{flex:1;background:#6c7bff;border-radius:4px 4px 0 0}
  .days{display:flex;gap:6px;padding:4px 16px;color:#8f96b8;font-size:11px}
  .days span{flex:1;text-align:center}
</style></head><body>
<h1>NEXUS</h1>
<div class="sub">$dias días de vida · nació el $nacio</div>
<div class="grid">
  <div class="card"><div class="num">$elementos</div><div class="lbl">elementos absorbidos</div></div>
  <div class="card"><div class="num">$recuerdos</div><div class="lbl">recuerdos (trozos)</div></div>
  <div class="card"><div class="num">$palabras</div><div class="lbl">palabras aprox.</div></div>
  <div class="card"><div class="num">$conceptos</div><div class="lbl">conceptos distintos</div></div>
  <div class="card"><div class="num">+$hoy</div><div class="lbl">aprendido hoy</div></div>
  <div class="card"><div class="num">$racha</div><div class="lbl">días de racha</div></div>
  <div class="card"><div class="num">$pendientes</div><div class="lbl">por digerir</div></div>
</div>
<h3>Últimos días</h3>
<div class="bars">$bars</div>
<div class="days">$labels</div>
<p class="sub">Respaldo: $respaldo</p>
</body></html>""")


def render(g: dict, b: dict) -> str:
    days = g["ultimos_dias"]
    peak = max((d["nuevos"] for d in days), default=0) or 1
    bars = "".join(
        f'<div class="bar" style="height:{max(2, int(d["nuevos"] / peak * 100))}%" '
        f'title="{d["dia"]}: {d["nuevos"]}"></div>'
        for d in days
    )
    labels = "".join(f'<span>{d["dia"][8:]}</span>' for d in days)
    a = g["aprendido"]
    if b["ultimo_respaldo"]:
        respaldo = f'{b["ultimo_respaldo"][:16].replace("T", " ")} UTC · {len(b["snapshots"])} copias'
    else:
        respaldo = "aún no hay copias"
    return PAGE.substitute(
        dias=g["dias_de_vida"],
        nacio=g["nacio"] or "—",
        elementos=a["elementos"],
        recuerdos=a["recuerdos"],
        palabras=a["palabras_aprox"],
        conceptos=a["conceptos"],
        hoy=g["hoy"],
        racha=g["racha_dias"],
        pendientes=g["pendientes_de_digerir"],
        bars=bars,
        labels=labels,
        respaldo=respaldo,
    )
