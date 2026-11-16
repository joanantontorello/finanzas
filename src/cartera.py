"""Valor de mercado de las inversiones, día a día.

Las posiciones salen de las operaciones de Trade Republic (cada compra, venta o plan de ahorro trae ISIN y cantidad).
Los precios diarios se bajan de Yahoo Finance (solo se envían los tickers) y se guardan en data/precios.json, así que
sin conexión se usa lo último descargado. Lo que no tiene cantidad (la hoja antigua de 2025, eToro…) o no tiene ticker
cuenta a precio de compra.

El ticker de cada ISIN vive en config/activos.yaml. Si aparece un ISIN nuevo se busca solo en Yahoo y se añade allí.
"""
import datetime as dt
import json
import re
import urllib.parse
import urllib.request
from bisect import bisect_right
from collections import defaultdict

import yaml

PATRON = re.compile(r"(Buy trade|Savings plan execution|Sell trade) ([A-Z]{2}[A-Z0-9]{9}\d) .*?quantity: ([\d.]+)")
UA = {"User-Agent": "Mozilla/5.0 (finanzas personales)"}


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _buscar_ticker(isin):
    try:
        q = _get("https://query1.finance.yahoo.com/v1/finance/search?q=" + urllib.parse.quote(isin) + "&quotesCount=5&newsCount=0")
        quotes = [x for x in q.get("quotes") or [] if x.get("symbol")]
        return quotes[0]["symbol"] if quotes else None
    except Exception:
        return None


def _historico(ticker, desde):
    """[(fecha 'AAAA-MM-DD', cierre)] y la divisa del ticker."""
    p1 = int(dt.datetime.combine(desde, dt.time()).timestamp())
    p2 = int(dt.datetime.now().timestamp()) + 86400
    j = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?period1={p1}&period2={p2}&interval=1d")
    r = j["chart"]["result"][0]
    cierres = r["indicators"]["quote"][0].get("close") or []
    serie = [(dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"), c) for t, c in zip(r.get("timestamp") or [], cierres) if c]
    return serie, r["meta"].get("currency")


def _interp(puntos, f):
    """Factor en la fecha f a partir de [(fecha, factor)] ordenados: lineal entre puntos, constante fuera."""
    if f <= puntos[0][0]:
        return puntos[0][1]
    if f >= puntos[-1][0]:
        return puntos[-1][1]
    for (f0, a), (f1, b) in zip(puntos, puntos[1:]):
        if f0 <= f <= f1:
            d0, d1, d = (dt.date.fromisoformat(x) for x in (f0, f1, f))
            return a + (b - a) * (d - d0).days / max((d1 - d0).days, 1)
    return puntos[-1][1]


class Cartera:
    def __init__(self, rows, root, descargar=True):
        self.root = root
        self.movs = defaultdict(list)   # isin -> [(fecha, cantidad +/-)]
        self.coste_sin_cantidad = []    # [(fecha, € aportados)] de lo que no se puede valorar
        self.coste = []                 # [(fecha, € aportados netos)] de todo, para comparar
        for x in rows:
            if x["tipo"] != "Inversion":
                continue
            v = -x["mio"]
            self.coste.append((x["fecha"], v))
            m = PATRON.search(x["concepto"] or "")
            if m:
                q = float(m.group(3)) * (-1 if m.group(1) == "Sell trade" else 1)
                self.movs[m.group(2)].append((x["fecha"], q, v))
            else:
                self.coste_sin_cantidad.append((x["fecha"], v))
        self.coste.sort()
        self.coste_sin_cantidad.sort()
        for k in self.movs:
            self.movs[k].sort()
        self.tickers = self._tickers(descargar)
        for c in self.tickers.pop("_canjes", None) or []:  # fusiones: el ISIN viejo pasa al nuevo con su coste
            f, de, a = str(c["fecha"]), c["de"], c["a"]
            q = sum(x for d, x, _ in self.movs.get(de, []) if d < f)
            v = sum(x for d, _, x in self.movs.get(de, []) if d < f)
            if abs(q) > 1e-9:
                self.movs[de].append((f, -q, -v))
                self.movs[a].append((f, q * float(c["ratio"]), v))
                self.movs[de].sort()
                self.movs[a].sort()
        self.precios = self._precios(descargar)  # isin -> ([fechas], [precio €])
        self._ajustar_a_trade_republic()
        # lo que tiene cantidad pero no precio también va a coste
        for isin, ops in self.movs.items():
            if isin not in self.precios:
                self.coste_sin_cantidad += [(f, v) for f, _, v in ops]
        self.coste_sin_cantidad.sort()

    # ------------------------------------------------------------ tickers y precios
    def _tickers(self, descargar):
        path = self.root / "config" / "activos.yaml"
        t = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
        nuevos = [i for i in self.movs if i not in t and not any(c.get("de") == i for c in t.get("_canjes") or [])]
        if nuevos and descargar:
            for isin in nuevos:
                s = _buscar_ticker(isin)
                if s:
                    t[isin] = s
                    with path.open("a", encoding="utf-8") as f:
                        f.write(f"{isin}: {s}  # buscado solo en Yahoo; revísalo\n")
        return t

    def _precios(self, descargar):
        cache_p = self.root / "data" / "precios.json"
        cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
        if not self.movs:
            return {}
        desde = dt.date.fromisoformat(min(ops[0][0] for ops in self.movs.values())) - dt.timedelta(days=7)
        necesarios = {self.tickers[i] for i in self.movs if self.tickers.get(i) and any(abs(q) > 1e-9 for _, q, _ in self.movs[i])}
        if descargar:
            for tk in sorted(necesarios | {"EURUSD=X"}):
                try:
                    serie, cur = _historico(tk, desde)
                    if serie:
                        cache[tk] = {"divisa": cur, "serie": serie}
                except Exception as e:  # sin conexión o ticker roto: se queda lo de la última vez
                    print(f"  precios: no se pudo bajar {tk} ({e}); uso la copia guardada" if tk in cache else f"  precios: sin datos de {tk} ({e})")
            cache_p.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
        fx = dict(cache.get("EURUSD=X", {}).get("serie") or [])  # dólares por euro
        fx_f = sorted(fx)
        out = {}
        for isin in self.movs:
            tk = self.tickers.get(isin)
            if not tk or tk not in cache:
                continue
            cur = cache[tk]["divisa"]
            fechas, precios = [], []
            for f, p in cache[tk]["serie"]:
                if cur == "USD":
                    i = bisect_right(fx_f, f)
                    if not i:
                        continue
                    p = p / fx[fx_f[i - 1]]
                elif cur == "GBp":
                    continue
                elif cur != "EUR":
                    continue
                fechas.append(f)
                precios.append(p)
            if fechas:
                out[isin] = (fechas, precios)
        return out

    def _ajustar_a_trade_republic(self):
        """Trade Republic manda: en cada «Extracto del patrimonio neto» (data/cartera_tr.json) se compara su precio con el
        de Yahoo ese día y la serie de Yahoo se corrige por ese factor (interpolado entre fotos, fijo antes de la primera y
        después de la última). Así el valor coincide con el de Trade Republic en las fechas de las fotos."""
        path = self.root / "data" / "cartera_tr.json"
        self.fotos = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.descuadres = []
        ajustes = defaultdict(list)
        for fecha, snap in sorted(self.fotos.items()):
            for x in snap["posiciones"]:
                isin = x["isin"]
                if isin in self.precios:
                    fechas, precios = self.precios[isin]
                    i = bisect_right(fechas, x["fecha_precio"])
                    if i:
                        ajustes[isin].append((x["fecha_precio"], x["precio"] / precios[i - 1]))
                q = sum(c for f, c, _ in self.movs.get(isin, []) if f <= fecha)
                if abs(q - x["unidades"]) > 1e-6:
                    self.descuadres.append({"fecha": fecha, "isin": isin, "nombre": x["nombre"], "tr": x["unidades"], "extractos": round(q, 6)})
        self.ajustes = dict(ajustes)
        for isin, aj in ajustes.items():
            fechas, precios = self.precios[isin]
            self.precios[isin] = (fechas, [p * _interp(aj, f) for f, p in zip(fechas, precios)])

    # ------------------------------------------------------------ valoración
    def valor(self, fecha):
        """(valor de mercado €, aportado neto €) a cierre del día 'AAAA-MM-DD'."""
        total = sum(v for f, v in self.coste_sin_cantidad if f <= fecha)
        for isin, (fechas, precios) in self.precios.items():
            q = sum(c for f, c, _ in self.movs[isin] if f <= fecha)
            if abs(q) < 1e-9:
                continue
            i = bisect_right(fechas, fecha)
            if i:
                total += q * precios[i - 1]
            else:  # aún sin cotización ese día: a coste
                total += sum(v for f, _, v in self.movs[isin] if f <= fecha)
        aportado = sum(v for f, v in self.coste if f <= fecha)
        return total, aportado

    def posiciones(self, fecha):
        """Detalle por activo a una fecha: [{isin, ticker, cantidad, precio, valor, aportado}]."""
        res = []
        for isin, ops in self.movs.items():
            q = sum(c for f, c, _ in ops if f <= fecha)
            ap = sum(v for f, _, v in ops if f <= fecha)
            if abs(q) < 1e-9:
                continue
            p = None
            if isin in self.precios:
                fechas, precios = self.precios[isin]
                i = bisect_right(fechas, fecha)
                p = precios[i - 1] if i else None
            res.append({"isin": isin, "ticker": self.tickers.get(isin), "cantidad": round(q, 6), "precio": None if p is None else round(p, 4),
                        "valor": round(q * p, 2) if p is not None else round(ap, 2), "aportado": round(ap, 2)})
        return sorted(res, key=lambda r: -r["valor"])
