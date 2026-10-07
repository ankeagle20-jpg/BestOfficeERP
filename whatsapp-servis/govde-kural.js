/** JSON gövde sınırı. Kuyruk ucu 4 MB, diğer uçlar 100 KB. Bayt diske yazılmaz. */

const express = require("express");

const JSON_KUCUK = 100 * 1024;
const JSON_KUYRUK = 4 * 1024 * 1024;

const jsonKucuk = express.json({ limit: JSON_KUCUK });
const jsonKuyruk = express.json({ limit: JSON_KUYRUK });

function kuyrukGovdeYolu(req) {
  return req.method === "POST" && /^\/t\/[^/]+\/kuyruk-toplu-ekle$/.test(String(req.path || ""));
}

function govdeAyir(req, res, next) {
  if (kuyrukGovdeYolu(req)) return jsonKuyruk(req, res, next);
  return jsonKucuk(req, res, next);
}

function jsonSinirHatasi(log) {
  return function (err, req, res, next) {
    const status = err && (err.status || err.statusCode);
    if (status !== 413 && !(err && err.type === "entity.too.large")) return next(err);
    const ham = (err && err.length) || (req.headers && req.headers["content-length"]) || 0;
    const boyut = parseInt(String(ham), 10) || 0;
    if (typeof log === "function") log("json_sinir", "kod=ek_cok_buyuk http=413 boyut=" + boyut);
    if (res.headersSent) return next(err);
    return res.status(413).json({ ok: false, code: "ek_cok_buyuk", error: "Govde siniri asildi" });
  };
}

module.exports = { govdeAyir, jsonSinirHatasi, kuyrukGovdeYolu, JSON_KUCUK, JSON_KUYRUK };
