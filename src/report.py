#!/usr/bin/env python3
"""Genera dashboard/data.json (agregados + movimientos) y un resumen en texto del último mes cerrado.

Definiciones:
  ingresos     = sueldo, paga, intereses, extras (parte mía)
  gasto neto   = gastos − reembolsos recibidos (parte mía; la conjunta cuenta al 50 %)
  inversión    = aportaciones netas (compras − ventas)
  ahorro       = ingresos − gasto neto        (lo que no te has gastado)
  liquidez     = ahorro − inversión           (lo que queda en cuenta corriente)
  trabajo      = ámbito Trabajo: sueldo − (gestoría + impuestos + suscripciones de trabajo)
"""
import json
import pathlib
import sys
from collections import defaultdict
from datetime import datetime

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import connect, ROOT  # noqa: E402

OUT = ROOT / "dashboard" / "data.json"


def r2(x):
    return round(x or 0, 2)


def agregar(rows):
    """rows: lista de dicts con tipo, categoria, ambito, mio. Devuelve dict de métricas."""
    m = defaultdict(float)
    cats = defaultdict(float)
    cats_inv = defaultdict(float)
    for x in rows:
        t, mio = x["tipo"], x["mio"]
        if t == "Ingreso":
            m["ingresos"] += mio
            if x["ambito"] == "Trabajo":
                m["ingresos_trabajo"] += mio
        elif t in ("Gasto", "Reembolso"):
            m["gasto_neto"] -= mio
            if t == "Gasto" and mio < 0:
                m["gasto_bruto"] -= mio
            else:
                m["reembolsos"] += mio
            if x["ambito"] == "Trabajo":
                m["gastos_trabajo"] -= mio
            cats[x["categoria"] or "SIN CATEGORÍA"] -= mio
            if x.get("fijo"):
                m["fijos"] -= mio
        elif t == "Inversion":
            m["inversion"] -= mio
            cats_inv[x["categoria"] or "SIN CATEGORÍA"] -= mio
    m["ahorro"] = m["ingresos"] - m["gasto_neto"]
    m["liquidez"] = m["ahorro"] - m["inversion"]
    m["tasa_ahorro"] = (m["ahorro"] / m["ingresos"]) if m["ingresos"] else None
    m["beneficio_trabajo"] = m["ingresos_trabajo"] - m["gastos_trabajo"]
    m["gasto_personal"] = m["gasto_neto"] - m["gastos_trabajo"]
    m["variables"] = m["gasto_neto"] - m["fijos"]
    out = {k: (r2(v) if k != "tasa_ahorro" else (round(v, 4) if v is not None else None)) for k, v in m.items()}
    out["categorias"] = {k: r2(v) for k, v in sorted(cats.items(), key=lambda kv: -kv[1])}
    out["inversiones"] = {k: r2(v) for k, v in sorted(cats_inv.items(), key=lambda kv: -kv[1])}
    return out


def main():
    con = connect()
    cats_cfg = yaml.safe_load((ROOT / "config" / "categorias.yaml").read_text(encoding="utf-8"))
    fijos = {n for t in cats_cfg.values() for n, p in (t or {}).items() if (p or {}).get("fijo")}
    cat_list = [{"nombre": n, "tipo": t, "fijo": bool((p or {}).get("fijo"))} for t, d in cats_cfg.items() for n, p in (d or {}).items()]

    rows = [dict(r) for r in con.execute("SELECT * FROM movimientos ORDER BY fecha, id").fetchall()]
    for x in rows:
        x["mio"] = r2(x["importe"] * (x["participacion"] or 1.0))
        x["fijo"] = x["categoria"] in fijos

    por_mes = defaultdict(list)
    por_anio = defaultdict(list)
    for x in rows:
        if x["tipo"] == "Traspaso":
            continue
        por_mes[x["mes"]].append(x)
        por_anio[x["mes"][:4]].append(x)

    meses = []
    for mes in sorted(por_mes):
        a = agregar(por_mes[mes])
        a["mes"] = mes
        a["pendientes"] = sum(1 for x in por_mes[mes] if x["estado"] == "pendiente")
        a["historico"] = all(x["cuenta"] == "Histórico" for x in por_mes[mes])
        meses.append(a)
    anios = {}
    for anio in sorted(por_anio):
        a = agregar(por_anio[anio])
        n_meses = len({x["mes"] for x in por_anio[anio]})
        a["meses"] = n_meses
        a["media_ingresos"] = r2(a["ingresos"] / n_meses)
        a["media_gasto"] = r2(a["gasto_neto"] / n_meses)
        a["media_ahorro"] = r2(a["ahorro"] / n_meses)
        anios[anio] = a

    # cuenta conjunta
    conj = [x for x in rows if x["cuenta"] == "Conjunta"]
    conjunta = {
        "aportado_joan": r2(sum(x["importe"] for x in conj if x["tipo"] == "Traspaso" and x["categoria"] == "TRASPASO")),
        "aportado_kate": r2(sum(x["importe"] for x in conj if x["categoria"] == "APORTACIÓN KATE")),
        "gasto_total": r2(-sum(x["importe"] for x in conj if x["tipo"] in ("Gasto", "Reembolso"))),
        "mi_parte": r2(-sum(x["mio"] for x in conj if x["tipo"] in ("Gasto", "Reembolso"))),
        "saldo": next((x["saldo"] for x in reversed(conj) if x["saldo"] is not None), None),
        "por_mes": {},
    }
    for mes in sorted({x["mes"] for x in conj}):
        ms = [x for x in conj if x["mes"] == mes]
        conjunta["por_mes"][mes] = {
            "joan": r2(sum(x["importe"] for x in ms if x["tipo"] == "Traspaso" and x["categoria"] == "TRASPASO")),
            "kate": r2(sum(x["importe"] for x in ms if x["categoria"] == "APORTACIÓN KATE")),
            "gasto": r2(-sum(x["importe"] for x in ms if x["tipo"] in ("Gasto", "Reembolso"))),
        }

    # cuentas: último saldo conocido e importación
    cuentas = []
    for c in con.execute("SELECT DISTINCT cuenta FROM movimientos WHERE cuenta != 'Histórico'").fetchall():
        c = c[0]
        last = con.execute("SELECT fecha, saldo FROM movimientos WHERE cuenta=? AND saldo IS NOT NULL ORDER BY fecha DESC, id DESC LIMIT 1", (c,)).fetchone()
        imp = con.execute("SELECT fecha_max, created_at FROM importaciones WHERE cuenta=? ORDER BY id DESC LIMIT 1", (c,)).fetchone()
        cuentas.append({"cuenta": c, "saldo": last["saldo"] if last else None, "fecha_saldo": last["fecha"] if last else None,
                        "hasta": imp["fecha_max"] if imp else None, "importado": imp["created_at"] if imp else None})

    pend = [{"id": x["id"], "fecha": x["fecha"], "cuenta": x["cuenta"], "concepto": x["concepto"], "importe": x["importe"],
             "tipo": x["tipo"], "categoria": x["categoria"], "nota": x["nota"]} for x in rows if x["estado"] == "pendiente"]

    movs = [{"id": x["id"], "fecha": x["fecha"], "cuenta": x["cuenta"], "concepto": x["concepto"], "importe": x["importe"],
             "mio": x["mio"], "tipo": x["tipo"], "categoria": x["categoria"], "ambito": x["ambito"], "estado": x["estado"],
             "nota": x["nota"]} for x in rows]

    data = {
        "generado": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "definiciones": __doc__.strip().split("Definiciones:")[1].strip(),
        "meses": meses,
        "anios": anios,
        "categorias": cat_list,
        "cuentas": cuentas,
        "conjunta": conjunta,
        "pendientes": pend,
        "movimientos": movs,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # CSV espejo (para consultarlo desde el móvil con el conector de Drive)
    import csv
    with open(ROOT / "data" / "movimientos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fecha", "cuenta", "concepto", "importe", "tu_parte", "tipo", "categoria", "ambito", "estado"])
        for x in movs:
            w.writerow([x["fecha"], x["cuenta"], x["concepto"], x["importe"], x["mio"], x["tipo"], x["categoria"], x["ambito"], x["estado"]])

    # resumen en texto del último mes completo (el anterior al actual)
    hoy = datetime.now().strftime("%Y-%m")
    cerrados = [m for m in meses if m["mes"] < hoy and not m["historico"]]
    if cerrados:
        m = cerrados[-1]
        print(f"Cierre {m['mes']}: ingresos {m['ingresos']:.2f} | gasto neto {m['gasto_neto']:.2f} | inversión {m['inversion']:.2f} | "
              f"ahorro {m['ahorro']:.2f} ({(m['tasa_ahorro'] or 0)*100:.0f} %) | pendientes {m['pendientes']}")
        top = list(m["categorias"].items())[:5]
        print("Top gastos: " + ", ".join(f"{k} {v:.0f}" for k, v in top))
    print(f"data.json: {len(meses)} meses, {len(movs)} movimientos, {len(pend)} pendientes -> {OUT}")


if __name__ == "__main__":
    main()
