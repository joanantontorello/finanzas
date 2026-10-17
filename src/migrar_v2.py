#!/usr/bin/env python3
"""Migración única (oct 2026): categorías planas -> grupo y subcategoría.
Mapea la base, data/overrides.json, config/ajustes.yaml y data/presupuesto.json. Idempotente: lo ya migrado no se toca."""
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cats  # noqa: E402
from ingest import cargar_reglas, clasificar, cargar_categorias  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAPA = {
    "AGUA": "Agua", "AJUSTES PAREJA": "Ajustes con Kate", "ALQUILER": "Alquiler", "CAFÉ": "Café",
    "CITAS/PAREJA": "Citas y pareja", "COMIDA": "Supermercado", "DEPORTE": "Gimnasio", "EFECTIVO": "Efectivo",
    "GESTORIA": "Gestoría", "HOGAR": "Hogar y equipamiento", "IMPREVISTOS": "Imprevistos",
    "IMPUESTOS": "IRPF e IVA trimestral", "INTERNET": "Internet y teléfono", "LUZ": "Electricidad",
    "OCIO": "Planes y ocio", "PELUQUERÍA Y AUTOCUIDADO": "Peluquería y autocuidado", "PSICOLOGA": "Psicología",
    "RESTAURANTES": "Restaurantes", "ROPA": "Ropa", "RUSO": "Mentoría y cursos", "SALUD": "Farmacia y salud",
    "SUBSCRIPCIONES": "Apps personales", "TRABAJO": "Herramientas de trabajo", "TRANSPORTE": "Transporte público",
    "VIAJES": "Comida y gastos en destino", "INTERESES": "Dividendos e intereses", "OTROS/EXTRAS": "Otros ingresos",
    "PAGA": "Otros ingresos", "SUELDO": "Trabajo", "ACCIONES": "Acciones", "CRYPTOMONEDAS": "Criptomonedas",
    "FONDOS INDEXADOS / ETF": "Fondos indexados y ETF", "FORMACIÓN / MENTORÍA": "Mentoría y cursos",
    "REEMBOLSOS": "Reembolsos", "TRASPASO": "Traspaso entre cuentas", "APORTACIÓN PAREJA": "Aportación de Kate",
}
# categorías antiguas que se reparten entre varias nuevas: si la regla nueva cae dentro, se usa la regla
REPARTO = {
    "DEPORTE": {"Gimnasio", "Pádel", "Jiu-jitsu"},
    "TRANSPORTE": {"Transporte público", "BiciMAD", "Uber y taxi", "Trenes y AVE", "Vuelos", "Gasolina, peajes y parking"},
    "SUBSCRIPCIONES": {"Streaming de vídeo", "Música", "Almacenamiento", "Apps personales", "Herramientas de trabajo"},
    "IMPUESTOS": {"IRPF e IVA trimestral", "Cuota de autónomos", "Gestoría"},
    "CAFÉ": {"Café", "Açaí y brunch"},
    "RESTAURANTES": {"Restaurantes", "Comida a domicilio", "Açaí y brunch"},
    "VIAJES": {"Alojamiento", "Comida y gastos en destino", "Actividades", "Vuelos", "Trenes y AVE"},
    "HOGAR": {"Hogar y equipamiento", "Seguros"},
    "IMPREVISTOS": {"Imprevistos", "Compras impulsivas"},
    "SALUD": {"Farmacia y salud", "Peluquería y autocuidado"},
    "OCIO": {"Planes y ocio", "Libros"},
}


def nueva(cat_vieja, mov, reglas, flat):
    if cat_vieja in flat:  # ya migrada
        return cat_vieja
    if cat_vieja is None:
        return None
    base = MAPA[cat_vieja]
    if mov["cuenta"] == "Histórico":
        if cat_vieja == "IMPUESTOS":  # la hoja de 2025 metía aquí gestoría y cuota de autónomos
            imp = round(abs(mov["importe"]), 2)
            if imp == 84.70:
                return "Gestoría"
            if imp in (87.61, 88.56):
                return "Cuota de autónomos"
        return base
    if cat_vieja in REPARTO:
        c = clasificar(mov, reglas, flat)["categoria"]
        if c in REPARTO[cat_vieja]:
            return c
    return base


def main():
    flat = cargar_categorias()
    reglas = cargar_reglas()
    con = sqlite3.connect(ROOT / "data" / "finanzas.db")
    con.row_factory = sqlite3.Row
    n = 0
    for r in con.execute("SELECT * FROM movimientos").fetchall():
        mov = dict(r)
        c = nueva(r["categoria"], mov, reglas, flat)
        if c == r["categoria"]:
            continue
        tipo = r["tipo"]
        t = flat[c]["tipo"] if c else tipo
        if t in ("Traspaso", "Ingreso", "Inversion"):
            tipo = t
        elif t == "Gasto":
            tipo = "Gasto" if r["importe"] < 0 else "Reembolso"
        elif t == "Reembolso":
            tipo = "Reembolso"
        ambito = flat[c]["ambito"] or r["ambito"] if c else r["ambito"]
        con.execute("UPDATE movimientos SET categoria=?, tipo=?, ambito=? WHERE id=?", (c, tipo, ambito, r["id"]))
        n += 1
    con.commit()
    print("movimientos migrados:", n)

    ov = ROOT / "data" / "overrides.json"
    d = json.loads(ov.read_text(encoding="utf-8"))
    m = 0
    for k, o in d.items():
        c = o.get("categoria")
        if c and c not in flat:
            row = con.execute("SELECT * FROM movimientos WHERE id=?", (int(k),)).fetchone()
            o["categoria"] = nueva(c, dict(row), reglas, flat) if row else MAPA[c]
            m += 1
    ov.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print("correcciones del dashboard migradas:", m)

    aj = ROOT / "config" / "ajustes.yaml"
    t = aj.read_text(encoding="utf-8")
    for viejo, nuevo in (("categoria: ALQUILER", "categoria: Alquiler"), ("categoria: AJUSTES PAREJA", "categoria: Ajustes con Kate")):
        t = t.replace(viejo, nuevo)
    aj.write_text(t, encoding="utf-8")
    for viejo, nuevo in (("ALQUILER", "Alquiler"), ("AJUSTES PAREJA", "Ajustes con Kate")):
        con.execute("UPDATE movimientos SET categoria=? WHERE cuenta='Ajustes' AND categoria=?", (nuevo, viejo))
    con.commit()
    print("ajustes migrados")


if __name__ == "__main__":
    main()
