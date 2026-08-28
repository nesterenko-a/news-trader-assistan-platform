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

// Реальное время (docs/24): JS подписка на SSE live-котировок на карточке бумаги.
function initRealtimeQuotes() {
  const wrap = document.getElementById("rt-quotes");
  if (!wrap) return;
  const ticker = wrap.getAttribute("data-ticker");
  if (!ticker) return;

  const priceEl = document.getElementById("rt-price");
  const hlEl = document.getElementById("rt-highlow");
  const volEl = document.getElementById("rt-volume");

  let es = null;
  function open() {
    if (es) es.close();
    es = new EventSource("/v1/realtime/stream?tickers=" + encodeURIComponent(ticker));
    es.addEventListener("quote", function (e) {
      let data;
      try {
        data = JSON.parse(e.data);
      } catch (err) {
        return;
      }
      if (data.ticker !== ticker) return;
      if (data.last != null && priceEl) priceEl.textContent = data.last;
      if (hlEl && (data.high != null || data.low != null)) {
        const h = data.high != null ? data.high : "";
        const l = data.low != null ? data.low : "";
        hlEl.textContent = (h + " / " + l).trim();
      }
      if (volEl && data.volume != null) volEl.textContent = data.volume;
    });
    es.onerror = function () {
      // Страница может быть неавторизована/демон выключен — закрываем, не падаем.
      if (es) es.close();
      es = null;
    };
  }
  open();
  // Навигация SPA не используется; при уходе со страницы EventSource закроется сам.
  window.addEventListener("pagehide", function () {
    if (es) es.close();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initRealtimeQuotes);
} else {
  initRealtimeQuotes();
}
