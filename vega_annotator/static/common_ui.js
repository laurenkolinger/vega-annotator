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

  function sizeText(bytes) {
    if (bytes == null) return "";
    if (bytes > 0 && bytes < 1e6) return "under 1 MB";
    if (bytes < 1e8) return Math.round(bytes / 1e6) + " MB";
    if (bytes < 1e10) return (bytes / 1e9).toFixed(2) + " GB";
    return bytes < 1e12 ? Math.round(bytes / 1e9) + " GB" : (bytes / 1e12).toFixed(1) + " TB";
  }

  function plural(count, one, many) { return count + " " + (count === 1 ? one : many); }

  // A control a person cannot use right now. It stays in place and keeps a tooltip
  // that says why; aria-disabled, not disabled, so the tooltip shows on hover and the
  // control can still take focus. Click handlers check isLocked first.
  function setLocked(button, locked, reason, tip) {
    if (!button) return;
    button.classList.toggle("va-locked", !!locked);
    if (locked) button.setAttribute("aria-disabled", "true");
    else button.removeAttribute("aria-disabled");
    if (locked && reason) button.title = reason;
    else if (!locked && tip) button.title = tip;
  }
  function isLocked(button) { return !!button && button.getAttribute("aria-disabled") === "true"; }

  // Opens the system folder chooser through the home server. kind is "images" or
  // "destination". Resolves to the route's answer.
  function chooseFolder(kind) { return postJson("/api/folder/choose", { for: kind }); }

  // Says what a scanned folder holds, in sentences, and whether it can come in.
  var TAKEN = "VEGA Annotator takes .jpg, .jpeg and .png files as they are. It converts .heic, .heif and .webp files to JPEG, and .tif, .tiff, .bmp and .gif files to PNG.";
  function scanSentence(summary) {
    if (!summary.images && summary.app_folder) return { ok: false, text: "That folder is " + summary.app_folder + ", not a folder of photos. Choose the folder that holds the photos." };
    if (!summary.images) return { ok: false, text: "That folder holds no images. " + TAKEN };
    var text = plural(summary.images, "image", "images") + " found" +
      (summary.convert ? ", " + summary.convert + " of them to convert first" : "") + ". " +
      (summary.skipped_count ? plural(summary.skipped_count, "file", "files") + " skipped. " : "");
    if (!summary.fits) {
      return { ok: false, text: text + "The copies need " + sizeText(summary.needed_bytes) + " plus 1 GB to spare, and this computer has " + sizeText(summary.free_bytes) + " free." };
    }
    return { ok: true, text: text + "The copies need about " + sizeText(summary.needed_bytes) + ", and this computer has " + sizeText(summary.free_bytes) + " free." };
  }

  // The files a scan skipped, folded away under one line.
  function skippedHtml(summary) {
    var items = summary.skipped || [];
    if (!items.length) return "";
    var more = summary.skipped_count - items.length;
    return '<details class="va-skipped"><summary title="VEGA Annotator leaves these files where they are and copies the rest.">Files skipped</summary><ul>' +
      items.map(function (item) { return '<li><span class="va-skipped-path">' + esc(item.path) + "</span> " + esc(item.reason) + "</li>"; }).join("") +
      (more > 0 ? "<li>and " + plural(more, "more file", "more files") + "</li>" : "") + "</ul></details>";
  }

  // Asks for a folder of images, then follows the scan. onState gets
  // {phase: "choosing" | "scanning" | "done" | "failed" | "cancelled", folder, summary, error}.
  function pickImages(onState) {
    onState({ phase: "choosing" });
    return chooseFolder("images").then(function (answer) {
      if (!answer.ok) { onState({ phase: "failed", error: answer.error }); return; }
      if (answer.cancelled) { onState({ phase: "cancelled" }); return; }
      onState({ phase: "scanning", folder: answer.folder });
      return followJob(answer.job, function () { /* The scan reports only at its end. */ }).then(function (job) {
        if (job.status === "done") onState({ phase: "done", folder: answer.folder, summary: job.result });
        else onState({ phase: "failed", folder: answer.folder, error: job.error });
      });
    });
  }

  // The label list of a project made here: add, and rename or remove before the first
  // export. root is the element to fill; options.buttonClass styles a RENAME or REMOVE
  // that is locked, options.liveClass one that can be used (the page's pink outline), and
  // options.addClass the ADD button; options.inputClass styles the text box, and
  // options.onChange hears the list after every change. Returns {reload}.
  function labelsEditor(root, run, options) {
    options = options || {};
    var url = "/api/projects/" + encodeURIComponent(run) + "/labels";
    var data = null;
    var drawn = "";
    var renaming = "";
    var refocus = "";
    var small = options.buttonClass || "btn-sm";
    var live = options.liveClass || small;
    root.classList.add("va-labels");
    root.innerHTML =
      '<div class="va-labels-note"></div>' +
      '<ol class="va-label-list" aria-label="Labels, in key order"></ol>' +
      '<div class="va-label-add">' +
        '<label class="va-label-add-label" for="va-label-new-' + esc(run) + '" title="Type one label, or several separated by commas. Each new label goes to the end of the list, so its number never changes another label\'s.">Add labels</label>' +
        '<div class="va-label-add-row">' +
          '<input type="text" id="va-label-new-' + esc(run) + '" class="' + (options.inputClass || "") + ' va-label-new" maxlength="400" autocomplete="off" placeholder="paled, recently dead" title="Type one label, or several separated by commas. Each new label goes to the end of the list, so its number never changes another label\'s.">' +
          '<button type="button" class="' + (options.addClass || small) + ' va-label-add-btn" title="Add these labels to the end of the project\'s list. The annotator offers them the next time it opens.">ADD</button>' +
        "</div>" +
      "</div>" +
      '<div class="va-labels-error" role="alert"></div>';
    var note = root.querySelector(".va-labels-note");
    var list = root.querySelector(".va-label-list");
    var input = root.querySelector(".va-label-new");
    var addButton = root.querySelector(".va-label-add-btn");
    var error = root.querySelector(".va-labels-error");

    function showError(text) { error.textContent = text || ""; }

    function noteText() {
      if (data.busy) return "Another action is running on this project. The labels can change once it ends.";
      if (data.open) return "Stop the annotator on this project to change its labels. It reads them once, when it opens.";
      if (data.exported) return "This project has exported frames. VEGA numbers labels in order, so labels can now be added but not renamed or removed.";
      return "Keys 1 to 9 pick the first nine labels in the annotator. A label can be renamed or removed until the first export, while no mask carries it.";
    }

    function rowHtml(name, index) {
      var used = (data.in_use || {})[name] || 0;
      var frozen = data.open || data.busy;
      var key = index < 9 ? '<span class="va-label-key" title="Key ' + (index + 1) + " picks " + esc(name) + ' in the annotator.">' + (index + 1) + "</span>"
        : '<span class="va-label-key va-label-key-none" title="Only the first nine labels have a key. Pick this one from the annotator\'s label buttons.">&middot;</span>';
      var use = '<span class="va-label-use" title="' + (used ? esc(plural(used, "mask carries", "masks carry") + " this label.") : "No mask carries this label yet.") + '">' + (used ? plural(used, "mask", "masks") : "unused") + "</span>";
      var name_ = renaming === name
        ? '<input type="text" class="' + (options.inputClass || "") + ' va-label-rename-input" value="' + esc(name) + '" maxlength="40" aria-label="New name for ' + esc(name) + '" title="Type the new name, then press Enter. Escape keeps the old one.">'
        : '<span class="va-label-name">' + esc(name) + "</span>";
      var actions = "";
      if (!data.exported) {
        var why = frozen ? noteText() : (used ? plural(used, "mask carries", "masks carry") + " " + name + ", so it keeps its name and its place." : "");
        var lock = why ? ' aria-disabled="true"' : "";
        var cls = why ? small + " va-locked" : live;
        actions = renaming === name
          ? '<button type="button" class="' + live + ' va-label-save" data-label="' + esc(name) + '" title="Save the new name for ' + esc(name) + '.">SAVE</button>'
          : '<button type="button" class="' + cls + ' va-label-rename" data-label="' + esc(name) + '"' + lock + ' title="' + esc(why || "Rename " + name + ". VEGA Annotator changes the project's list, and the annotator shows the new name the next time it opens.") + '">RENAME</button>' +
            '<button type="button" class="' + cls + ' va-label-remove" data-label="' + esc(name) + '"' + lock + ' title="' + esc(why || "Remove " + name + " from the project's list. The labels after it move up one key.") + '">REMOVE</button>';
      }
      return '<li class="va-label-row" data-label="' + esc(name) + '">' + key + name_ + use + '<span class="va-label-actions">' + actions + "</span></li>";
    }

    function render() {
      var html = data.labels.map(rowHtml).join("");
      var frozen = data.open || data.busy;
      note.textContent = noteText();
      note.classList.toggle("va-labels-note-warn", !!frozen);
      setLocked(addButton, frozen, noteText(), "Add these labels to the end of the project's list. The annotator offers them the next time it opens.");
      input.disabled = !!frozen;
      if (html !== drawn) {
        list.innerHTML = html;
        drawn = html;
      }
      if (refocus) {
        var again = root.querySelector(refocus);
        refocus = "";
        if (again) again.focus();
      }
      var editing = root.querySelector(".va-label-rename-input");
      if (editing && document.activeElement !== editing) { editing.focus(); editing.select(); }
    }

    function load() {
      return fetch(url).then(function (response) { return response.json(); }).then(function (answer) {
        if (!answer.ok) { showError(answer.error); return; }
        data = answer;
        render();
      }).catch(function () { /* The next reload tries again. */ });
    }

    function change(payload, focusAfter) {
      showError("");
      return postJson(url, payload).then(function (answer) {
        if (!answer.ok) { showError(answer.error); return false; }
        renaming = "";
        refocus = focusAfter || "";
        if (options.onChange) options.onChange(answer.labels);
        return load().then(function () { return true; });
      });
    }

    function add() {
      if (isLocked(addButton)) { showError(addButton.title); return; }
      var text = input.value.trim();
      if (!text) { showError("Type a label to add."); input.focus(); return; }
      change({ action: "add", names: text }, ".va-label-new").then(function (done) { if (done) input.value = ""; });
    }

    function rowSelector(kind, name) { return ".va-label-" + kind + '[data-label="' + CSS.escape(name) + '"]'; }

    function saveRename(name) {
      var box = root.querySelector(".va-label-rename-input");
      var next = box ? box.value.trim() : "";
      if (!next || next === name) { renaming = ""; drawn = ""; refocus = rowSelector("rename", name); render(); return; }
      change({ action: "rename", old: name, new: next }, rowSelector("rename", next));
    }

    root.addEventListener("click", function (event) {
      var button = event.target.closest("button");
      if (!button || !root.contains(button)) return;
      var name = button.getAttribute("data-label");
      if (isLocked(button)) { showError(button.title); return; }
      if (button.classList.contains("va-label-add-btn")) add();
      else if (button.classList.contains("va-label-rename")) { showError(""); renaming = name; render(); }
      else if (button.classList.contains("va-label-save")) saveRename(name);
      else if (button.classList.contains("va-label-remove")) change({ action: "remove", name: name }, ".va-label-new");
    });

    root.addEventListener("keydown", function (event) {
      if (event.target === input && event.key === "Enter") { event.preventDefault(); add(); return; }
      if (!event.target.classList.contains("va-label-rename-input")) return;
      if (event.key === "Enter") { event.preventDefault(); saveRename(renaming); }
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        var name = renaming;
        renaming = "";
        refocus = rowSelector("rename", name);
        render();
      }
    });

    load();
    // A rename in progress is never redrawn away by a refresh.
    return { reload: function () { return renaming ? Promise.resolve() : load(); } };
  }

  return { esc: esc, labelHtml: labelHtml, toast: toast, buildModal: buildModal, closeModal: closeModal,
           promptPurpose: promptPurpose, postJson: postJson, followJob: followJob, sizeText: sizeText,
           plural: plural, setLocked: setLocked, isLocked: isLocked, chooseFolder: chooseFolder,
           scanSentence: scanSentence, skippedHtml: skippedHtml, pickImages: pickImages,
           labelsEditor: labelsEditor };
})();
