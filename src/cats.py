"""Lectura de config/categorias.yaml (grupo y subcategorías) compartida por ingest.py y report.py."""
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def cargar(root=ROOT):
    """Devuelve (flat, grupos, roles).
    flat:   {subcategoría: {tipo, grupo, esencial, ambito, rol}}
    grupos: lista ordenada de {nombre, tipo, esencial, sub: [...]}
    roles:  {rol: subcategoría} (traspaso, aportacion_pareja, ajustes_pareja, reembolsos, imprevistos)
    """
    data = yaml.safe_load((pathlib.Path(root) / "config" / "categorias.yaml").read_text(encoding="utf-8"))
    flat, grupos, roles = {}, [], {}
    for tipo, gs in data.items():
        for gname, props in (gs or {}).items():
            props = props or {}
            subs = props.get("sub") or []
            grupos.append({"nombre": gname, "tipo": tipo, "esencial": props.get("esencial"), "sub": subs})
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
