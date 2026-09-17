#!/usr/bin/env python3
"""Importa extractos de inbox/ a la base de datos y los clasifica con config/reglas.yaml.

Uso:
  python3 src/ingest.py                    # importa todo lo que haya en inbox/ y reclasifica
  python3 src/ingest.py inbox/fichero.csv  # importa un fichero concreto
  python3 src/ingest.py --reclasificar     # vuelve a aplicar las reglas a lo no revisado a mano
  python3 src/ingest.py --pendientes       # lista lo que queda por revisar
  python3 src/ingest.py set ID TIPO CATEGORIA [AMBITO] [NOTA]   # corrige un movimiento a mano (estado=revisado)
  python3 src/ingest.py --overrides        # aplica data/overrides.json (correcciones hechas desde el dashboard)
"""
import hashlib
import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import connect, ROOT  # noqa: E402
from parsers import detect  # noqa: E402

CONFIG = ROOT / "config"
INBOX = ROOT / "inbox"


def cargar_reglas():
    reglas = yaml.safe_load((CONFIG / "reglas.yaml").read_text(encoding="utf-8"))
    for r in reglas:
        r["_re"] = re.compile(r["match"], re.I)
        if "cuenta" in r and isinstance(r["cuenta"], str):
            r["cuenta"] = [r["cuenta"]]
    return reglas


def cargar_cuentas():
    return yaml.safe_load((CONFIG / "cuentas.yaml").read_text(encoding="utf-8"))


def cargar_categorias():
    cats = yaml.safe_load((CONFIG / "categorias.yaml").read_text(encoding="utf-8"))
    flat = {}
    for tipo, d in cats.items():
        for nombre, props in (d or {}).items():
            flat[nombre] = dict(props or {}, tipo=tipo)
    return flat


def clasificar(mov, reglas, categorias):
    """Devuelve dict con tipo, categoria, ambito, estado, regla para un movimiento."""
    for r in reglas:
        if "cuenta" in r and mov["cuenta"] not in r["cuenta"]:
            continue
        if "desde" in r and mov["fecha"] < str(r["desde"]):
            continue
        if "hasta" in r and mov["fecha"] > str(r["hasta"]):
            continue
        if "min" in r and abs(mov["importe"]) < r["min"]:
            continue
        if "max" in r and abs(mov["importe"]) > r["max"]:
            continue
        if "signo" in r and ((r["signo"] == "+") != (mov["importe"] > 0)):
            continue
        if not r["_re"].search(mov["concepto"]):
            continue
        cat = r.get("categoria")
        ambito = r.get("ambito") or categorias.get(cat, {}).get("ambito", "Personal")
        return {"tipo": r["tipo"], "categoria": cat, "ambito": ambito, "participacion": r.get("participacion"),
                "estado": r.get("estado", "auto"), "regla": r["id"], "nota": r.get("nota")}
    # sin regla: gasto si sale dinero. Lo pequeño (<30 €) va a IMPREVISTOS sin molestar; lo grande queda pendiente.
    if mov["importe"] < 0 and abs(mov["importe"]) < 30:
        return {"tipo": "Gasto", "categoria": "IMPREVISTOS", "ambito": "Personal", "estado": "auto",
                "regla": "fallback_pequeno", "nota": "sin regla, importe pequeño"}
    return {"tipo": "Gasto" if mov["importe"] < 0 else None, "categoria": None,
            "ambito": "Personal", "estado": "pendiente", "regla": None, "nota": None}


def hash_mov(cuenta, clave):
    return hashlib.sha1(f"{cuenta}|{clave}".encode("utf-8")).hexdigest()[:20]


def importar(path, con, reglas, cuentas, categorias):
    parser, cuenta = detect(path)
    filas = parser(path)
    part = cuentas.get(cuenta, {}).get("participacion", 1.0)
    nuevas = 0
    for f in filas:
        h = hash_mov(f["cuenta"], f["clave"])
        if con.execute("SELECT 1 FROM movimientos WHERE hash=?", (h,)).fetchone():
            continue
        if f.get("tipo"):  # ya viene clasificado (histórico)
            c = {k: f.get(k) for k in ("tipo", "categoria", "ambito", "estado", "regla")}
            c["nota"] = None
        else:
            c = clasificar(f, reglas, categorias)
        p = 1.0 if c["tipo"] in ("Traspaso",) else (c.get("participacion") or part)
        con.execute(
            """INSERT INTO movimientos (hash,cuenta,fecha,mes,concepto,importe,saldo,tipo,categoria,ambito,participacion,estado,regla,nota,fuente)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (h, f["cuenta"], f["fecha"], f["fecha"][:7], f["concepto"], f["importe"], f.get("saldo"),
             c["tipo"], c["categoria"], c["ambito"], p, c["estado"], c["regla"], c["nota"], pathlib.Path(path).name),
        )
        nuevas += 1
    fechas = sorted(f["fecha"] for f in filas)
    ultimo = max(filas, key=lambda f: f["fecha"]) if filas else None
    con.execute(
        "INSERT INTO importaciones (fichero,cuenta,filas,nuevas,fecha_min,fecha_max,saldo_final) VALUES (?,?,?,?,?,?,?)",
        (pathlib.Path(path).name, cuenta, len(filas), nuevas, fechas[0] if fechas else None,
         fechas[-1] if fechas else None, ultimo["saldo"] if ultimo else None),
    )
    con.commit()
    return cuenta, len(filas), nuevas, fechas[0] if fechas else None, fechas[-1] if fechas else None


def reclasificar(con, reglas, cuentas, categorias):
    """Vuelve a aplicar las reglas a todo lo que no se haya revisado a mano."""
    cambios = 0
    for row in con.execute("SELECT * FROM movimientos WHERE estado != 'revisado' AND cuenta != 'Histórico'").fetchall():
        c = clasificar(dict(row), reglas, categorias)
        part = 1.0 if c["tipo"] == "Traspaso" else (c.get("participacion") or cuentas.get(row["cuenta"], {}).get("participacion", 1.0))
        if (c["tipo"], c["categoria"], c["ambito"], c["estado"], c["regla"], part) != (row["tipo"], row["categoria"], row["ambito"], row["estado"], row["regla"], row["participacion"]):
            con.execute("UPDATE movimientos SET tipo=?,categoria=?,ambito=?,estado=?,regla=?,nota=?,participacion=? WHERE id=?",
                        (c["tipo"], c["categoria"], c["ambito"], c["estado"], c["regla"], c["nota"], part, row["id"]))
            cambios += 1
    con.commit()
    return cambios


def importar_ajustes(con):
    """Movimientos manuales de config/ajustes.yaml (cuenta 'Ajustes'). Idempotente por contenido."""
    path = CONFIG / "ajustes.yaml"
    if not path.exists():
        return 0
    nuevas = 0
    for a in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
        fecha = str(a["fecha"])
        h = hash_mov("Ajustes", f"{fecha}|{a['concepto']}|{a['importe']}")
        if con.execute("SELECT 1 FROM movimientos WHERE hash=?", (h,)).fetchone():
            continue
        con.execute(
            """INSERT INTO movimientos (hash,cuenta,fecha,mes,concepto,importe,saldo,tipo,categoria,ambito,participacion,estado,regla,nota,fuente)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (h, "Ajustes", fecha, fecha[:7], a["concepto"], float(a["importe"]), None, a["tipo"], a["categoria"],
             a.get("ambito", "Personal"), 1.0, "revisado", "ajuste_manual", a.get("nota"), "ajustes.yaml"))
        nuevas += 1
    con.commit()
    return nuevas


OVERRIDES = ROOT / "data" / "overrides.json"


def aplicar_overrides(con):
    """Correcciones hechas desde el dashboard (data/overrides.json: {id: {categoria, tipo, ambito, nota, participacion, mes}})."""
    import json
    if not OVERRIDES.exists():
        return 0
    data = json.loads(OVERRIDES.read_text(encoding="utf-8") or "{}")
    n = 0
    for id_, o in data.items():
        row = con.execute("SELECT * FROM movimientos WHERE id=?", (int(id_),)).fetchone()
        if not row:
            continue
        campos = {k: o[k] for k in ("categoria", "tipo", "ambito", "nota", "participacion", "mes") if k in o and o[k] not in (None, "")}
        if not campos:
            continue
        sets = ", ".join(f"{k}=?" for k in campos) + ", estado='revisado', regla='dashboard'"
        con.execute(f"UPDATE movimientos SET {sets} WHERE id=?", (*campos.values(), int(id_)))
        n += 1
    con.commit()
    return n


def pendientes(con):
    rows = con.execute("SELECT id,cuenta,fecha,importe,concepto,tipo,categoria,nota FROM movimientos WHERE estado='pendiente' ORDER BY fecha").fetchall()
    for r in rows:
        print(f"#{r['id']:<5} {r['fecha']} {r['cuenta']:<14} {r['importe']:>9.2f}  {r['concepto'][:70]:<70} -> {r['tipo'] or '?'}/{r['categoria'] or '?'}  {r['nota'] or ''}")
    print(f"\n{len(rows)} pendientes")
    return rows


def set_manual(con, id_, tipo, categoria, ambito=None, nota=None):
    con.execute("UPDATE movimientos SET tipo=?,categoria=?,ambito=COALESCE(?,ambito),nota=COALESCE(?,nota),estado='revisado' WHERE id=?",
                (tipo, categoria, ambito, nota, id_))
    con.commit()


def main(argv):
    con = connect()
    reglas, cuentas, categorias = cargar_reglas(), cargar_cuentas(), cargar_categorias()
    if argv and argv[0] == "set":
        set_manual(con, int(argv[1]), argv[2], argv[3], argv[4] if len(argv) > 4 else None, argv[5] if len(argv) > 5 else None)
        print("ok")
        return
    if argv and argv[0] == "--pendientes":
        pendientes(con)
        return
    if argv and argv[0] == "--reclasificar":
        print("reclasificados:", reclasificar(con, reglas, cuentas, categorias))
        print("ajustes nuevos:", importar_ajustes(con), "| overrides aplicados:", aplicar_overrides(con))
        pendientes(con)
        return
    if argv and argv[0] == "--overrides":
        print("overrides aplicados:", aplicar_overrides(con))
        return
    files = [pathlib.Path(a) for a in argv] or sorted(p for p in INBOX.iterdir() if p.suffix.lower() in (".csv", ".pdf", ".xlsx", ".xls"))
    for f in files:
        cuenta, n, nuevas, fmin, fmax = importar(str(f), con, reglas, cuentas, categorias)
        print(f"{f.name}: {cuenta} {n} filas, {nuevas} nuevas ({fmin} .. {fmax})")
    print("reclasificados:", reclasificar(con, reglas, cuentas, categorias))
    print("ajustes nuevos:", importar_ajustes(con), "| overrides aplicados:", aplicar_overrides(con))
    tot = con.execute("SELECT COUNT(*) c, SUM(estado='pendiente') p FROM movimientos").fetchone()
    print(f"total en base: {tot['c']} movimientos, {tot['p']} pendientes")


if __name__ == "__main__":
    main(sys.argv[1:])
