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
def detect(path):
    """Devuelve (parser, cuenta) según el prefijo del nombre del fichero."""
    name = pathlib.Path(path).name.lower()
    if name.startswith("revolut"):
        return lambda p: parse_revolut(p, "Revolut"), "Revolut"
    if name.startswith("conjunta"):
        return lambda p: parse_revolut(p, "Conjunta"), "Conjunta"
    if name.startswith("traderepublic") or name.startswith("tr_"):
        return parse_traderepublic, "Trade Republic"
    if name.startswith("bbva"):
        return parse_bbva, "BBVA"
    if name.startswith("historico_"):
        anio = int(re.search(r"historico_(\d{4})", name).group(1))
        return lambda p: parse_historico(p, anio), "Histórico"
    raise ValueError(f"No sé qué banco es {name}. Prefijos válidos: revolut_, conjunta_, traderepublic_, bbva_, historico_AAAA")
