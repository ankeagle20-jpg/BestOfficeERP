"use strict";
const m = require("./static/js/wa-toplu.js");

function esit(ad, ok) {
  if (!ok) {
    console.log("FAIL " + ad);
    process.exit(1);
  }
  console.log("ok " + ad);
}

const acik = m.wtSecimUygula([
  { gorunur: true, secilebilir: true, secili: false },
  { gorunur: false, secilebilir: true, secili: false },
  { gorunur: true, secilebilir: false, secili: false }
], true);
esit("gorunen", acik[0] === true && acik[1] === false && acik[2] === false);

const kapali = m.wtSecimUygula([
  { gorunur: true, secilebilir: true, secili: true },
  { gorunur: false, secilebilir: true, secili: true }
], false);
esit("gizli kalir", kapali[0] === false && kapali[1] === true);

const ozet = m.wtOzet(["05550001111", "905550001111", "05550002222"], 150);
esit("tekil ozet", ozet.secili === 3 && ozet.alici === 2 && ozet.sure_dk === 3 && ozet.gun === 1);
