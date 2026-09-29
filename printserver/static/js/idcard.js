"use strict";

(() => {
  const aspect = window.CARD.w_mm / window.CARD.h_mm;
  const editor = new PhotoEditor();
  const fileInput = document.getElementById("file-input");
  const slots = Object.fromEntries(
    [...document.querySelectorAll(".id-slot")].map((el) => [el.dataset.side, el]));
  const sides = { front: null, back: null };  // { file, state, blob, url }
  let pendingSide = null;

  Object.entries(slots).forEach(([side, el]) => {
    el.dataset.empty = el.innerHTML;
    el.addEventListener("click", () => {
      if (sides[side]) openEditor(side, sides[side].file, sides[side].state, false);
      else pickFile(side);
    });
  });

  function pickFile(side) {
    pendingSide = side;
    fileInput.value = "";
    fileInput.click();
  }

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (file && pendingSide) openEditor(pendingSide, file, null, true);
    pendingSide = null;
  });

  function openEditor(side, file, state, isNew) {
    editor.open({
      file, state, isNew, aspect,
      onDone: (res) => setSide(side, res),
      onRemove: () => setSide(side, null),
      onReplace: () => pickFile(side),
    });
  }

  function setSide(side, data) {
    if (sides[side]) URL.revokeObjectURL(sides[side].url);
    sides[side] = data ? { ...data, url: URL.createObjectURL(data.blob) } : null;
    const el = slots[side];
    el.classList.toggle("filled", !!data);
    el.setAttribute("aria-label", `${data ? "Edit" : "Add"} ${side} of card`);
    el.innerHTML = data ? `<img alt="" src="${sides[side].url}">` : el.dataset.empty;
  }

  function buildForm() {
    if (!sides.front && !sides.back) {
      toast("Tap Front or Back to add a photo of the card", "error");
      return null;
    }
    const form = new FormData();
    for (const side of ["front", "back"]) {
      if (sides[side]) form.append(side, sides[side].blob, `${side}.jpg`);
    }
    form.append("outline", radioValue("outline"));
    form.append("fit", radioValue("fit"));
    form.append("copies", document.getElementById("copies").value || "1");
    appendPrintTarget(form);
    return form;
  }

  async function send(url, button) {
    const form = buildForm();
    if (!form) return null;
    setBusy(button, true);
    try {
      const res = await fetch(url, { method: "POST", body: form });
      if (!res.ok) throw new Error(await readError(res));
      return res;
    } catch (err) {
      toast(err.message, "error");
      return null;
    } finally {
      setBusy(button, false);
    }
  }

  const printBtn = document.getElementById("print-card");
  printBtn.addEventListener("click", async () => {
    if (await send("/api/id-card/print", printBtn)) {
      toast(sentMessage(), "success");
      refreshStatus();
    }
  });

  const pdfBtn = document.getElementById("download-pdf");
  pdfBtn.addEventListener("click", async () => {
    const res = await send("/api/id-card/pdf", pdfBtn);
    if (!res) return;
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "id-card.pdf";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  });
  // Photos shared from another app: crop the front, then the back.
  takeSharedItems().then(async (items) => {
    const images = items.filter((item) => item.image).slice(0, 2);
    if (!images.length) return;
    let files;
    try {
      files = await Promise.all(images.map(sharedImageFile));
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const cropSide = (index) => {
      const side = index === 0 ? "front" : "back";
      editor.open({
        file: files[index], state: null, isNew: true, aspect,
        onDone: (result) => {
          setSide(side, result);
          if (index + 1 < files.length) cropSide(index + 1);
        },
        onReplace: () => pickFile(side),
      });
    };
    cropSide(0);
  });
})();
