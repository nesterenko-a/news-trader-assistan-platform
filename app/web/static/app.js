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
