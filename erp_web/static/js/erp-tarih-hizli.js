/**
 * Hızlı tarih: 01012026 (ggmmaaaa) veya 010125 (ggmmaa, yıl 2000+aa) → gg.aa.yyyy / ISO
 * Flatpickr (allowInput) ve input[type=date] ile kullanılır.
 */
(function (w) {
  'use strict';

  function pad2(n) {
    n = parseInt(n, 10);
    if (isNaN(n)) return '00';
    return n < 10 ? '0' + n : String(n);
  }

  function erpParseCompactDate(raw) {
    var d = String(raw || '').replace(/\D/g, '');
    if (d.length !== 6 && d.length !== 8) return null;
    var dd = d.slice(0, 2);
    var mm = d.slice(2, 4);
    var day = parseInt(dd, 10);
    var month = parseInt(mm, 10);
    if (month < 1 || month > 12 || day < 1 || day > 31) return null;
    var year;
    if (d.length === 8) {
      year = parseInt(d.slice(4, 8), 10);
    } else {
      year = 2000 + parseInt(d.slice(4, 6), 10);
    }
    if (year < 1900 || year > 2100) return null;
    var dt = new Date(year, month - 1, day);
    if (dt.getFullYear() !== year || dt.getMonth() !== month - 1 || dt.getDate() !== day) return null;
    return {
      iso: year + '-' + pad2(month) + '-' + pad2(day),
      display: pad2(day) + '.' + pad2(month) + '.' + year
    };
  }

  function tryCompactFromString(str) {
    var only = String(str || '').replace(/\D/g, '');
    if (only.length !== 6 && only.length !== 8) return null;
    return erpParseCompactDate(only);
  }

  function applyCompactToFlatpickr(fp) {
    if (!fp) return false;
    var target = fp.altInput || fp.input;
    if (!target) return false;
    var r = tryCompactFromString(target.value);
    if (!r) return false;
    try {
      fp.setDate(r.iso, false, 'Y-m-d');
      return true;
    } catch (e) {
      return false;
    }
  }

  function bindFlatpickrInstance(fp) {
    if (!fp || fp._erpHizliBound) return;
    fp._erpHizliBound = true;
    var target = fp.altInput || fp.input;
    if (!target) return;

    function onCompactOrBlur() {
      if (applyCompactToFlatpickr(fp)) return;
      if (fp.altInput) {
        try {
          fp.setDate(fp.altInput.value, false, fp.config.altFormat);
        } catch (e) {}
      }
    }

    target.addEventListener('input', function () {
      var only = target.value.replace(/\D/g, '');
      if (only.length === 6 || only.length === 8) applyCompactToFlatpickr(fp);
    });
    target.addEventListener('blur', onCompactOrBlur);
    target.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') onCompactOrBlur();
    });
  }

  function patchFlatpickr() {
    if (!w.flatpickr || w.flatpickr.__erpHizliPatched) return;
    var orig = w.flatpickr;
    var wrapped = function (selector, config) {
      config = config || {};
      var prev = config.onReady;
      config.onReady = function (selectedDates, dateStr, instance) {
        bindFlatpickrInstance(instance);
        if (typeof prev === 'function') prev(selectedDates, dateStr, instance);
      };
      var inst = orig(selector, config);
      return inst;
    };
    for (var k in orig) {
      if (Object.prototype.hasOwnProperty.call(orig, k)) wrapped[k] = orig[k];
    }
    wrapped.__erpHizliPatched = true;
    w.flatpickr = wrapped;
  }

  function scanFlatpickrInputs() {
    document.querySelectorAll('.flatpickr-input').forEach(function (inp) {
      if (inp._flatpickr) bindFlatpickrInstance(inp._flatpickr);
    });
  }

  function bindNativeDateInput(el) {
    if (!el || el.tagName !== 'INPUT' || el.type !== 'date' || el.dataset.erpHizliNative === '1') return;
    el.dataset.erpHizliNative = '1';
    var digits = '';

    el.addEventListener('focus', function () {
      digits = '';
    });
    el.addEventListener('blur', function () {
      digits = '';
    });

    el.addEventListener('paste', function (e) {
      var text = ((e.clipboardData || w.clipboardData || {}).getData('text') || '').trim();
      var iso = text.match(/^(\d{4}-\d{2}-\d{2})/);
      if (iso) {
        e.preventDefault();
        el.value = iso[1];
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return;
      }
      var r = erpParseCompactDate(text);
      if (r) {
        e.preventDefault();
        el.value = r.iso;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });

    el.addEventListener('keydown', function (ev) {
      if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
      var k = ev.key;
      if (k >= '0' && k <= '9') {
        ev.preventDefault();
        digits += k;
        if (digits.length > 8) digits = digits.slice(-8);
        if (digits.length === 6 || digits.length === 8) {
          var r = erpParseCompactDate(digits);
          if (r) {
            el.value = r.iso;
            digits = '';
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
          }
        }
      } else if (k === 'Backspace') {
        if (digits.length) {
          ev.preventDefault();
          digits = digits.slice(0, -1);
        }
      } else if (k === 'Escape') {
        digits = '';
      }
    }, true);
  }

  function scanNativeDateInputs(root) {
    (root || document).querySelectorAll('input[type="date"]').forEach(bindNativeDateInput);
  }

  function init() {
    patchFlatpickr();
    scanFlatpickrInputs();
    scanNativeDateInputs();
  }

  document.addEventListener('focusin', function (e) {
    var el = e.target;
    if (el && el.tagName === 'INPUT' && el.type === 'date') bindNativeDateInput(el);
  });

  w.erpParseCompactDate = erpParseCompactDate;
  w.erpSyncFlatpickrFromVisible = function (inputEl) {
    var fp = inputEl && inputEl._flatpickr;
    if (!fp) return;
    applyCompactToFlatpickr(fp);
    if (fp.altInput) {
      try {
        fp.setDate(fp.altInput.value, false, fp.config.altFormat);
      } catch (e) {}
    }
  };

  function rescanAll() {
    patchFlatpickr();
    scanFlatpickrInputs();
    scanNativeDateInputs(document);
  }

  w.erpBindFlatpickrInstance = bindFlatpickrInstance;
  w.erpRescanTarihHizli = rescanAll;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  setTimeout(init, 0);
  setTimeout(rescanAll, 400);
  window.addEventListener('load', function () {
    rescanAll();
  });
})(window);
