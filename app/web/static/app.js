function downloadPng(canvas) {
  const link = document.createElement("a");
  link.download = "newstrader-screenshot.png";
  link.href = canvas.toDataURL("image/png");
  link.click();
}

function captureForeignObject() {
  const node = document.body;
  const width = node.scrollWidth;
  const height = node.scrollHeight;
  const html = new XMLSerializer().serializeToString(node);
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="' + width + '" height="' + height +
    '"><foreignObject width="100%" height="100%">' + html + "</foreignObject></svg>";
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = function () {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#0f1420";
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(img, 0, 0);
    URL.revokeObjectURL(url);
    downloadPng(canvas);
  };
  img.onerror = function () {
    alert("Не удалось сделать скриншот");
  };
  img.src = url;
}

async function capturePage() {
  if (window.html2canvas) {
    try {
      const canvas = await html2canvas(document.body, {
        backgroundColor: "#0f1420",
        scale: 2,
        useCORS: true,
      });
      return downloadPng(canvas);
    } catch (e) {
      captureForeignObject();
    }
  } else {
    captureForeignObject();
  }
}

// Реальное время (docs/24): странично-широкий live-обновление котировок по SSE.
// Поддерживает: карточку бумаги (#rt-quotes → цена/макс-мин/объём) и общие
// ячейки/строки на других страницах:
//   - ячейка цены:   [data-rt-price="TICKER"]
//   - строка с P&L:  tr[data-rt-recalc="TICKER"][data-rq][data-cost],
//                    дочерние ячейки [data-rt-field="cost|pnl|pnlpct"]
//   - итоги:         [data-rt-total="value|pnl"]
function initRealtimeQuotes() {
  const tickers = new Set();

  // Карточка бумаги
  const cardWrap = document.getElementById("rt-quotes");
  const cardTicker = cardWrap ? cardWrap.getAttribute("data-ticker") : null;
  if (cardTicker) tickers.add(cardTicker);

  // Общие ячейки цены
  const priceCells = document.querySelectorAll("[data-rt-price]");
  const priceByTicker = {};
  priceCells.forEach(function (el) {
    const t = el.getAttribute("data-rt-price");
    if (!t) return;
    tickers.add(t);
    (priceByTicker[t] = priceByTicker[t] || []).push(el);
  });

  // Строки с P&L (портфель/paper)
  const recalcs = [];
  document.querySelectorAll("tr[data-rt-recalc]").forEach(function (row) {
    const t = row.getAttribute("data-rt-recalc");
    const qty = parseFloat(row.getAttribute("data-rq"));
    const cost = parseFloat(row.getAttribute("data-cost"));
    if (!t || isNaN(qty) || isNaN(cost)) return;
    tickers.add(t);
    recalcs.push({ ticker: t, qty: qty, cost: cost, row: row });
  });

  // OI ячейки (фьючерсы): [data-rt-oi-value|change|groups][data-rt-oi="TICKER"]
  const oiValueByTicker = {};
  const oiChangeByTicker = {};
  const oiGroupsByTicker = {};
  document.querySelectorAll("[data-rt-oi]").forEach(function (el) {
    const t = el.getAttribute("data-rt-oi");
    if (!t) return;
    tickers.add(t);
    if (el.hasAttribute("data-rt-oi-value")) (oiValueByTicker[t] = oiValueByTicker[t] || []).push(el);
    if (el.hasAttribute("data-rt-oi-change")) (oiChangeByTicker[t] = oiChangeByTicker[t] || []).push(el);
    if (el.hasAttribute("data-rt-oi-groups")) (oiGroupsByTicker[t] = oiGroupsByTicker[t] || []).push(el);
  });

  if (tickers.size === 0) return;

  const costText = function (v) {
    if (v == null || isNaN(v)) return "—";
    return v.toFixed(2);
  };

  function fmtNumber(v) {
    if (v == null || isNaN(v)) return "—";
    return v.toFixed(2);
  }

  function fmtPercent(v) {
    if (v == null || isNaN(v)) return "—";
    return v.toFixed(2) + "%";
  }

  function recompute(recalc, price) {
    const marketValue = recalc.qty * price;
    const cost = recalc.qty * recalc.cost;
    const pnl = marketValue - cost;
    const pct = cost !== 0 ? ((pnl / cost) * 100) : 0;
    recalc.row.querySelectorAll("[data-rt-field]").forEach(function (cell) {
      const f = cell.getAttribute("data-rt-field");
      if (f === "cost") cell.textContent = fmtNumber(marketValue);
      else if (f === "pnl") {
        cell.textContent = fmtNumber(pnl);
        cell.className = (pnl >= 0 ? "pos" : "neg");
      } else if (f === "pnlpct") {
        cell.textContent = fmtPercent(pct);
      }
    });
  }

  function updateTotals() {
    let v = 0, pnl = 0;
    recalcs.forEach(function (r) {
      const priceEl = (priceByTicker[r.ticker] || [])[0];
      // цена может обновляться асинхронно; пересчитываем по последней известной
      const last = priceEl ? parseFloat(priceEl.getAttribute("data-last")) : NaN;
      if (!isNaN(last)) {
        v += r.qty * last;
        pnl += (r.qty * last) - (r.qty * r.cost);
      }
    });
    const vEl = document.querySelector("[data-rt-total='value']");
    const pEl = document.querySelector("[data-rt-total='pnl']");
    if (vEl) vEl.textContent = fmtNumber(v);
    if (pEl) {
      pEl.textContent = fmtNumber(pnl);
      pEl.className = (pnl >= 0 ? "pos" : "neg");
    }
  }

  const list = Array.from(tickers);
  const es = new EventSource("/v1/realtime/stream?tickers=" + encodeURIComponent(list.join(",")));
  es.addEventListener("quote", function (e) {
    let data;
    try {
      data = JSON.parse(e.data);
    } catch (err) {
      return;
    }
    const range = data.last != null ? String(data.last) : "";
    // Карточка
    if (cardWrap && data.ticker === cardTicker) {
      const priceEl = document.getElementById("rt-price");
      const hlEl = document.getElementById("rt-highlow");
      const volEl = document.getElementById("rt-volume");
      if (data.last != null && priceEl) priceEl.textContent = data.last;
      if (hlEl && (data.high != null || data.low != null)) {
        const h = data.high != null ? data.high : "";
        const l = data.low != null ? data.low : "";
        hlEl.textContent = (h + " / " + l).trim();
      }
      if (volEl && data.volume != null) volEl.textContent = data.volume;
    }
    // Общие ячейки цены
    const els = priceByTicker[data.ticker];
    if (els && data.last != null) {
      els.forEach(function (el) {
        el.textContent = range;
        el.setAttribute("data-last", String(data.last));
      });
    }
    // Строки P&L
    const isDirty = els && data.last != null;
    if (isDirty) {
      recalcs.forEach(function (r) {
        if (r.ticker === data.ticker) recompute(r, data.last);
      });
      updateTotals();
    }
  });
  // OI (фьючерсы): обновляем последнее значение/изменение/группы клиентов
  es.addEventListener("oi", function (e) {
    let data;
    try {
      data = JSON.parse(e.data);
    } catch (err) {
      return;
    }
    const t = data.ticker;
    const fmt = function (n) {
      if (n == null || isNaN(n)) return "—";
      return Number(n).toLocaleString("ru-RU");
    };
    const fmtPct = function (n) {
      if (n == null || isNaN(n)) return "—";
      return (n > 0 ? "+" : "") + n.toFixed(2) + "%";
    };
    if (oiValueByTicker[t]) oiValueByTicker[t].forEach(function (el) { el.textContent = fmt(data.open_position); });
    if (oiChangeByTicker[t]) oiChangeByTicker[t].forEach(function (el) { el.textContent = fmtPct(data.change_pct); });
    if (oiGroupsByTicker[t] && data.groups) {
      oiGroupsByTicker[t].forEach(function (el) {
        const phys = data.groups.physical ? data.groups.physical.net : null;
        const jur = data.groups.juridical ? data.groups.juridical.net : null;
        el.textContent = (phys != null ? "физ " + fmt(phys) : "") + (jur != null ? " · юр " + fmt(jur) : "");
      });
    }
  });
  es.onerror = function () {
    // Неавторизован/демон выключен — закрываем, не падаем.
    es.close();
  };
  window.addEventListener("pagehide", function () {
    es.close();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initRealtimeQuotes);
} else {
  initRealtimeQuotes();
}
