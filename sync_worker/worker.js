// Синхронізація історії переглядів і обраного UAFix Player між пристроями.
//
// Кожен користувач має свій код (його генерує застосунок, 12 знаків — ~60 біт випадковості,
// вгадати чужий перебором нереально); код і є ключем до даних. Сервер зберігає один
// документ на код і зливає його з тим, що надіслав пристрій: для кожного фільму перемагає
// новіший запис (ts), очищення історії — позначка history_cleared.
//
// API:  GET  /v1/sync/<код>   → документ
//       POST /v1/sync/<код>   (документ пристрою) → злитий документ
//
// Документ: {history: [...], favorites: [...], history_cleared: ts}
//   history:   {url, title, orig, poster, age, season, label, si, ei, voice, pos, dur, ts,
//               seen?: {"сезон:серія": частка переглянутого}}
//   favorites: {url, title, orig, poster, age, ts, removed?}

const CODE_RE = /^[A-HJ-NP-Z2-9]{12}$/;   // без схожих I/O/0/1
const MAX_BODY = 256 * 1024;
const MAX_HISTORY = 200;
const MAX_FAVORITES = 500;
const RATE_LIMIT = 60;                     // запитів на хвилину з однієї адреси

const hits = new Map();                    // проста межа запитів (у межах одного ізолята)

function limited(ip) {
  const minute = Math.floor(Date.now() / 60000);
  const key = `${ip}:${minute}`;
  const n = (hits.get(key) || 0) + 1;
  hits.set(key, n);
  if (hits.size > 5000) hits.clear();
  return n > RATE_LIMIT;
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

const str = (v, max) => (typeof v === "string" ? v.slice(0, max) : "");
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : 0);

function cleanHistory(h) {
  if (!h || typeof h !== "object" || typeof h.url !== "string" || !h.url.startsWith("https://"))
    return null;
  const out = {
    url: str(h.url, 300), title: str(h.title, 200), orig: str(h.orig, 200),
    poster: str(h.poster, 400), age: str(h.age, 10), season: str(h.season, 80),
    label: str(h.label, 120), si: num(h.si), ei: num(h.ei), voice: str(h.voice, 80),
    pos: num(h.pos), dur: num(h.dur), ts: num(h.ts),
  };
  if (h.removed) out.removed = true;  // прибрано з «Продовжити перегляд» на одному з пристроїв
  const seen = cleanSeen(h.seen);
  if (seen) out.seen = seen;
  return out;
}

// скільки переглянуто кожної серії: {"сезон:серія": частка 0..1}, не більше 150 записів
function cleanSeen(s) {
  if (!s || typeof s !== "object" || Array.isArray(s)) return null;
  const out = {};
  let n = 0;
  for (const [k, v] of Object.entries(s)) {
    if (!/^\d{1,3}:\d{1,4}$/.test(k) || typeof v !== "number" || !Number.isFinite(v)) continue;
    out[k] = Math.max(0, Math.min(1, Math.round(v * 1000) / 1000));
    if (++n >= 150) break;
  }
  return n ? out : null;
}

function cleanFavorite(f) {
  if (!f || typeof f !== "object" || typeof f.url !== "string" || !f.url.startsWith("https://"))
    return null;
  const out = {
    url: str(f.url, 300), title: str(f.title, 200), orig: str(f.orig, 200),
    poster: str(f.poster, 400), age: str(f.age, 10), ts: num(f.ts),
  };
  if (f.removed) out.removed = true;
  return out;
}

function mergeByUrl(a, b, clean, max) {
  const byUrl = new Map();
  for (const raw of [...(a || []), ...(b || [])]) {
    const item = clean(raw);
    if (!item) continue;
    const prev = byUrl.get(item.url);
    if (!prev || item.ts >= prev.ts) byUrl.set(item.url, item);
  }
  return [...byUrl.values()].sort((x, y) => y.ts - x.ts).slice(0, max);
}

function merge(server, client) {
  const cleared = Math.max(num(server.history_cleared), num(client.history_cleared));
  const history = mergeByUrl(server.history, client.history, cleanHistory, MAX_HISTORY)
    .filter((h) => !cleared || h.ts > cleared);
  const favorites = mergeByUrl(server.favorites, client.favorites, cleanFavorite, MAX_FAVORITES);
  return { history, favorites, history_cleared: cleared };
}

// D1 інколи відповідає тимчасовою помилкою («Network connection lost», «overloaded») —
// одна повторна спроба з паузою
async function retry(fn) {
  try {
    return await fn();
  } catch (e) {
    await new Promise((r) => setTimeout(r, 250));
    return await fn();
  }
}

async function load(env, code) {
  const row = await retry(() =>
    env.DB.prepare("SELECT data FROM sync WHERE code = ?").bind(code).first());
  return row ? JSON.parse(row.data) : { history: [], favorites: [], history_cleared: 0 };
}

// останні помилки — для діагностики (без кодів і даних користувачів)
async function logError(env, request, e) {
  try {
    await env.DB.prepare("CREATE TABLE IF NOT EXISTS errors (at INTEGER, method TEXT, msg TEXT)").run();
    await env.DB.prepare("INSERT INTO errors (at, method, msg) VALUES (?1, ?2, ?3)")
      .bind(Date.now(), request.method, String((e && e.stack) || e).slice(0, 800)).run();
    await env.DB.prepare("DELETE FROM errors WHERE at < ?1").bind(Date.now() - 7 * 86400000).run();
  } catch {}
}

export default {
  async fetch(request, env) {
    try {
      return await handle(request, env);
    } catch (e) {
      await logError(env, request, e);
      return json({ error: "server error" }, 503);
    }
  },
};

async function handle(request, env) {
  {
    const url = new URL(request.url);
    const m = url.pathname.match(/^\/v1\/sync\/([^/]+)$/);
    if (!m) return json({ error: "not found" }, 404);
    const code = m[1].toUpperCase().replace(/-/g, "");
    if (!CODE_RE.test(code)) return json({ error: "bad code" }, 400);
    if (limited(request.headers.get("cf-connecting-ip") || "?"))
      return json({ error: "too many requests" }, 429);

    if (request.method === "GET") return json(await load(env, code));

    if (request.method === "POST") {
      const body = await request.text();
      if (body.length > MAX_BODY) return json({ error: "too large" }, 413);
      let client;
      try {
        client = JSON.parse(body);
      } catch {
        return json({ error: "bad json" }, 400);
      }
      const merged = merge(await load(env, code), client || {});
      const data = JSON.stringify(merged);
      if (data.length > MAX_BODY) return json({ error: "too large" }, 413);
      await retry(() => env.DB.prepare(
        "INSERT INTO sync (code, data, updated) VALUES (?1, ?2, ?3) " +
        "ON CONFLICT(code) DO UPDATE SET data = ?2, updated = ?3",
      ).bind(code, data, Date.now()).run());
      return json(merged);
    }
    return json({ error: "method not allowed" }, 405);
  }
}
