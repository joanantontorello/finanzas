"""Categorías (grupo y subcategorías) compartidas por ingest.py y report.py.

Fuente: data/categorias.json, que se edita desde el dashboard (apartado Categorías) y se guarda en GitHub.
Si no existe, se usa la versión antigua config/categorias.yaml.
"""
import json
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _datos(root=ROOT):
    root = pathlib.Path(root)
    j = root / "data" / "categorias.json"
    if j.exists():
        return json.loads(j.read_text(encoding="utf-8"))
    return yaml.safe_load((root / "config" / "categorias.yaml").read_text(encoding="utf-8"))


def mover(root=ROOT):
    """{subcategoría quitada o renombrada: subcategoría actual}, resolviendo cadenas (A→B, B→C ⇒ A→C)."""
    m = _datos(root).get("_mover") or {}
    res = {}
    for k in m:
        v, vistos = m[k], {k}
        while v in m and v not in vistos:
            vistos.add(v)
            v = m[v]
        res[k] = v
    return res


def cargar(root=ROOT):
    """Devuelve (flat, grupos, roles).
    flat:   {subcategoría: {tipo, grupo, esencial, ambito, rol}}
    grupos: lista ordenada de {nombre, tipo, esencial, color, sub: [...]}
    roles:  {rol: subcategoría} (traspaso, aportacion_pareja, ajustes_pareja, reembolsos, imprevistos)
    """
    data = _datos(root)
    flat, grupos, roles = {}, [], {}
    for tipo, gs in data.items():
        if tipo.startswith("_"):
            continue
        for gname, props in (gs or {}).items():
            props = props or {}
            subs = props.get("sub") or []
            grupos.append({"nombre": gname, "tipo": tipo, "esencial": props.get("esencial"), "color": props.get("color"), "sub": subs})
            for s in subs:
                rol = (props.get("roles") or {}).get(s)
                flat[s] = {
                    "tipo": tipo,
                    "grupo": gname,
                    "esencial": props.get("esencial"),
                    "ambito": (props.get("ambito_sub") or {}).get(s) or props.get("ambito"),
                    "rol": rol,
                }
                if rol:
                    roles[rol] = s
    return flat, grupos, roles
