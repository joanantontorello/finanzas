"""Acceso a la base de datos SQLite de finanzas personales."""
import sqlite3
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "finanzas.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS movimientos (
  id            INTEGER PRIMARY KEY,
  hash          TEXT UNIQUE NOT NULL,
  cuenta        TEXT NOT NULL,
  fecha         TEXT NOT NULL,           -- YYYY-MM-DD
  mes           TEXT NOT NULL,           -- YYYY-MM
  concepto      TEXT NOT NULL,           -- texto original del banco
  importe       REAL NOT NULL,           -- con signo, tal y como afecta a la cuenta
  saldo         REAL,                    -- saldo de la cuenta tras el movimiento (si el banco lo da)
  tipo          TEXT,                    -- Ingreso | Gasto | Inversion | Traspaso | Reembolso
  categoria     TEXT,
  ambito        TEXT DEFAULT 'Personal', -- Personal | Trabajo
  participacion REAL DEFAULT 1.0,        -- 0.5 en la cuenta conjunta
  estado        TEXT DEFAULT 'pendiente',-- auto | revisado | pendiente
  regla         TEXT,                    -- id de la regla que lo clasificó
  nota          TEXT,
  fuente        TEXT,                    -- fichero de origen
  created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mov_mes ON movimientos(mes);
CREATE INDEX IF NOT EXISTS idx_mov_cuenta ON movimientos(cuenta);
CREATE INDEX IF NOT EXISTS idx_mov_estado ON movimientos(estado);

CREATE TABLE IF NOT EXISTS importaciones (
  id          INTEGER PRIMARY KEY,
  fichero     TEXT,
  cuenta      TEXT,
  filas       INTEGER,
  nuevas      INTEGER,
  fecha_min   TEXT,
  fecha_max   TEXT,
  saldo_final REAL,
  created_at  TEXT DEFAULT (datetime('now'))
);
"""


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(movimientos)")}
    if "compensar" not in cols:  # 1 = pagado a medias con Kate desde una cuenta propia: su mitad pasa a AJUSTES PAREJA
        con.execute("ALTER TABLE movimientos ADD COLUMN compensar INTEGER DEFAULT 0")
        con.commit()
    return con
