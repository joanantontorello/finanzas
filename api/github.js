// Guarda y lee las correcciones (data/overrides.json) y el presupuesto (data/presupuesto.json) en GitHub
// con un token que vive en el servidor (variable de entorno GITHUB_TOKEN de Vercel), para que ningún
// dispositivo tenga que configurar su propio token. El acceso lo protege Vercel Authentication, igual que el dashboard.
const REPO = 'joanantontorello/finanzas';
const PERMITIDOS = ['data/overrides.json', 'data/presupuesto.json'];

module.exports = async (req, res) => {
  const path = req.query.path;
  res.setHeader('x-finanzas-api', '1');
  res.setHeader('Cache-Control', 'no-store');
  if (!PERMITIDOS.includes(path)) return res.status(400).json({ error: 'ruta no permitida' });
  const token = process.env.GITHUB_TOKEN;
  if (!token) { res.setHeader('x-finanzas-api', 'sin-token'); return res.status(503).json({ error: 'falta GITHUB_TOKEN en Vercel' }); }
  const url = `https://api.github.com/repos/${REPO}/contents/${path}`;
  const h = { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json', 'User-Agent': 'finanzas-dashboard' };
  try {
    let r;
    if (req.method === 'GET') r = await fetch(url, { headers: h });
    else if (req.method === 'PUT') {
      const b = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
      r = await fetch(url, { method: 'PUT', headers: { ...h, 'Content-Type': 'application/json' }, body: JSON.stringify({ message: b.message, content: b.content, sha: b.sha }) });
    } else return res.status(405).json({ error: 'método no permitido' });
    res.status(r.status).setHeader('Content-Type', 'application/json').send(await r.text());
  } catch (e) {
    res.status(502).json({ error: String(e) });
  }
};
