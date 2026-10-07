/** Kuyruk ucu 4 MB, diger uclar 100 KB. Token govdeden once. Ag ve Chrome yok. */
const assert = require("assert");
const http = require("http");
const fs = require("fs");
const path = require("path");
const express = require("express");
const { govdeAyir, jsonSinirHatasi, JSON_KUCUK, JSON_KUYRUK } = require("./govde-kural");

const TOKEN = "t".repeat(32);
const indexSrc = fs.readFileSync(path.join(__dirname, "index.js"), "utf8");
const tokenAt = indexSrc.indexOf("app.use(requireInternalToken)");
const govdeAt = indexSrc.indexOf("app.use(govdeAyir)");
assert.ok(tokenAt > 0 && govdeAt > tokenAt);
assert.strictEqual(indexSrc.includes("app.use(express.json("), false);
assert.ok(indexSrc.includes("jsonSinirHatasi"));
assert.ok(indexSrc.indexOf("app.use(jsonSinirHatasi") > govdeAt);

function uygulama(loglar) {
  const app = express();
  let yol = 0;
  app.use((req, res, next) => {
    const got = String(req.headers["x-wa-internal-token"] || "");
    if (got !== TOKEN) {
      res.status(401).json({ ok: false, error: "Yetkisiz" });
      req.resume();
      return;
    }
    return next();
  });
  app.use(govdeAyir);
  app.post("/t/:tenantId/kuyruk-toplu-ekle", (req, res) => {
    yol += 1;
    res.json({ ok: true, yol });
  });
  app.post("/t/:tenantId/send", (req, res) => {
    yol += 1;
    res.json({ ok: true, yol });
  });
  app.use(jsonSinirHatasi((olay, detay) => loglar.push(olay + " " + detay)));
  return app;
}

function iste(app, yol, token, govde) {
  return new Promise((resolve) => {
    const server = app.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      const req = http.request(
        {
          host: "127.0.0.1",
          port,
          path: yol,
          method: "POST",
          headers: Object.assign(
            { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(govde) },
            token ? { "X-WA-Internal-Token": token } : {}
          ),
        },
        (res) => {
          const parca = [];
          res.on("data", (b) => parca.push(b));
          res.on("end", () => {
            server.close();
            let body = {};
            try {
              body = JSON.parse(Buffer.concat(parca).toString("utf8") || "{}");
            } catch (_e) {
              body = {};
            }
            resolve({ status: res.statusCode, body });
          });
        }
      );
      req.on("error", () => {
        server.close();
        resolve({ status: 0, body: {} });
      });
      req.write(govde);
      req.end();
    });
  });
}

(async () => {
  const loglar = [];
  const app = uygulama(loglar);
  const kucuk = JSON.stringify({ liste: [{ mesaj: "a" }] });
  const orta = JSON.stringify({ liste: [{ mesaj: "m", ek: "x".repeat(220 * 1024) }] });
  assert.ok(Buffer.byteLength(orta) > 200 * 1024 && Buffer.byteLength(orta) < JSON_KUYRUK);
  const sinirAlti = JSON.stringify({ liste: [{ mesaj: "y".repeat(JSON_KUYRUK - 4096) }] });
  assert.ok(Buffer.byteLength(sinirAlti) < JSON_KUYRUK && Buffer.byteLength(sinirAlti) > JSON_KUCUK);
  const ust = JSON.stringify({ liste: [{ mesaj: "z".repeat(JSON_KUYRUK + 1024) }] });
  assert.ok(Buffer.byteLength(ust) > JSON_KUYRUK);
  const sendBuyuk = JSON.stringify({ mesaj: "q".repeat(JSON_KUCUK + 2048) });
  assert.ok(Buffer.byteLength(sendBuyuk) > JSON_KUCUK && Buffer.byteLength(sendBuyuk) < JSON_KUYRUK);

  const a = await iste(app, "/t/default/kuyruk-toplu-ekle", TOKEN, orta);
  assert.strictEqual(a.status, 200);
  assert.strictEqual(a.body.ok, true);
  const b = await iste(app, "/t/default/kuyruk-toplu-ekle", TOKEN, sinirAlti);
  assert.strictEqual(b.status, 200);
  const c = await iste(app, "/t/default/kuyruk-toplu-ekle", TOKEN, ust);
  assert.strictEqual(c.status, 413);
  assert.strictEqual(c.body.code, "ek_cok_buyuk");
  assert.ok(loglar.some((s) => s.includes("kod=ek_cok_buyuk") && s.includes("http=413") && s.includes("boyut=")));
  assert.ok(loglar.every((s) => !s.includes("z".repeat(40))));
  const d = await iste(app, "/t/default/send", TOKEN, sendBuyuk);
  assert.strictEqual(d.status, 413);
  assert.strictEqual(d.body.code, "ek_cok_buyuk");
  const e = await iste(app, "/t/default/send", TOKEN, kucuk);
  assert.strictEqual(e.status, 200);
  const f = await iste(app, "/t/default/kuyruk-toplu-ekle", "", Buffer.alloc(180 * 1024, 0x7b).toString("utf8"));
  assert.strictEqual(f.status, 401);
  assert.strictEqual(f.body.ok, false);
  console.log("CASE govde kural ok");
})().catch((err) => {
  console.error("FAIL govde");
  console.error(err && err.message ? err.message : "hata");
  process.exit(1);
});
