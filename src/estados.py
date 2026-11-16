"""Estados financieros de la economía personal, como los de una empresa: cuenta de resultados, flujo de caja y balance,
por mes, trimestre y año. Todo en "tu parte" (la conjunta al 50 %) y con criterio de caja (cuenta cuando se cobra o paga).

Cuenta de resultados
  Ingresos                      sueldo, intereses, otros ingresos
  − Coste de vida esencial      grupos esenciales (vivienda, salud y deporte, transporte, gestoría y herramientas)
  = Margen bruto
  − Gasto no esencial           hábitos, ocio, viajes, suscripciones, formación
  = Resultado operativo
  ± Extraordinarios             grupo Otros (ajustes con la pareja, efectivo, imprevistos) y reembolsos sin categoría
  = Resultado antes de impuestos
  − Impuestos                   cuota de autónomos, IRPF/IVA y plazos de deuda con Hacienda
  = Beneficio neto              (igual que el Beneficio del dashboard)

Flujo de caja
  Operativo = beneficio neto + plazos de deuda (se pagan como financiación, no como gasto del periodo)
  Inversión = − aportaciones netas a inversiones
  Financiación = − plazos de deuda pagados
  Variación de caja = la suma de los tres (= beneficio − invertido)

Balance (al cierre de cada periodo)
  Activos: caja en cada cuenta (último saldo conocido), huchas de ahorro de Revolut, inversiones a valor de mercado
           (posiciones de los extractos × precios diarios ajustados al «Extracto del patrimonio neto» de Trade Republic;
           lo que no se puede valorar, a precio de compra)
  Pasivos: deuda con Hacienda pendiente, dinero recibido a devolver
  Patrimonio neto = activos − pasivos
"""
import calendar
import datetime as dt
from collections import defaultdict

import yaml

CUENTAS_REALES = ("Revolut", "Conjunta", "Trade Republic", "BBVA")


def _periodos(primero, ultimo):
    """Claves de periodo entre dos meses 'AAAA-MM' (incluidos)."""
    y, m = map(int, primero.split("-"))
    Y, M = map(int, ultimo.split("-"))
    meses = []
    while (y, m) <= (Y, M):
        meses.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    trim = sorted({f"{x[:4]}-T{(int(x[5:]) - 1) // 3 + 1}" for x in meses})
    anios = sorted({x[:4] for x in meses})
    return {"mensual": meses, "trimestral": trim, "anual": anios}


def _clave(mes, gran):
    if gran == "mensual":
        return mes
    if gran == "trimestral":
        return f"{mes[:4]}-T{(int(mes[5:]) - 1) // 3 + 1}"
    return mes[:4]


def _fin_periodo(p, gran, hoy):
    if gran == "mensual":
        y, m = map(int, p.split("-"))
    elif gran == "trimestral":
        y, m = int(p[:4]), int(p[-1]) * 3
    else:
        y, m = int(p), 12
    fin = dt.date(y, m, calendar.monthrange(y, m)[1])
    return min(fin, hoy)


def _etiqueta(p, gran):
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    if gran == "mensual":
        return f"{meses[int(p[5:]) - 1]} {p[2:4]}"
    if gran == "trimestral":
        return f"{p[-2:]} {p[:4]}"
    return p


def calcular(rows, flat, roles, root, hoy=None):
    hoy = hoy or dt.date.today()
    ajustes = roles.get("ajustes_pareja")
    con_mov = [x for x in rows if x["tipo"] and x["tipo"] != "Traspaso"]
    if not rows:
        return {}
    primero = min(x["mes"] for x in rows)
    ultimo = hoy.strftime("%Y-%m")
    per = _periodos(primero, ultimo)

    # ---------------------------------------------------------------- flujos por periodo
    flujos = {g: defaultdict(lambda: defaultdict(float)) for g in per}

    def sumar(gran, p, linea, sub, v):
        flujos[gran][p][linea] += v
        if sub:
            flujos[gran][p][linea + "|" + sub] += v

    for x in con_mov:
        t, mio = x["tipo"], x["mio"]
        partes = []
        if t == "Ingreso":
            partes.append(("ingresos", x["categoria"] or "Sin categoría", mio))
        elif t == "Inversion":
            partes.append(("inversion", x["categoria"] or "Sin categoría", -mio))
        elif t in ("Gasto", "Reembolso"):
            importes = [(x["categoria"], -mio)]
            if x.get("compensar") and t == "Gasto":
                importes.append((ajustes, -(x["importe"] - mio)))
            for c, v in importes:
                info = flat.get(c) or {}
                rol, grupo = info.get("rol"), info.get("grupo") or "Sin categoría"
                if rol == "impuesto":
                    partes.append(("impuestos", c, v))
                elif rol == "deuda":
                    partes.append(("deuda", c, v))
                elif info.get("esencial") is True:
                    partes.append(("esencial", grupo, v))
                elif info.get("esencial") is False:
                    partes.append(("noesencial", grupo, v))
                else:
                    partes.append(("extra", c or "Sin categoría", v))
        for gran in per:
            p = _clave(x["mes"], gran)
            for linea, sub, v in partes:
                sumar(gran, p, linea, sub, v)

    def r2(v):
        return None if v is None else round(v, 2)

    def subs(f, linea):
        pref = linea + "|"
        return {k[len(pref):]: r2(v) for k, v in sorted(f.items(), key=lambda kv: -abs(kv[1])) if k.startswith(pref)}

    # ---------------------------------------------------------------- balance: datos de partida
    saldos = defaultdict(list)  # cuenta -> [(fecha, saldo)]
    for x in rows:
        if x["cuenta"] in CUENTAS_REALES and x["saldo"] is not None:
            saldos[x["cuenta"]].append((x["fecha"], x["saldo"], x["id"]))
    for c in saldos:
        saldos[c].sort(key=lambda t: (t[0], t[2]))  # por fecha y orden del extracto (no por saldo)
    import cartera as cartera_mod
    cart = cartera_mod.Cartera(rows, root)
    huchas = sorted((x["fecha"], -x["importe"] * (0.5 if x["cuenta"] == "Conjunta" else 1.0))
                    for x in rows if x.get("regla") == "ahorro_revolut")
    inversiones = sorted((x["fecha"], -x["mio"]) for x in rows if x["tipo"] == "Inversion")
    a_devolver = sorted((x["fecha"], x["importe"]) for x in rows if x.get("regla") == "madre" or
                        (x.get("nota") or "").startswith("de tu madre"))
    deudas = []
    dp = root / "config" / "deudas.yaml"
    if dp.exists():
        for d in yaml.safe_load(dp.read_text(encoding="utf-8")) or []:
            deudas.append({"desde": str(d.get("desde") or d["plazos"][0][0]),
                           "plazos": [(str(f), float(i)) for f, i in d["plazos"]],
                           "principal": float(d.get("principal") or 0)})

    def acumulado(lista, fin):
        return sum(v for f, v in lista if f <= fin)

    def balance(fin):
        fin_s = fin.isoformat()
        caja, detalle_caja = 0.0, {}
        for c in CUENTAS_REALES:
            previos = [s for f, s, _ in saldos.get(c, []) if f <= fin_s]
            if previos:
                v = previos[-1] * (0.5 if c == "Conjunta" else 1.0)
                detalle_caja[c + (" (tu 50 %)" if c == "Conjunta" else "")] = r2(v)
                caja += v
        if not detalle_caja:
            caja = None
        hucha = acumulado(huchas, fin_s)
        coste = acumulado(inversiones, fin_s)
        inv = cart.valor(fin_s)[0]
        deuda = 0.0
        for d in deudas:
            if d["desde"] <= fin_s:
                deuda += sum(i for f, i in d["plazos"] if f > fin_s)
        devolver = acumulado(a_devolver, fin_s)
        activos = (caja + hucha + inv) if caja is not None else None  # sin saldos de banco (2025) no hay balance fiable
        pasivos = deuda + max(devolver, 0)
        return {
            "activos": r2(activos), "caja": r2(caja), "activos|caja": detalle_caja, "hucha": r2(hucha), "inversiones": r2(inv),
            "inversiones|": {"Aportado (precio de compra)": r2(coste), "Plusvalías latentes": r2(inv - coste)},
            "pasivos": r2(pasivos), "deuda_hacienda": r2(deuda), "a_devolver": r2(max(devolver, 0)),
            "patrimonio": r2(activos - pasivos) if activos is not None else None, "caja_conocida": "Revolut" in detalle_caja and "Trade Republic" in detalle_caja,  # antes de tener saldos de la cuenta principal el balance no es fiable
        }

    # ---------------------------------------------------------------- montaje
    out = {}
    hist = {x["mes"] for x in rows if x["cuenta"] == "Histórico"}
    for gran, periodos in per.items():
        lista = []
        for p in periodos:
            f = flujos[gran][p]
            ing, ess, noess = f["ingresos"], f["esencial"], f["noesencial"]
            ext, imp, deu, inv = f["extra"], f["impuestos"], f["deuda"], f["inversion"]
            bruto = ing - ess
            oper = bruto - noess
            antes = oper - ext
            neto = antes - imp - deu
            fin = _fin_periodo(p, gran, hoy)
            meses_p = [m for m in per["mensual"] if _clave(m, gran) == p]
            lista.append({
                "periodo": p, "etiqueta": _etiqueta(p, gran), "fin": fin.isoformat(),
                "en_curso": fin >= hoy, "historico": all(m in hist for m in meses_p),
                "resultados": {
                    "ingresos": r2(ing), "ingresos|": subs(f, "ingresos"),
                    "esencial": r2(ess), "esencial|": subs(f, "esencial"),
                    "margen_bruto": r2(bruto), "margen_bruto_pct": r2(bruto / ing * 100) if ing else None,
                    "noesencial": r2(noess), "noesencial|": subs(f, "noesencial"),
                    "operativo": r2(oper), "operativo_pct": r2(oper / ing * 100) if ing else None,
                    "extra": r2(-ext), "extra|": {k: r2(-v) for k, v in subs(f, "extra").items()},
                    "antes_impuestos": r2(antes),
                    "impuestos": r2(imp + deu), "impuestos|": {**subs(f, "impuestos"), **subs(f, "deuda")},
                    "neto": r2(neto), "neto_pct": r2(neto / ing * 100) if ing else None,
                },
                "flujo": {
                    "operativo": r2(neto + deu), "inversion": r2(-inv), "inversion|": {k: r2(-v) for k, v in subs(f, "inversion").items()},
                    "financiacion": r2(-deu), "variacion": r2(neto - inv),
                    "inversion_pct": r2(inv / ing * 100) if ing else None,
                },
                "balance": balance(fin),
            })
        out[gran] = lista

    # 2025 no tiene saldos de banco (hoja antigua): la caja se estima hacia atrás desde el primer cierre con saldos
    # reales, restando la variación de caja de cada mes posterior. Esos balances van marcados como estimados.
    mens = out.get("mensual", [])
    idx = next((i for i, e in enumerate(mens) if e["balance"]["caja_conocida"]), None)
    est = {}
    if idx:
        c = mens[idx]["balance"]["caja"]
        for i in range(idx, 0, -1):
            c -= mens[i]["flujo"]["variacion"]
            est[mens[i - 1]["periodo"]] = c
    for lista in out.values():
        for e in lista:
            b = e["balance"]
            mes = e["fin"][:7]
            if b["caja_conocida"] or mes not in est:
                continue
            caja = est[mes]
            activos = caja + b["hucha"] + b["inversiones"]
            b.update({"caja": r2(caja), "activos|caja": {"Estimada con los flujos del mes": r2(caja)}, "activos": r2(activos),
                      "patrimonio": r2(activos - b["pasivos"]), "estimado": True})

    # ---------------------------------------------------------------- patrimonio neto día a día y velas mensuales
    # Cada vela es un mes: apertura = patrimonio al cierre del mes anterior, cierre = último día, máximo y mínimo = el
    # día más alto y más bajo. Antes de tener saldos diarios (2025) las velas son estimadas y sin mecha.
    inicio = next((d for d in (dt.date.fromisoformat(f) for f in sorted({x["fecha"] for x in rows}))
                   if all(any(f <= d.isoformat() for f, _, _ in saldos.get(c, [])) for c in ("Revolut", "Trade Republic"))), None)
    diario = []
    if inicio:
        d = inicio
        while d <= hoy:
            b = balance(d)
            diario.append((d.isoformat(), b["patrimonio"], b["caja"], b["hucha"], b["inversiones"], b["pasivos"]))
            d += dt.timedelta(days=1)
    velas, previo = [], None
    for e in mens:
        b, mes = e["balance"], e["periodo"]
        dias = [x for x in diario if x[0][:7] == mes]
        if dias:
            o = previo if previo is not None else dias[0][1]
            vals = [o] + [x[1] for x in dias]
            c = dias[-1][1]
            velas.append({"mes": mes, "etiqueta": e["etiqueta"], "o": r2(o), "h": r2(max(vals)), "l": r2(min(vals)), "c": r2(c),
                          "caja": r2(dias[-1][2] + dias[-1][3]), "inversiones": r2(dias[-1][4]), "pasivos": r2(dias[-1][5]),
                          "aportado": r2(acumulado(inversiones, dias[-1][0])), "en_curso": e["en_curso"]})
            previo = c
        elif b.get("patrimonio") is not None:
            c = b["patrimonio"]
            o = previo if previo is not None else c
            velas.append({"mes": mes, "etiqueta": e["etiqueta"], "o": r2(o), "h": r2(max(o, c)), "l": r2(min(o, c)), "c": r2(c),
                          "caja": b["caja"], "inversiones": b["inversiones"], "pasivos": b["pasivos"],
                          "aportado": r2(acumulado(inversiones, e["fin"])), "estimado": True, "en_curso": e["en_curso"]})
            previo = c
    out["patrimonio"] = {
        "velas": velas,
        "diario": [[f, r2(v)] for f, v, *_ in diario],
        "posiciones": cart.posiciones(hoy.isoformat()),
        "fotos_tr": [{"fecha": f, "total": x["total"], "valores": x["valores"], "crypto": x["crypto"], "efectivo": x["efectivo"]}
                     for f, x in sorted(cart.fotos.items())],
        "descuadres": cart.descuadres,
    }
    return out
