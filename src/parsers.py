"""Parsers de los extractos bancarios. Cada parser devuelve una lista de dicts:
{cuenta, fecha (YYYY-MM-DD), concepto, importe (float con signo), saldo (float|None), clave (str única dentro del fichero)}
"""
import csv
import re
import pathlib
from datetime import datetime

MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12,
}
MESES_LARGO = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def eur(s):
    """'1.234,56 €' -> 1234.56"""
    s = str(s).replace("€", "").replace(" ", "").strip()
    return float(s.replace(".", "").replace(",", "."))


# --------------------------------------------------------------------------- Revolut (personal y conjunta)
def parse_revolut(path, cuenta):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["State"] != "COMPLETED":
                continue  # REVERTED no ocurrió; PENDING aparecerá como COMPLETED en el próximo export
            amount = float(r["Amount"])
            fee = float(r["Fee"] or 0)
            importe = round(amount - fee, 2)
            started = r["Started Date"]
            fecha = started[:10]
            concepto = f"[{r['Type']}] {r['Description']}"
            rows.append({
                "cuenta": cuenta,
                "fecha": fecha,
                "concepto": concepto,
                "importe": importe,
                "saldo": float(r["Balance"]) if r["Balance"] else None,
                "clave": f"{started}|{r['Description']}|{amount}",
            })
    return rows


# --------------------------------------------------------------------------- Trade Republic (PDF)
TR_DATE = re.compile(r"(\d{1,2})\s+(ene|feb|mar|abr|may|jun|jul|ago|sept|sep|oct|nov|dic)\.?\s+(\d{4})", re.I)
TR_TIPOS = ["Transacción con tarjeta", "Transferencia", "Interés", "Operar", "Bonificación",
            "Rentabilidad", "Recibos domiciliados", "Comisión", "Reembolso", "Devolución", "Retirada", "Depósito"]
TR_AMOUNT = re.compile(r"(-?\d{1,3}(?:\.\d{3})*,\d{2}) €\s+(-?\d{1,3}(?:\.\d{3})*,\d{2}) €\s*$")


def _tr_clean_page(text):
    """Quita cabeceras/pies de página y devuelve solo el bloque de transacciones."""
    # cortar en la cabecera de la tabla
    m = re.search(r"FECHA TIPO DESCRIPCIÓN ENTRADA DE\s+DINERO\s+SALIDA DE\s+DINERO BALANCE", text)
    if not m:
        return ""
    body = text[m.end():]
    # cortar al llegar al resumen final
    for marker in ("RESUMEN DEL BALANCE", "NOTAS SOBRE EL EXTRACTO"):
        i = body.find(marker)
        if i != -1:
            body = body[:i]
    return body


def parse_traderepublic(path, cuenta="Trade Republic"):
    import pypdf
    reader = pypdf.PdfReader(str(path))
    body = " ".join(_tr_clean_page(p.extract_text()) for p in reader.pages)
    body = re.sub(r"\s+", " ", body)
    # posiciones de cada fecha que arranca una transacción (seguida de un tipo conocido)
    starts = []
    for m in TR_DATE.finditer(body):
        rest = body[m.end():m.end() + 30].lstrip()
        if any(rest.startswith(t) for t in TR_TIPOS):
            starts.append(m)
    rows = []
    prev_balance = None
    # balance inicial del resumen
    m0 = re.search(r"Cuenta corriente\s+(-?[\d.]+,\d{2}) €", body)
    for idx, m in enumerate(starts):
        end = starts[idx + 1].start() if idx + 1 < len(starts) else len(body)
        chunk = body[m.end():end].strip()
        d, mon, y = int(m.group(1)), MESES[m.group(2).lower()], int(m.group(3))
        fecha = f"{y:04d}-{mon:02d}-{d:02d}"
        am = TR_AMOUNT.search(chunk)
        if not am:
            raise ValueError(f"No encuentro importes en: {chunk[:120]}")
        amount, balance = eur(am.group(1)), eur(am.group(2))
        desc = chunk[:am.start()].strip()
        tipo = next((t for t in TR_TIPOS if desc.startswith(t)), "")
        desc = desc[len(tipo):].strip()
        if prev_balance is None:
            # primera transacción: signo por diferencia con el balance inicial si lo hay
            sign = 1
            if m0:
                sign = 1 if balance > eur(m0.group(1)) else -1
        else:
            sign = 1 if balance > prev_balance else -1
        importe = round(sign * amount, 2)
        # comprobación de coherencia
        if prev_balance is not None and abs(round(prev_balance + importe, 2) - balance) > 0.011:
            raise ValueError(f"Descuadre en {fecha} {desc}: {prev_balance} + {importe} != {balance}")
        prev_balance = balance
        rows.append({
            "cuenta": cuenta,
            "fecha": fecha,
            "concepto": f"[{tipo}] {desc}",
            "importe": importe,
            "saldo": balance,
            "clave": f"{fecha}|{desc}|{importe}|{balance}",
        })
    return rows


# --------------------------------------------------------------------------- BBVA (xlsx "Últimos movimientos")
def parse_bbva(path, cuenta="BBVA"):
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True)
    ws = wb.worksheets[0]
    rows, header = [], None
    for r in ws.iter_rows(values_only=True):
        vals = list(r)
        if header is None:
            if "Fecha" in vals and "Importe" in vals:
                header = {name: i for i, name in enumerate(vals) if name}
            continue
        if not vals[header["Fecha"]]:
            continue
        fecha = datetime.strptime(str(vals[header["Fecha"]]), "%d/%m/%Y").strftime("%Y-%m-%d")
        concepto = str(vals[header["Concepto"]] or "").strip()
        mov = str(vals[header["Movimiento"]] or "").strip()
        obs = str(vals[header.get("Observaciones", -1)] or "").strip() if "Observaciones" in header else ""
        importe = round(float(vals[header["Importe"]]), 2)
        saldo = vals[header["Disponible"]]
        rows.append({
            "cuenta": cuenta,
            "fecha": fecha,
            "concepto": f"{concepto} | {mov}" + (f" | {obs}" if obs and obs.lower() != mov.lower() else ""),
            "importe": importe,
            "saldo": float(saldo) if saldo is not None else None,
            "clave": f"{fecha}|{concepto}|{mov}|{importe}|{saldo}",
        })
    return rows


# --------------------------------------------------------------------------- Revolut en PDF (personal y conjunta)
RV_MES = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6, "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
RV_FECHA = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}), (\d{4})"
RV_LINEA = re.compile(r"^" + RV_FECHA + r" " + RV_FECHA + r" (.+?) €([\d,]+\.\d{2}) €(-?[\d,]+\.\d{2})$")
RV_REF_INUTIL = re.compile(r"^(Sent from Revolut|REF-|\d{2}-\d{2}-\d{4} / )", re.I)
_RV_M = "(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
RV_GB = re.compile(r"^(\d{1,2}) " + _RV_M + r" (\d{4}) (\d{1,2}) " + _RV_M + r" (\d{4}) ")  # "2 Nov 2026" (extracto en-gb)
RV_INICIO = re.compile(r"^" + RV_FECHA + r" " + RV_FECHA + r" ")


def _rv_lineas(lineas):
    """Pasa las fechas en-gb ("2 Nov 2026") al formato en-us ("Nov 2, 2026") y une las descripciones largas que el PDF
    parte en varias líneas, con los importes en la última."""
    out, buf = [], None
    for l in lineas:
        l = RV_GB.sub(r"\2 \1, \3 \5 \4, \6 ", l.strip())
        if buf is not None:
            buf = " ".join((buf + " " + l).split())
            if RV_LINEA.match(buf):
                out.append(buf); buf = None
                continue
            if buf.count(" ") > 40:  # no era una fila partida
                out.append(buf); buf = None
            continue
        if RV_INICIO.match(l) and not RV_LINEA.match(l):
            buf = l
            continue
        out.append(l)
    if buf is not None:
        out.append(buf)
    return out


def _rv_num(x):
    return float(x.replace(",", ""))


def parse_revolut_pdf(path, cuenta):
    """Extracto PDF de Revolut. Solo la sección "Account transactions": se salta los pendientes (una sola fecha) y el
    apartado "Deposit transactions", que repite los traspasos a la hucha de ahorro. El signo sale de la cadena de saldos."""
    import pypdf
    lineas = _rv_lineas("\n".join(pg.extract_text() for pg in pypdf.PdfReader(str(path)).pages).splitlines())
    m0 = next((re.search(r"Account \(Current Account\) €(-?[\d,]+\.\d{2})", l) for l in lineas if l.startswith("Account (Current Account)")), None)
    saldo_prev = _rv_num(m0.group(1)) if m0 else None
    rows, dentro, actual = [], False, None

    def cerrar():
        if not actual:
            return
        d = actual
        tipo = ("Card Refund" if d["importe"] > 0 else "Card Payment") if d["card"] else \
               ("Topup" if re.match(r"(Apple Pay top-up|Money added|Payment from)", d["desc"]) else "Transfer")
        concepto = f"[{tipo}] {d['desc']}" + (f" · Ref: {d['ref']}" if d["ref"] and not RV_REF_INUTIL.match(d["ref"]) else "")
        rows.append({"cuenta": cuenta, "fecha": d["fecha"], "concepto": concepto, "importe": d["importe"], "saldo": d["saldo"],
                     "clave": f"pdf|{d['fecha']}|{d['desc']}|{d['importe']}|{d['saldo']}"})

    for l in lineas:
        if l.startswith("Account transactions from"):
            dentro = True
            continue
        if l.startswith(("Deposit transactions from", "Pending from")):
            cerrar(); actual = None
            dentro = False
            continue
        if not dentro:
            continue
        m = RV_LINEA.match(l.strip())
        if m:
            cerrar()
            mes, dia, anio = RV_MES[m.group(1)], int(m.group(2)), int(m.group(3))
            imp, saldo = _rv_num(m.group(8)), _rv_num(m.group(9))
            signo = 1 if (saldo_prev is not None and saldo > saldo_prev) else -1
            if saldo_prev is not None and abs(round(saldo_prev + signo * imp, 2) - saldo) > 0.011:
                raise ValueError(f"Revolut PDF descuadra en {l!r}: {saldo_prev} ± {imp} != {saldo}")
            saldo_prev = saldo
            actual = {"fecha": f"{anio:04d}-{mes:02d}-{dia:02d}", "desc": m.group(7).strip(), "importe": round(signo * imp, 2),
                      "saldo": saldo, "card": False, "ref": None}
        elif actual and l.startswith("Card:"):
            actual["card"] = True
        elif actual and l.startswith("Reference:"):
            actual["ref"] = l[len("Reference:"):].strip()
    cerrar()
    return rows


# --------------------------------------------------------------------------- BBVA en PDF ("Últimos movimientos" desde la app)
BB_FECHA = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
BB_IMPORTE = re.compile(r"^(-?[\d .]+,\d{2}) €\s+(-?[\d .]+,\d{2}) €$")


def parse_bbva_pdf(path, cuenta="BBVA"):
    import pypdf
    lineas = [l.strip() for pg in pypdf.PdfReader(str(path)).pages for l in pg.extract_text().splitlines()]
    rows, i = [], 0
    while i < len(lineas):
        m = BB_FECHA.match(lineas[i])
        if m and i + 1 < len(lineas) and lineas[i + 1].startswith("Fecha valor"):
            fecha = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            j, partes = i + 2, []
            while j < len(lineas) and not BB_IMPORTE.match(lineas[j]):
                partes.append(lineas[j]); j += 1
            if j >= len(lineas):
                break
            mi = BB_IMPORTE.match(lineas[j])
            importe = eur(mi.group(1).replace(" ", "")); saldo = eur(mi.group(2).replace(" ", ""))
            concepto = " | ".join(partes)
            rows.append({"cuenta": cuenta, "fecha": fecha, "concepto": concepto, "importe": round(importe, 2), "saldo": saldo,
                         "clave": f"pdf|{fecha}|{concepto}|{importe}|{saldo}"})
            i = j + 1
        else:
            i += 1
    return rows


# --------------------------------------------------------------------------- Histórico (hoja de años anteriores)
MAPA_CATEGORIAS_HISTORICO = {
    "GIMNASIO/SALUD": "DEPORTE",
    "INVERSIONES": "INTERESES",
    "REEMBOLSOS/DEVOLUCIONES": "REEMBOLSOS",
    "INTERES TAE": "INTERESES",
    "GIMNASIO": "DEPORTE",
}


def parse_historico(path, anio, cuenta="Histórico"):
    """CSV con columnas MES,TIPO,CATEGORIA,IMPORTE (la hoja antigua). Devuelve filas ya clasificadas."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f)):
            mes = MESES_LARGO[r["MES"].strip().lower()]
            tipo_raw = r["TIPO"].strip()
            cat = (r["CATEGORIA"] or "").strip().upper()
            cat = MAPA_CATEGORIAS_HISTORICO.get(cat, cat)
            imp = eur(r["IMPORTE"])
            if tipo_raw == "Ingreso" and cat == "REEMBOLSOS":
                tipo, importe = "Reembolso", imp
            elif tipo_raw == "Ingreso":
                tipo, importe = "Ingreso", imp
            elif tipo_raw == "Gasto":
                tipo, importe = "Gasto", -imp
            elif tipo_raw.startswith("Inversi"):
                tipo, importe = "Inversion", -imp
            elif tipo_raw == "Traspaso":
                tipo, importe, cat = "Traspaso", -imp, "TRASPASO"
            else:
                raise ValueError(f"Tipo desconocido en histórico: {tipo_raw}")
            ambito = "Trabajo" if cat in ("TRABAJO", "SUELDO", "IMPUESTOS", "GESTORIA") else "Personal"
            rows.append({
                "cuenta": cuenta,
                "fecha": f"{anio}-{mes:02d}-01",
                "concepto": f"(histórico {anio}) {tipo_raw} {cat}",
                "importe": importe,
                "saldo": None,
                "clave": f"{anio}|{i}|{mes}|{tipo_raw}|{cat}|{imp}",
                "tipo": tipo, "categoria": cat, "ambito": ambito, "estado": "revisado", "regla": "historico",
            })
    return rows


# --------------------------------------------------------------------------- detección por nombre de fichero
# --------------------------------------------------------------------------- Trade Republic: extracto del patrimonio neto
CRIPTO_ISIN = {"BTC": "XF000BTC0017", "ETH": "XF000ETH0019"}
_NUM = r"\d[\d.]*(?:,\d+)?"


def es_patrimonio_tr(path):
    """El «Extracto del patrimonio neto» de Trade Republic (foto de la cartera a una fecha), no un extracto de movimientos."""
    if not str(path).lower().endswith(".pdf"):
        return False
    import pypdf
    try:
        return "EXTRACTO DEL PATRIMONIO NETO" in (pypdf.PdfReader(str(path)).pages[0].extract_text() or "")
    except Exception:
        return False


def parse_tr_patrimonio(path):
    """{fecha, valores, crypto, efectivo, total, posiciones: [{isin, nombre, unidades, precio, fecha_precio, valor}]}"""
    import pypdf
    texto = " ".join(" ".join((pg.extract_text() or "").split()) for pg in pypdf.PdfReader(str(path)).pages)
    f = re.search(r"EXTRACTO DEL PATRIMONIO NETO a (\d{2})\.(\d{2})\.(\d{4})", texto)
    fecha = f"{f.group(3)}-{f.group(2)}-{f.group(1)}"
    tot = lambda etiqueta: eur(re.search(etiqueta + r" (" + _NUM + ")", texto).group(1))
    snap = {"fecha": fecha, "valores": tot("Cuenta de valores"), "crypto": tot("Wallet Crypto"), "efectivo": tot("Efectivo"),
            "total": tot("TOTAL"), "posiciones": []}
    pos = re.finditer(r"(" + _NUM + r") unidades (.+?) (" + _NUM + r") (\d{2})\.(\d{2})\.(\d{4}) (" + _NUM + r")", texto)
    for m in pos:
        nombre = m.group(2)
        isin = re.search(r"ISIN: ([A-Z]{2}[A-Z0-9]{9}\d)", nombre)
        sim = nombre.split()[-1]
        isin = isin.group(1) if isin else CRIPTO_ISIN.get(sim)
        nombre = re.sub(r" ISIN: .*", "", nombre).strip()
        snap["posiciones"].append({"isin": isin, "nombre": nombre, "unidades": eur(m.group(1)), "precio": eur(m.group(3)),
                                   "fecha_precio": f"{m.group(6)}-{m.group(5)}-{m.group(4)}", "valor": eur(m.group(7))})
    suma = round(sum(x["valor"] for x in snap["posiciones"]), 2)
    if abs(suma - round(snap["valores"] + snap["crypto"], 2)) > 0.05:
        raise ValueError(f"Extracto de patrimonio: las posiciones suman {suma} y el total de valores + cripto es {snap['valores'] + snap['crypto']}")
    return snap


def detect(path):
    """Devuelve (parser, cuenta) según el prefijo del nombre del fichero."""
    name = pathlib.Path(path).name.lower()
    pdf = name.endswith(".pdf")
    if name.startswith("revolut"):
        return (lambda p: (parse_revolut_pdf if pdf else parse_revolut)(p, "Revolut")), "Revolut"
    if name.startswith("conjunta"):
        return (lambda p: (parse_revolut_pdf if pdf else parse_revolut)(p, "Conjunta")), "Conjunta"
    if name.startswith("traderepublic") or name.startswith("tr_"):
        return parse_traderepublic, "Trade Republic"
    if name.startswith("bbva"):
        return (parse_bbva_pdf if pdf else parse_bbva), "BBVA"
    if name.startswith("historico_"):
        anio = int(re.search(r"historico_(\d{4})", name).group(1))
        return lambda p: parse_historico(p, anio), "Histórico"
    raise ValueError(f"No sé qué banco es {name}. Prefijos válidos: revolut_, conjunta_, traderepublic_, bbva_, historico_AAAA")
