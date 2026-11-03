/* Helpers shared by the home screen and the project window: VICARIUS's modal and toast,
   and its rule for showing a label's full name beside its code. */
window.VA = (function () {
  "use strict";

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // A two-word name whose second word is an English head noun is a common name,
  // shown in roman; every other genus and epithet pair is shown in italic.
  var BINOMIAL_RE = /^[A-Z][a-z]+ [a-z]+$/;
  var COMMON_NAME_HEADS = ["sponge", "coral", "fan", "plume", "rod", "whip", "tape", "line", "plague", "cyanobacteria"];
  function isBinomial(name) {
    if (!BINOMIAL_RE.test(name)) return false;
    return COMMON_NAME_HEADS.indexOf(name.split(" ")[1].toLowerCase()) === -1;
  }
  function labelHtml(code, names) {
    var name = (names || {})[code];
    if (!name) return '<span class="vic-code vic-code-bare">' + esc(code) + "</span>";
    var tag = isBinomial(name) ? "i" : "span";
    return "<" + tag + ' class="vic-name">' + esc(name) + "</" + tag + '> <span class="vic-code">' + esc(code) + "</span>";
  }

  function toast(message, kind) {
    var region = document.querySelector(".toast-region");
    if (!region) return;
    var item = document.createElement("div");
    item.className = "toast " + (kind || "info");
    item.textContent = message;
    region.appendChild(item);
    requestAnimationFrame(function () { item.classList.add("show"); });
    setTimeout(function () {
      item.classList.remove("show");
      setTimeout(function () { item.remove(); }, 220);
    }, 4200);
  }

  function closeModal(backdrop) {
    backdrop.classList.remove("open");
    setTimeout(function () { backdrop.remove(); }, 200);
  }

  function buildModal(options) {
    var backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    var modal = document.createElement("div");
    modal.className = "modal";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    var header = document.createElement("div");
    header.className = "modal-header";
    var title = document.createElement("div");
    title.className = "modal-title";
    title.textContent = options.title || "";
    var close = document.createElement("button");
    close.className = "modal-close";
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.textContent = "x";
    close.addEventListener("click", function () { if (options.onCancel) options.onCancel(); closeModal(backdrop); });
    header.appendChild(title);
    header.appendChild(close);
    var body = document.createElement("div");
    body.className = "modal-body";
    body.innerHTML = options.bodyHtml || "";
    var actions = document.createElement("div");
    actions.className = "modal-actions";
    (options.actions || []).forEach(function (spec) {
      var button = document.createElement("button");
      button.className = spec.kind || "btn-ghost";
      button.type = "button";
      button.textContent = spec.label;
      button.addEventListener("click", function () { spec.handler(backdrop); });
      actions.appendChild(button);
    });
    modal.appendChild(header);
    modal.appendChild(body);
    modal.appendChild(actions);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    requestAnimationFrame(function () { backdrop.classList.add("open"); });
    return backdrop;
  }

  // The launcher's prompt: initials to the left of a purpose, both required. Enter
  // accepts and Shift+Enter adds a line. Resolves to {initials, purpose} or null.
  // onAccept runs inside the click, so a window it opens is not blocked as a popup.
  function promptPurpose(options) {
    return new Promise(function (resolve) {
      var done = false;
      function finish(value) { if (!done) { done = true; resolve(value); } }
      var bodyHtml =
        (options.note ? '<p class="va-note">' + esc(options.note) + "</p>" : "") +
        '<div class="launcher-prompt-row">' +
          '<div class="launcher-prompt-ini">' +
            '<label class="field-label" for="va-initials" title="Type your initials, 1 to 4 letters. They sign the log row for this action, and the annotator credits your masks to them.">Initials <span class="req-star">*</span></label>' +
            '<input id="va-initials" class="field-input mono" type="text" maxlength="4" autocomplete="off">' +
          "</div>" +
          (options.askPurpose === false ? "" :
          '<div class="launcher-prompt-purpose">' +
            '<label class="field-label" for="va-purpose" title="Say why this project is being opened, in one line. VEGA Annotator writes it to the project\'s access log beside your initials.">Purpose</label>' +
            '<textarea id="va-purpose" class="textarea field-input" rows="3"></textarea>' +
          "</div>") +
        "</div>";
      function submit(backdrop) {
        var iniEl = backdrop.querySelector("#va-initials");
        var purposeEl = backdrop.querySelector("#va-purpose");
        var initials = (iniEl.value || "").trim().toUpperCase();
        var purpose = purposeEl ? (purposeEl.value || "").trim() : "";
        if (!/^[A-Za-z]{1,4}$/.test(initials)) { iniEl.focus(); toast("Initials must be 1 to 4 letters.", "warn"); return; }
        if (purposeEl && !purpose) { purposeEl.focus(); toast("Purpose is required.", "warn"); return; }
        if (options.onAccept) options.onAccept();
        closeModal(backdrop);
        finish({ initials: initials, purpose: purpose });
      }
      var backdrop = buildModal({
        title: options.title,
        bodyHtml: bodyHtml,
        onCancel: function () { finish(null); },
        actions: [
          { label: "Cancel", kind: "btn-ghost", handler: function (node) { closeModal(node); finish(null); } },
          { label: options.primaryLabel || "OPEN", kind: "btn-accent", handler: submit }
        ]
      });
      var iniEl = backdrop.querySelector("#va-initials");
      var purposeEl = backdrop.querySelector("#va-purpose");
      iniEl.value = options.initials || "";
      if (purposeEl) purposeEl.value = options.suggestion || "";
      backdrop.addEventListener("keydown", function (event) {
        if (event.key === "Escape") { event.preventDefault(); closeModal(backdrop); finish(null); return; }
        if (event.key !== "Enter" || event.shiftKey) return;
        event.preventDefault();
        if (event.target === iniEl && purposeEl) { purposeEl.focus(); return; }
        submit(backdrop);
      });
      setTimeout(function () { iniEl.focus(); iniEl.select(); }, 60);
    });
  }

  function postJson(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    }).then(function (response) {
      return response.json().catch(function () { return { ok: false, error: "HTTP " + response.status }; });
    });
  }

  // Polls one job until it ends, calling onProgress with its record each time.
  function followJob(jobId, onProgress) {
    return new Promise(function (resolve) {
      function poll() {
        fetch("/api/jobs/" + jobId).then(function (response) { return response.json(); }).then(function (data) {
          var job = data.job || { status: "failed", error: data.error || "The job was lost." };
          onProgress(job);
          if (job.status === "running") setTimeout(poll, 600);
          else resolve(job);
        }).catch(function () { setTimeout(poll, 1500); });
      }
      poll();
    });
  }

  return { esc: esc, labelHtml: labelHtml, toast: toast, buildModal: buildModal, closeModal: closeModal,
           promptPurpose: promptPurpose, postJson: postJson, followJob: followJob };
})();
