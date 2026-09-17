# Finanzas personales

Sistema de seguimiento de finanzas personales de Joan Anton. Todo local y gratis: SQLite + Python + dashboard estático en Vercel.

- `inbox/` → deja aquí los extractos (revolut_*.csv, conjunta_*.csv, traderepublic_*.pdf, bbva_*.xlsx). No se versiona.
- `src/ingest.py` → importa y clasifica (reglas en `config/reglas.yaml`).
- `src/report.py` → genera `dashboard/data.json` y `data/movimientos.csv`.
- `dashboard/` → página estática (index.html + data.json).
- `docs/CHECKLIST.md` → el cierre mensual paso a paso.
- Skill de Claude Code: `~/.claude/skills/cierre-finanzas` ("cierra el mes").

```bash
python3 src/ingest.py            # importa inbox/
python3 src/ingest.py --pendientes
python3 src/ingest.py set 123 Gasto OCIO
python3 src/report.py
```
