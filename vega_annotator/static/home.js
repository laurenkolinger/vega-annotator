/* The home screen: project cards in the VICARIUS launcher's layout. */
(function () {
  "use strict";
  var VA = window.VA;
  var MODULE_NAME = "reef_point_seg";
  var REFRESH_MS = 4000;
  var SPECIES_CHIP_MAX = 4;
  var WINDOW_FEATURES = "popup,width=1480,height=960,scrollbars=yes,resizable=yes";
  var SAM_JOB_KINDS = ["sam3", "sam3_download", "self_test"];
  var state = null;
  var progressByRun = {};
  var adoptedRuns = {};
  var lastListHtml = "";
  var samBusy = false;
  var newProjectJob = "";
  var NO_SERVER_INSTALL = "Connect the server, then choose its VEGA_Annotator folder, to install SAM3 from it.";
  var NO_COPY_INSTALL = "This server folder holds no copy of SAM3 yet. The mainframe's publish tool adds it.";
  var INSTALL_TIP = "VEGA Annotator copies SAM3, 3.4 GB, from the server folder to this computer.";
  var CLOSED_FIRST = "Stop the annotator on this project first, so it saves its work and lets go of the project.";

  function $(id) { return document.getElementById(id); }
  function esc(value) { return VA.esc(value); }
  var sizeText = VA.sizeText;

  var STATE_TEXT = {
    on_server: { chip: "On the server", kind: "", tip: "VEGA Annotator holds this project in the server folder only. Bring it in to label it on this computer." },
    here: { chip: "On this computer", kind: "good", tip: "This computer holds the project, and no labeling work waits to go back." },
    changed: { chip: "On this computer, not put back", kind: "warn", tip: "This computer holds labeling work the server folder does not have yet." },
    made_here: { chip: "Made on this computer", kind: "good", tip: "This computer made this project from a folder of images and holds its only copy. EXPORT FOR VEGA hands its exported frames to VEGA." },
    put_back: { chip: "Back on the server", kind: "good", tip: "The server folder has this project's latest work. Bring it in again to keep labeling." },
    server_newer: { chip: "Newer on the server", kind: "warn", tip: "Another computer put newer work in the server folder. Bring it in to get that work." },
    held_elsewhere: { chip: "In use elsewhere", kind: "warn", tip: "Another computer holds this project. Take over only when that computer is done with it." },
    conflict: { chip: "Changed in two places", kind: "bad", tip: "This computer has work the server folder lacks, and the server copy moved on too. Put Back saves this computer's work beside the project and overwrites nothing." },
    saved_beside: { chip: "Saved beside the project", kind: "warn", tip: "This computer's work is safe in the project's conflicts folder on the server. Bringing the project in replaces this computer's copy with the server's." },
    no_server: { chip: "Server not connected", kind: "", tip: "The work is safe in the server folder. Connect the server to bring the project in again." },
    not_on_server: { chip: "Not in this server folder", kind: "warn", tip: "This server folder does not hold this project, so this computer's copy is the one to keep. Check the chosen folder, or publish the project again from the mainframe." },
    error: { chip: "Cannot be read", kind: "bad", tip: "VEGA Annotator cannot read this project's records." }
  };
  // A project made here before its first import has ended: still coming in, or stopped partway.
  var IMPORTING_TEXT = { chip: "Bringing images in", kind: "", tip: "VEGA Annotator is copying this new project's images in. Its actions appear once the import ends." };
  var PARTWAY_TEXT = { chip: "Import stopped partway", kind: "warn", tip: "This new project's import stopped before every image came in. ADD IMAGES with the same folder brings in the rest." };
  var PARTWAY_NOTE = "The import stopped partway. Use ADD IMAGES with the same folder to bring in the rest, or REMOVE the project. Images already here are skipped.";

  function madeHere(card) { return card.state === "made_here"; }
  // A project made here whose first import has not ended: it takes only ADD IMAGES and REMOVE.
  function partway(card) { return madeHere(card) && !!card.creating; }
  function cardFor(run) { return state.projects.filter(function (item) { return item.run === run; })[0]; }
  function annotatorOn(card) { return state.annotator.run === card.run && (state.annotator.state === "loading" || state.annotator.state === "ready"); }
  // A project made here keeps its labels as plain words: no codes, never italic.
  function customLabels(card) { return (card.target_labels || []).map(function (label) { return label.code; }); }

  function speciesChips(card) {
    var names = card.label_names || {};
    var pairs = Object.keys(card.species || {}).map(function (code) { return [code, card.species[code]]; })
      .sort(function (a, b) { return b[1] - a[1] || (a[0] < b[0] ? -1 : 1); });
    var html = pairs.slice(0, SPECIES_CHIP_MAX).map(function (pair) {
      var label = madeHere(card) ? '<span class="va-label-word">' + esc(pair[0]) + "</span>" : VA.labelHtml(pair[0], names);
      return '<span class="run-species-chip">' + label + " <b>" + pair[1] + "</b></span>";
    }).join("");
    var more = pairs.length - SPECIES_CHIP_MAX;
    if (more > 0) html += '<span class="run-species-chip run-species-chip-more">+' + more + " more</span>";
    return html;
  }

  function reviewerChip(card) {
    var who = (card.reviewers || []).filter(Boolean);
    if (!who.length) return "";
    return '<span class="run-reviewer-chip" title="These initials are recorded on this project\'s masks: ' + esc(who.join(", ")) + '.">by ' + esc(who.join(", ")) + "</span>";
  }

  function statsLine(card) {
    return '<div class="run-card-stats"><span class="run-card-stat-count">' + VA.plural(card.n_masks || 0, "mask", "masks") + ", " + VA.plural(card.n_images || 0, "image", "images") + "</span>" +
      (card.size_bytes == null ? "" : '<span class="run-card-stat-count va-size" title="VEGA Annotator counts this project\'s files: frames, labels, and records. Image links count as nothing.">' + sizeText(card.size_bytes) + "</span>") +
      speciesChips(card) + reviewerChip(card) + "</div>";
  }

  function stageDots(card) {
    return (card.stages || []).map(function (stage) {
      return '<span class="step-dot step-' + esc(stage.status) + '" title="' + esc(stage.name + " is " + stage.status + ".") + '"></span>';
    }).join("");
  }

  function targetsLine(card) {
    if (madeHere(card)) {
      return '<div class="va-targets" title="The person who made this project typed these labels. LABELS adds more.">Labels: ' +
        customLabels(card).map(function (label) { return '<span class="va-label-word">' + esc(label) + "</span>"; }).join(", ") + "</div>";
    }
    var codes = (card.target_labels || []).map(function (label) { return VA.labelHtml(label.code, card.label_names); }).join(", ");
    return '<div class="va-targets" title="VEGA took these target labels from this project\'s Choose Images step.">Target: ' + codes + "</div>";
  }

  function holderText(card) {
    var holder = card.holder;
    if (holder && holder.computer === state.computer) return "";
    if (!holder) return "";
    return "In use on " + holder.computer + (holder.initials ? " by " + holder.initials : "") + " since " + String(holder.at || "").slice(0, 16).replace("T", " ");
  }

  // Why a card's description cannot be edited now.
  function descriptionLock(card) {
    return partway(card) ? "Finish the import with ADD IMAGES to edit this description." : "Bring the project in to edit its description.";
  }

  function canOpen(card) {
    if (partway(card)) return false;
    return card.state === "here" || card.state === "changed" || madeHere(card) || (card.state === "not_on_server" && card.holding);
  }

  function button(card, action, label, kind, tip, lockedWhy) {
    var lock = lockedWhy ? ' aria-disabled="true"' : "";
    return '<button type="button" class="' + kind + ' run-action' + (lockedWhy ? " va-locked" : "") + '" data-action="' + action + '" data-run="' + esc(card.run) + '"' + lock + ' title="' + esc(lockedWhy || tip) + '">' + label + "</button>";
  }

  function madeHereButtons(card) {
    if (partway(card)) {
      return button(card, "add-images", "ADD IMAGES", "btn-accent-outline", "Bring in the rest of the folder this project was made from. VEGA Annotator skips the images already here.");
    }
    var open = annotatorOn(card);
    var noExport = !card.frames_exported ? "Export a batch in the annotator first. EXPORT FOR VEGA hands VEGA the frames the annotator has exported." : "";
    return button(card, "open", "OPEN &rarr;", "btn-accent-outline", "Prompt for initials and purpose, then open this project in its own window.") +
      button(card, "add-images", "ADD IMAGES", "btn-accent-outline", "Pick another folder of images. VEGA Annotator copies them into this project as new frames.", open ? CLOSED_FIRST : "") +
      button(card, "labels", "LABELS", "btn-accent-outline", "See this project's labels and add more. Until the first export you can also rename or remove a label no mask carries.") +
      button(card, "export-for-vega", "EXPORT FOR VEGA", "btn-accent", "Write this project's exported frames, masks and labels to one new folder that VEGA trains from.", open ? CLOSED_FIRST : noExport);
  }

  function buttonsFor(card) {
    var bringIn = button(card, "bring-in", "BRING IN &rarr;", "btn-accent", "Copy this project from the server folder to this computer and mark it as held here. VEGA Annotator checks every file as it copies.");
    var open = button(card, "open", "OPEN &rarr;", "btn-accent-outline", "Prompt for initials and purpose, then open this project in its own window.");
    var putBack = button(card, "put-back", "PUT BACK", "btn-accent", "Send this computer's work to the server folder. VEGA Annotator saves and closes the annotator first, and checks every file it sends.");
    var takeOver = button(card, "take-over", "TAKE OVER", "btn-accent-outline", "Mark this project as held here even though another computer holds it. Work on that computer stays there and goes beside the project when it is put back.");
    if (progressByRun[card.run]) return "";
    var heldByOther = card.holder && card.holder.computer !== state.computer;
    var unsent = card.unsent_records ? putBack : "";
    switch (card.state) {
      case "on_server": case "put_back": case "server_newer": return bringIn + unsent;
      case "saved_beside": return heldByOther ? takeOver : bringIn;
      case "here": case "changed": return open + putBack;
      case "made_here": return madeHereButtons(card);
      case "not_on_server": return card.holding ? open : "";
      case "held_elsewhere": return takeOver + unsent;
      case "conflict": return putBack;
      default: return "";
    }
  }

  function stopButton(card) {
    if (state.annotator.run !== card.run || state.annotator.state === "stopped") return "";
    return '<button type="button" class="btn-accent-outline run-action va-stop" data-action="stop-annotator" data-run="' + esc(card.run) +
      '" title="Save the annotator\'s work, stop it, and free this computer\'s memory.">STOP ANNOTATOR</button>';
  }

  function progressLine(card) {
    if (partway(card)) return VA.plural(card.frames_total || 0, "image", "images") + " in so far";
    if (madeHere(card)) return VA.plural(card.frames_exported || 0, "frame", "frames") + " exported";
    var done = card.completed_count || 0;
    var total = card.total_steps || 0;
    return total ? done + " / " + total + " complete (" + Math.round(done * 100 / total) + "%)" : "not started";
  }

  function canRemove(card) {
    if (card.where === "server") return false;
    if (madeHere(card)) return true;
    if (card.state === "not_on_server") return !card.holding && !card.pending_files && !card.unsent_records;
    return ["put_back", "server_newer", "no_server", "saved_beside", "held_elsewhere"].indexOf(card.state) !== -1;
  }

  function removeTip(card) {
    if (madeHere(card)) return state.trash ? "Move this project to the Trash. This computer holds its only copy." : "Delete this project. This computer holds its only copy.";
    return card.state === "not_on_server" ? "Remove this computer's copy of the project." : "Remove this computer's copy of the project. The server folder keeps it.";
  }

  function removeButton(card) {
    if (!canRemove(card) || progressByRun[card.run]) return "";
    return '<div class="run-card-archive"><button class="run-archive-btn run-action" data-action="remove" data-run="' + esc(card.run) + '" type="button" aria-label="Remove from this computer" title="' + esc(removeTip(card)) + '">' +
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg></button></div>';
  }

  function progressHtml(run) {
    var job = progressByRun[run];
    if (!job) return "";
    var percent = job.total ? Math.round(job.done * 100 / job.total) : 0;
    return '<div class="va-note">' + esc(job.message || "Starting") + (job.total ? " " + percent + "%" : "") + "</div>" +
      '<div class="va-progress"><div class="va-progress-bar" style="width:' + percent + '%"></div></div>';
  }

  function cardHtml(card) {
    var words = partway(card) ? (progressByRun[card.run] ? IMPORTING_TEXT : PARTWAY_TEXT) : (STATE_TEXT[card.state] || STATE_TEXT.error);
    var title = esc(card.project_name || card.run);
    var when = String(card.updated_at || card.created_at || "").slice(0, 19);
    if (card.error && !card.project_name) {
      return '<div class="run-card"><div class="run-card-head"><div class="run-card-title">' + esc(card.run) + "</div></div>" +
        '<div class="run-card-error">' + esc(card.error) + "</div></div>";
    }
    var left = card.frames_left == null ? "" : VA.plural(card.frames_left, "frame", "frames") + " left of " + card.frames_total;
    var holder = holderText(card);
    var editable = canOpen(card);
    var descriptionAttributes = editable ? ' role="button" tabindex="0" title="Click to edit this description."' : ' title="' + esc(descriptionLock(card)) + '"';
    var noDescription = editable ? "add a description..." : "no description";
    return '<div class="run-card' + (editable ? " va-openable" : "") + '" data-run="' + esc(card.run) + '">' +
      '<div class="run-card-head"><div class="run-card-title" title="This project\'s run folder is ' + esc(card.run) + '.">' + title + "</div>" +
      '<div class="run-card-when" title="This line names the run folder and when the project was last saved.">' + esc(card.run) + " &middot; " + esc(when) + "</div></div>" +
      '<div class="run-card-desc' + (card.description ? "" : " empty") + '" data-run="' + esc(card.run) + '"' + descriptionAttributes + ">" + (card.description ? esc(card.description) : noDescription) + "</div>" +
      statsLine(card) +
      (madeHere(card) ? "" : '<div class="run-card-steps">' + stageDots(card) + "</div>") +
      '<div class="run-card-state"><span class="launcher-chip ' + words.kind + '" title="' + esc(words.tip) + '">' + words.chip + "</span>" +
      '<span class="run-card-frames" title="VEGA Annotator counts the frames not yet exported, out of all the project\'s frames.">' + esc(left) + "</span>" +
      stopButton(card) + "</div>" +
      targetsLine(card) +
      (holder ? '<div class="va-note">' + esc(holder) + "</div>" : "") +
      (card.conflict_folder ? '<div class="va-note">This computer\'s work is saved in ' + esc(card.conflict_folder) + "</div>" : "") +
      (card.error ? '<div class="run-card-error">' + esc(card.error) + "</div>" : "") +
      (partway(card) && !progressByRun[card.run] ? '<div class="va-note warn">' + esc(PARTWAY_NOTE) + "</div>" : "") +
      progressHtml(card.run) +
      '<div class="run-card-foot' + (madeHere(card) ? " va-foot-wide" : "") + '"><span class="run-card-progress" title="' +
      (partway(card) ? "VEGA Annotator counts the images this new project holds so far." : madeHere(card) ? "The annotator exported these frames. EXPORT FOR VEGA hands them to VEGA." : "VEGA Annotator counts the stages this project has finished, as the VICARIUS page does.") + '">' + progressLine(card) + "</span>" +
      '<div class="run-card-actions">' + buttonsFor(card) + "</div></div>" +
      removeButton(card) + "</div>";
  }

  // The SAM3 line: what this computer has, and the ways to get it when it is missing.
  function samJob() {
    return (state.jobs || []).filter(function (job) { return SAM_JOB_KINDS.indexOf(job.kind) !== -1; })[0];
  }

  function renderSam() {
    var sam = state.sam3;
    var server = state.server;
    var samChip = $("sam3-chip");
    samChip.textContent = state.unsupported ? "Cannot run here" : (sam.installed ? "SAM3 ready" : "SAM3 not installed");
    samChip.className = "launcher-chip " + (state.unsupported ? "bad" : (sam.installed ? "good" : "warn"));
    samChip.title = state.unsupported ? "SAM3 cannot run on this computer, so the annotator cannot open here." :
      (sam.installed ? "SAM3's weights are on this computer, " + sam.size_gb + " GB." : "SAM3's weights are not on this computer yet. The annotator needs them to draw outlines.");
    $("plan-label").textContent = state.unsupported || (state.plan.label + " " + state.ram_gb + " GB of memory.");
    var missing = !sam.installed && !state.unsupported;
    $("download-sam3").hidden = !missing || samBusy;
    var install = $("install-sam3");
    install.hidden = !missing || samBusy;
    VA.setLocked(install, !sam.on_server, server.connected ? NO_COPY_INSTALL : NO_SERVER_INSTALL, INSTALL_TIP);
    $("test-sam3").hidden = !sam.installed || !!state.unsupported || samBusy;
    // The note follows the state until Download, Install or Test writes a result, which stays until the next one.
    var note = $("sam3-result");
    if (!samBusy && (!note.textContent || note.getAttribute("data-from") === "state")) {
      note.textContent = missing && sam.download_failed ? sam.download_failed + " Click DOWNLOAD SAM3 to continue from where it stopped." : "";
      note.className = "va-note" + (note.textContent ? " bad" : "");
      note.setAttribute("data-from", "state");
    }
  }

  function renderPanels() {
    var server = state.server;
    var chip = $("server-chip");
    chip.textContent = server.connected ? "Connected" : "Not connected";
    chip.className = "launcher-chip " + (server.connected ? "good" : "warn");
    chip.title = server.connected ? "VEGA Annotator can read this server folder now." : "VEGA Annotator cannot reach a server folder now. Projects made on this computer work without one.";
    $("server-path").textContent = server.path || "No folder chosen";
    $("server-reason").textContent = server.connected ? "" : server.reason;
    renderSam();
    var disk = state.disk;
    var here = state.projects.filter(function (card) { return card.where !== "server"; })
      .reduce(function (sum, card) { return sum + (card.size_bytes || 0); }, 0);
    $("disk-note").textContent = sizeText(disk.free_bytes) + " free of " + sizeText(disk.total_bytes) + " on this computer. Projects here use " + sizeText(here) + ".";
  }

  // The same control after a redraw: a card's button, or its description.
  function focusSelector(element) {
    var run = element && element.getAttribute ? element.getAttribute("data-run") : null;
    if (!run) return "";
    var action = element.getAttribute("data-action");
    return (action ? '[data-action="' + CSS.escape(action) + '"]' : ".run-card-desc") + '[data-run="' + CSS.escape(run) + '"]';
  }

  function render() {
    renderPanels();
    var list = $("runs-list");
    var focus = document.activeElement;
    if (focus && focus.classList && focus.classList.contains("va-desc-edit")) return;
    var html = state.projects.length
      ? state.projects.map(cardHtml).join("")
      : '<div class="activity-empty">&gt; no projects yet: start one with NEW PROJECT, or connect the server folder</div>';
    // A refresh that changes nothing leaves the page alone, so keyboard focus stays put.
    if (html === lastListHtml) return;
    var selector = focusSelector(focus);
    list.innerHTML = html;
    lastListHtml = html;
    var again = selector && list.querySelector(selector);
    if (again) again.focus();
  }

  // A job keeps running when this page reloads, so its progress comes back from the state:
  // a project's job on its card, SAM3's on its line, and a new project's under NEW PROJECT.
  function adoptRunningJobs(running) {
    var live = {};
    running.forEach(function (job) {
      if (SAM_JOB_KINDS.indexOf(job.kind) !== -1 && !samBusy) { followSamJob(job.id); return; }
      if (job.kind === "new_project" && !newProjectJob) followNewProject(job.id, null);
      if (!job.run) return;
      live[job.run] = true;
      if (!progressByRun[job.run] || adoptedRuns[job.run]) {
        progressByRun[job.run] = job;
        adoptedRuns[job.run] = true;
      }
    });
    Object.keys(adoptedRuns).forEach(function (run) {
      if (!live[run]) { delete progressByRun[run]; delete adoptedRuns[run]; }
    });
  }

  function refresh() {
    return fetch("/api/state").then(function (response) { return response.json(); }).then(function (data) {
      state = data;
      adoptRunningJobs(data.jobs || []);
      render();
    }).catch(function () { /* The next refresh tries again. */ });
  }

  function savedInitials() { return state && state.settings ? state.settings.initials : ""; }

  function runJob(run, url, payload, doneText) {
    progressByRun[run] = { done: 0, total: 0, message: "Starting" };
    render();
    return VA.postJson(url, payload).then(function (data) {
      if (!data.ok) { delete progressByRun[run]; VA.toast(data.error, "err"); refresh(); return null; }
      return VA.followJob(data.job, function (job) {
        progressByRun[run] = job;
        render();
      }).then(function (job) {
        delete progressByRun[run];
        if (job.status === "failed") {
          VA.toast(job.error, "err");
        } else if (job.result && job.result.conflict) {
          VA.toast("Work saved beside the project, overwriting nothing: " + job.result.reason, "warn");
        } else {
          VA.toast(typeof doneText === "function" ? doneText(job.result || {}) : doneText, "ok");
        }
        refresh();
        return job;
      });
    });
  }

  function spaceNote(run) {
    var card = cardFor(run);
    if (!card || card.size_bytes == null || card.where !== "server") return "";
    return "This copies " + sizeText(card.size_bytes) + " to this computer, and labeling can add up to " +
      sizeText((card.frames_total || 0) * state.disk.growth_per_frame_bytes) + " more. This computer has " + sizeText(state.disk.free_bytes) + " free.";
  }

  function bringIn(run, takeOver) {
    VA.promptPurpose({
      title: (takeOver ? "Take over project: " : "Bring in project: ") + "initials",
      note: (takeOver ? "This computer will hold the project. Any work the other computer has not put back stays there, and goes beside the project when it is put back. " : "") + spaceNote(run),
      askPurpose: false, initials: savedInitials(), primaryLabel: takeOver ? "TAKE OVER" : "BRING IN"
    }).then(function (answer) {
      if (!answer) return;
      runJob(run, "/api/projects/" + encodeURIComponent(run) + "/bring-in",
        { initials: answer.initials, take_over: !!takeOver }, run + " is on this computer.");
    });
  }

  function putBack(run) {
    var initials = savedInitials();
    if (initials) {
      runJob(run, "/api/projects/" + encodeURIComponent(run) + "/put-back", { initials: initials }, run + " is back in the server folder.");
      return;
    }
    VA.promptPurpose({ title: "Put back project: initials", askPurpose: false, primaryLabel: "PUT BACK" }).then(function (answer) {
      if (!answer) return;
      runJob(run, "/api/projects/" + encodeURIComponent(run) + "/put-back", { initials: answer.initials }, run + " is back in the server folder.");
    });
  }

  function openProject(run) {
    var opened = null;
    VA.promptPurpose({
      title: "Open project: initials + purpose", initials: savedInitials(),
      suggestion: "running " + MODULE_NAME + " on run " + run, primaryLabel: "OPEN",
      onAccept: function () { opened = window.open("about:blank", "reefpointseg_orchestrator", WINDOW_FEATURES); }
    }).then(function (answer) {
      if (!answer) return;
      VA.postJson("/api/projects/" + encodeURIComponent(run) + "/open", answer).then(function (data) {
        if (!data.ok) { if (opened) opened.close(); VA.toast(data.error, "err"); return; }
        if (opened) { opened.location = data.url; try { opened.focus(); } catch (e) { /* Focus is best effort. */ } }
        else VA.toast("Allow popups for this page, then click again.", "warn");
        VA.toast("Project opened, logged as " + answer.initials + ".", "ok");
        refresh();
      });
    });
  }

  // A modal that closes on Escape and gives focus back to the control that opened it,
  // named by options.returnTo, since a click does not focus a button in every browser.
  function dialog(options) {
    var backdrop;
    function giveFocusBack() {
      var target = options.returnTo && document.querySelector(options.returnTo);
      if (target && target.focus) target.focus();
    }
    function close() {
      if (!backdrop.parentNode) return;
      VA.closeModal(backdrop);
      if (options.onClose) options.onClose();
      giveFocusBack();
    }
    backdrop = VA.buildModal({
      title: options.title, bodyHtml: options.bodyHtml, onCancel: function () { if (options.onClose) options.onClose(); giveFocusBack(); },
      actions: (options.actions || []).map(function (spec) {
        return { label: spec.label, kind: spec.kind, handler: function () { if (spec.handler) spec.handler(close); else close(); } };
      })
    });
    backdrop.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { event.preventDefault(); close(); }
    });
    backdrop.close = close;
    var buttons = backdrop.querySelectorAll(".modal-actions button");
    (options.actions || []).forEach(function (spec, index) {
      if (spec.title) buttons[index].title = spec.title;
      if (spec.id) buttons[index].id = spec.id;
    });
    var close_ = backdrop.querySelector(".modal-close");
    close_.title = "Close this window. Whatever you saved stays saved.";
    setTimeout(function () {
      var first = options.focus && backdrop.querySelector(options.focus);
      if (first) first.focus();
    }, 60);
    return backdrop;
  }

  function cardButton(action, run) { return '.run-action[data-action="' + action + '"][data-run="' + CSS.escape(run) + '"]'; }

  function removeProject(run) {
    var card = cardFor(run);
    var name = card ? (card.project_name || run) : run;
    var words;
    if (card && madeHere(card)) {
      words = "This computer holds the only copy of " + esc(name) + ". " + (state.trash
        ? "REMOVE moves it to the Trash, and emptying the Trash deletes it for good."
        : "REMOVE deletes its images, masks and labels for good.");
    } else {
      words = card && card.state === "not_on_server"
        ? "This server folder does not hold " + esc(run) + ". This computer's copy goes away."
        : "The server folder keeps " + esc(run) + ". This computer's copy goes away.";
    }
    return dialog({
      title: "Remove from this computer",
      returnTo: cardButton("remove", run),
      bodyHtml: '<div class="va-note">' + words + "</div>",
      focus: ".modal-actions .btn-ghost",
      actions: [
        { label: "Cancel", kind: "btn-ghost", title: "Keep the project as it is." },
        { label: "REMOVE", kind: "btn-accent", title: removeTip(card || {}), handler: function (close) {
          close();
          VA.postJson("/api/projects/" + encodeURIComponent(run) + "/remove", {}).then(function (data) {
            var done = data.trashed ? name + " is in the Trash." : run + " is off this computer.";
            VA.toast(data.ok ? done : data.error, data.ok ? "ok" : "err");
            refresh();
          });
        } }
      ]
    });
  }

  // The folder field NEW PROJECT and ADD IMAGES share: CHOOSE FOLDER, the path, and
  // what the scan found. Calls onSummary with the summary, or null while there is none.
  function imagesFieldHtml(prefix) {
    return '<label class="field-label" for="' + prefix + '-folder-btn" title="Pick the folder that holds the images. VEGA Annotator also looks in its subfolders.">Image folder <span class="req-star">*</span></label>' +
      '<div class="va-field-row"><span id="' + prefix + '-folder" class="va-path va-path-empty">No folder chosen</span>' +
      '<button type="button" id="' + prefix + '-folder-btn" class="btn-accent-outline" title="Open the folder chooser. VEGA Annotator then looks through the folder and says what it found.">CHOOSE FOLDER</button></div>';
  }

  function wireImagesField(backdrop, prefix, onSummary) {
    var pathLine = backdrop.querySelector("#" + prefix + "-folder");
    var summaryLine = backdrop.querySelector("#" + prefix + "-summary");
    var choose = backdrop.querySelector("#" + prefix + "-folder-btn");
    var busy = false;
    choose.addEventListener("click", function () {
      if (busy) return;
      busy = true;
      // A cancelled chooser leaves the folder chosen before, so its summary comes back.
      var before = { html: summaryLine.innerHTML, cls: summaryLine.className };
      VA.pickImages(function (step) {
        if (step.phase === "choosing") { summaryLine.className = "va-summary"; summaryLine.textContent = "Waiting for the folder chooser."; return; }
        if (step.phase === "cancelled") { busy = false; summaryLine.className = before.cls; summaryLine.innerHTML = before.html; return; }
        if (step.folder) { pathLine.textContent = step.folder; pathLine.classList.remove("va-path-empty"); }
        if (step.phase === "scanning") { summaryLine.textContent = "Looking through the folder."; onSummary(null, step.folder); return; }
        busy = false;
        if (step.phase === "failed") { summaryLine.className = "va-summary bad"; summaryLine.textContent = step.error; onSummary(null, ""); return; }
        var said = VA.scanSentence(step.summary);
        summaryLine.className = "va-summary " + (said.ok ? "good" : "bad");
        summaryLine.innerHTML = esc(said.text) + VA.skippedHtml(step.summary);
        onSummary(said.ok ? step.summary : null, step.folder);
      });
    });
  }

  function followProgress(job, bar, line) {
    if (!job.total) { line.textContent = job.message || "Starting"; return; }
    bar.hidden = false;
    bar.firstElementChild.style.width = Math.round(job.done * 100 / job.total) + "%";
    line.textContent = job.message + " " + Math.round(job.done * 100 / job.total) + "%";
  }

  // What NEW PROJECT or ADD IMAGES brought in, with every file it skipped listed beneath.
  function importedText(result, lead, into) {
    var skipped = result.skipped_count || 0;
    return lead + VA.plural(result.added || 0, "image", "images") + " added" + (into ? " to " + into : "") +
      (skipped ? ", " + VA.plural(skipped, "file", "files") + " skipped." : ".");
  }

  function newProjectDone(result) { return importedText(result, (result.run || "The project") + " is ready: ", ""); }

  // Turns a dialog's Cancel into Close once its job is done, and hides the button that started it.
  function finishDialog(backdrop, startButton, closeTip) {
    startButton.hidden = true;
    var done = backdrop.querySelector(".modal-actions .btn-ghost");
    done.textContent = "Close";
    done.title = closeTip;
    done.focus();
  }

  // Shows a new project's progress under NEW PROJECT, on its card, and in its dialog while that is open.
  function followNewProject(jobId, backdrop) {
    newProjectJob = jobId;
    var status = $("new-project-status");
    var bar = $("new-project-progress");
    return VA.followJob(jobId, function (job) {
      followProgress(job, bar, status);
      if (backdrop && backdrop.parentNode) followProgress(job, backdrop.querySelector("#np-progress"), backdrop.querySelector("#np-result"));
      if (job.run && job.status === "running") {
        progressByRun[job.run] = job;
        render();
      }
    }).then(function (job) {
      newProjectJob = "";
      bar.hidden = true;
      if (job.run) delete progressByRun[job.run];
      if (job.status === "failed") {
        status.textContent = job.error;
        status.className = "va-note bad";
        if (backdrop && backdrop.parentNode) {
          backdrop.querySelector("#np-progress").hidden = true;
          var line = backdrop.querySelector("#np-result");
          line.textContent = job.error;
          line.className = "va-note bad";
        }
        return refresh().then(function () {
          if (!backdrop || !backdrop.parentNode) return;
          var create = backdrop.querySelector("#np-create");
          // A project whose import stopped is finished with ADD IMAGES on its card, never made twice.
          if (job.run && cardFor(job.run)) finishDialog(backdrop, create, "Close this window. The project's card finishes the import with ADD IMAGES.");
          else VA.setLocked(create, false);
        });
      }
      var result = job.result || {};
      var listed = esc(newProjectDone(result)) + VA.skippedHtml(result);
      status.textContent = "";
      status.className = "va-note";
      if (backdrop && backdrop.parentNode && result.skipped_count) {
        // The dialog stays, so the person can read which files stayed behind and why. The
        // result lists every skipped file, the scan's too, so the scan's own line goes.
        backdrop.querySelector("#np-progress").hidden = true;
        backdrop.querySelector("#np-summary").hidden = true;
        var line = backdrop.querySelector("#np-result");
        line.innerHTML = listed;
        line.className = "va-note va-selectable good";
        finishDialog(backdrop, backdrop.querySelector("#np-create"), "Close this window. The project is made.");
      } else {
        if (backdrop && backdrop.parentNode) backdrop.close();
        else if (result.skipped_count) { status.innerHTML = listed; status.className = "va-note good"; }
        VA.toast(newProjectDone(result), "ok");
      }
      lastListHtml = "";
      return refresh().then(function () {
        // While the dialog stays open to list skipped files, focus stays on its Close button;
        // the dialog returns focus to the page itself when the person closes it.
        if (backdrop && backdrop.parentNode && backdrop.classList.contains("open")) return;
        var run = (job.result || {}).run;
        var open = run && document.querySelector('.run-action[data-action="open"][data-run="' + CSS.escape(run) + '"]');
        if (open) open.focus();
      });
    });
  }

  function newProject() {
    if (newProjectJob) { VA.toast("A new project is still being made. Its progress shows under NEW PROJECT.", "info"); return; }
    var chosen = null;
    var folder = "";
    var backdrop = dialog({
      title: "New project",
      returnTo: "#new-project",
      focus: "#np-folder-btn",
      bodyHtml:
        '<p class="va-note">VEGA Annotator copies the images into a new project on this computer and leaves your folder as it is.</p>' +
        imagesFieldHtml("np") +
        '<label class="field-label" for="np-name" title="Name the project in your own words. The project\'s folder takes the date, your initials and this name.">Project name <span class="req-star">*</span></label>' +
        '<input id="np-name" class="field-input" type="text" maxlength="80" autocomplete="off" title="Name the project in your own words. The project\'s folder takes the date, your initials and this name.">' +
        '<label class="field-label" for="np-labels" title="Type the labels you will give masks, such as healthy, bleached and diseased. One per line, or separated by commas.">Labels <span class="req-star">*</span></label>' +
        '<textarea id="np-labels" class="textarea field-input va-labels-box" rows="3" placeholder="healthy, bleached, diseased" title="Type the labels you will give masks, such as healthy, bleached and diseased. One per line, or separated by commas."></textarea>' +
        '<div class="va-hint">One per line, or separated by commas. Keys 1 to 9 pick the first nine.</div>' +
        '<div class="launcher-prompt-row">' +
          '<div class="launcher-prompt-ini">' +
            '<label class="field-label" for="va-initials" title="Type your initials, 1 to 4 letters. They sign the project\'s log, and the annotator credits your masks to them.">Initials <span class="req-star">*</span></label>' +
            '<input id="va-initials" class="field-input mono" type="text" maxlength="4" autocomplete="off" title="Type your initials, 1 to 4 letters. They sign the project\'s log, and the annotator credits your masks to them.">' +
          "</div>" +
          '<div class="launcher-prompt-purpose">' +
            '<label class="field-label" for="np-purpose" title="Say what this project is for, in one line. VEGA Annotator writes it to the project\'s log beside your initials.">Purpose <span class="req-star">*</span></label>' +
            '<textarea id="np-purpose" class="textarea field-input" rows="2" title="Say what this project is for, in one line. VEGA Annotator writes it to the project\'s log beside your initials."></textarea>' +
          "</div>" +
        "</div>" +
        '<div id="np-summary" class="va-summary" aria-live="polite"></div>' +
        '<div id="np-progress" class="va-progress" hidden><div class="va-progress-bar"></div></div>' +
        '<div id="np-result" class="va-note" aria-live="polite"></div>',
      actions: [
        { label: "Cancel", kind: "btn-ghost", title: "Close this window without making anything." },
        { label: "CREATE", kind: "btn-accent", id: "np-create", title: "VEGA Annotator makes the project and copies the images in.", handler: function () { create(); } }
      ]
    });
    var createButton = backdrop.querySelector("#np-create");
    var initials = backdrop.querySelector("#va-initials");
    var name = backdrop.querySelector("#np-name");
    var result = backdrop.querySelector("#np-result");
    initials.value = savedInitials();
    VA.setLocked(createButton, true, "Choose a folder of images first.");
    wireImagesField(backdrop, "np", function (summary, path) {
      chosen = summary;
      folder = path;
      if (summary && !name.value.trim()) name.value = path.split("/").filter(Boolean).pop() || "";
      VA.setLocked(createButton, !summary, path ? "Choose a folder this computer has room for, with images in it." : "Choose a folder of images first.", "VEGA Annotator makes the project and copies the images in.");
    });

    function fail(text, field) {
      result.textContent = text;
      result.className = "va-note bad";
      if (field) field.focus();
    }

    function create() {
      if (VA.isLocked(createButton)) { fail(createButton.title, backdrop.querySelector("#np-folder-btn")); return; }
      var labels = backdrop.querySelector("#np-labels");
      var purpose = backdrop.querySelector("#np-purpose");
      var who = initials.value.trim().toUpperCase();
      if (!name.value.trim()) { fail("Give the project a name.", name); return; }
      if (!labels.value.trim()) { fail("Type at least one label.", labels); return; }
      if (!/^[A-Za-z]{1,4}$/.test(who)) { fail("Initials must be 1 to 4 letters.", initials); return; }
      if (!purpose.value.trim()) { fail("Purpose is required.", purpose); return; }
      result.className = "va-note";
      result.textContent = "Starting";
      VA.setLocked(createButton, true, "VEGA Annotator is making this project.");
      newProjectJob = "starting";
      $("new-project-status").textContent = "";
      VA.postJson("/api/projects/new", { folder: folder, name: name.value, labels: labels.value, initials: who, purpose: purpose.value.trim() })
        .then(function (data) {
          if (!data.ok) { newProjectJob = ""; VA.setLocked(createButton, !chosen, "Choose a folder of images first.", "VEGA Annotator makes the project and copies the images in."); fail(data.error); return; }
          if (data.run) { progressByRun[data.run] = { run: data.run, done: 0, total: 0, message: "Starting" }; render(); }
          followNewProject(data.job, backdrop);
        });
    }
  }

  function addImages(run) {
    var card = cardFor(run);
    var name = card ? card.project_name || run : run;
    var finishing = !!(card && partway(card));
    var folder = finishing ? card.origin_folder || "" : "";
    var ADD_TIP = "VEGA Annotator copies these images into the project.";
    var backdrop = dialog({
      title: "Add images: " + name,
      returnTo: cardButton("add-images", run),
      focus: folder ? "#ai-add" : "#ai-folder-btn",
      bodyHtml:
        '<p class="va-note">' + (finishing
          ? "This project's import stopped partway. VEGA Annotator brings in the rest of the same folder and skips the images already here."
          : "VEGA Annotator copies the images in as new frames, after the ones already here. Your folder stays as it is.") + "</p>" +
        imagesFieldHtml("ai") +
        '<div class="launcher-prompt-row"><div class="launcher-prompt-ini">' +
          '<label class="field-label" for="va-initials" title="Type your initials, 1 to 4 letters. They sign the project\'s log for these images.">Initials <span class="req-star">*</span></label>' +
          '<input id="va-initials" class="field-input mono" type="text" maxlength="4" autocomplete="off" title="Type your initials, 1 to 4 letters. They sign the project\'s log for these images.">' +
        "</div></div>" +
        '<div id="ai-summary" class="va-summary" aria-live="polite"></div>' +
        '<div id="ai-progress" class="va-progress" hidden><div class="va-progress-bar"></div></div>' +
        '<div id="ai-result" class="va-note" aria-live="polite"></div>',
      actions: [
        { label: "Cancel", kind: "btn-ghost", title: "Close this window without adding anything." },
        { label: "ADD IMAGES", kind: "btn-accent", id: "ai-add", title: ADD_TIP, handler: function () { start(); } }
      ]
    });
    var addButton = backdrop.querySelector("#ai-add");
    var result = backdrop.querySelector("#ai-result");
    var initials = backdrop.querySelector("#va-initials");
    initials.value = savedInitials();
    VA.setLocked(addButton, true, "Choose a folder of images first.");
    if (folder) {
      // A stopped import is finished from the folder the project was made from.
      var pathLine = backdrop.querySelector("#ai-folder");
      pathLine.textContent = folder;
      pathLine.classList.remove("va-path-empty");
      backdrop.querySelector("#ai-summary").textContent = "This is the folder the project was made from. If its drive is now under another name, CHOOSE FOLDER picks it there.";
      VA.setLocked(addButton, false, "", ADD_TIP);
    }
    wireImagesField(backdrop, "ai", function (summary, path) {
      folder = path;
      VA.setLocked(addButton, !summary, path ? "Choose a folder this computer has room for, with images in it." : "Choose a folder of images first.", ADD_TIP);
    });

    function start() {
      var who = initials.value.trim().toUpperCase();
      if (VA.isLocked(addButton)) { result.textContent = addButton.title; result.className = "va-note bad"; return; }
      if (!/^[A-Za-z]{1,4}$/.test(who)) { result.textContent = "Initials must be 1 to 4 letters."; result.className = "va-note bad"; initials.focus(); return; }
      VA.setLocked(addButton, true, "VEGA Annotator is adding the images.");
      result.className = "va-note";
      result.textContent = "Starting";
      progressByRun[run] = { done: 0, total: 0, message: "Starting" };
      render();
      VA.postJson("/api/projects/" + encodeURIComponent(run) + "/add-images", { folder: folder, initials: who }).then(function (data) {
        if (!data.ok) {
          delete progressByRun[run];
          render();
          VA.setLocked(addButton, false, "", ADD_TIP);
          result.textContent = data.error;
          result.className = "va-note bad";
          return;
        }
        var bar = backdrop.querySelector("#ai-progress");
        VA.followJob(data.job, function (job) {
          progressByRun[run] = job;
          render();
          if (backdrop.parentNode) followProgress(job, bar, result);
        }).then(function (job) {
          delete progressByRun[run];
          bar.hidden = true;
          var report = job.result || {};
          var failed = job.status === "failed";
          var text = failed ? job.error : importedText(report, "", name) + " The annotator shows them the next time it opens.";
          if (backdrop.parentNode) {
            result.innerHTML = esc(text) + (failed ? "" : VA.skippedHtml(report));
            result.className = "va-note va-selectable " + (failed ? "bad" : "good");
            if (failed) VA.setLocked(addButton, false, "", ADD_TIP);
            else {
              backdrop.querySelector("#ai-summary").hidden = true;
              finishDialog(backdrop, addButton, "Close this window. The images are in the project.");
            }
          } else {
            VA.toast(text, failed ? "err" : "ok");
          }
          lastListHtml = "";
          refresh();
        });
      });
    }
  }

  function showLabels(run) {
    var card = cardFor(run);
    var backdrop = dialog({
      title: "Labels: " + (card ? card.project_name || run : run),
      returnTo: cardButton("labels", run),
      focus: ".va-label-new",
      bodyHtml: '<div id="labels-dialog-editor"></div>',
      actions: [{ label: "Close", kind: "btn-ghost", title: "Close this window. Every change is already saved." }]
    });
    VA.labelsEditor(backdrop.querySelector("#labels-dialog-editor"), run, {
      buttonClass: "btn-sm va-mini", liveClass: "btn-accent-outline va-mini", addClass: "btn-accent-outline", inputClass: "field-input",
      onChange: function () { lastListHtml = ""; refresh(); }
    });
  }

  function exportForVega(run) {
    var card = cardFor(run);
    var destination = "";
    var originals = null;
    var backdrop = dialog({
      title: "Export for VEGA: " + (card ? card.project_name || run : run),
      returnTo: cardButton("export-for-vega", run),
      focus: "#ex-folder-btn",
      bodyHtml:
        '<p class="va-note">VEGA Annotator writes one new folder there, named after the project and the time, holding every exported frame with its masks and labels. VEGA trains from that folder.</p>' +
        '<label class="field-label" for="ex-folder-btn" title="Pick where the new folder goes: a USB drive, a shared drive, or any folder.">Destination <span class="req-star">*</span></label>' +
        '<div class="va-field-row"><span id="ex-folder" class="va-path va-path-empty">No folder chosen</span>' +
        '<button type="button" id="ex-folder-btn" class="btn-accent-outline" title="Open the folder chooser to pick where the new folder goes.">CHOOSE FOLDER</button></div>' +
        '<div id="ex-free" class="va-summary"></div>' +
        '<label class="va-check" for="ex-originals" title="Add the original image files, as they came in. VEGA trains on the working copies, so the folder works without them.">' +
          '<input type="checkbox" id="ex-originals"> <span>Include originals</span> <span id="ex-originals-size" class="va-check-size"></span></label>' +
        '<div id="ex-progress" class="va-progress" hidden><div class="va-progress-bar"></div></div>' +
        '<div id="ex-result" class="va-note" aria-live="polite"></div>',
      actions: [
        { label: "Cancel", kind: "btn-ghost", title: "Close this window without writing anything." },
        { label: "EXPORT FOR VEGA", kind: "btn-accent", id: "ex-go", title: "VEGA Annotator writes the new folder for VEGA.", handler: function () { start(); } }
      ]
    });
    var go = backdrop.querySelector("#ex-go");
    var result = backdrop.querySelector("#ex-result");
    var free = backdrop.querySelector("#ex-free");
    VA.setLocked(go, true, "Choose where to write the folder first.");
    fetch("/api/projects/" + encodeURIComponent(run) + "/export-for-vega").then(function (response) { return response.json(); }).then(function (data) {
      if (!data.ok) { result.textContent = data.error; result.className = "va-note bad"; return; }
      originals = data.originals_bytes;
      backdrop.querySelector("#ex-originals-size").textContent = "(" + sizeText(originals) + ")";
    });
    backdrop.querySelector("#ex-folder-btn").addEventListener("click", function () {
      var before = free.textContent;
      free.textContent = "Waiting for the folder chooser.";
      free.className = "va-summary";
      VA.chooseFolder("destination").then(function (answer) {
        if (!answer.ok) { free.textContent = answer.error; free.className = "va-summary bad"; return; }
        if (answer.cancelled) { free.textContent = before === "Waiting for the folder chooser." ? "" : before; return; }
        destination = answer.folder;
        var path = backdrop.querySelector("#ex-folder");
        path.textContent = destination;
        path.classList.remove("va-path-empty");
        free.textContent = sizeText(answer.free_bytes) + " free there.";
        VA.setLocked(go, false, "", "VEGA Annotator writes the new folder for VEGA.");
      });
    });

    function start() {
      if (VA.isLocked(go)) { result.textContent = go.title; result.className = "va-note bad"; return; }
      VA.setLocked(go, true, "VEGA Annotator is writing the folder.");
      result.className = "va-note";
      result.textContent = "Starting";
      progressByRun[run] = { done: 0, total: 0, message: "Starting" };
      render();
      VA.postJson("/api/projects/" + encodeURIComponent(run) + "/export-for-vega",
        { destination: destination, include_originals: backdrop.querySelector("#ex-originals").checked }).then(function (data) {
        if (!data.ok) {
          delete progressByRun[run];
          render();
          VA.setLocked(go, false, "", "VEGA Annotator writes the new folder for VEGA.");
          result.textContent = data.error;
          result.className = "va-note bad";
          return;
        }
        var bar = backdrop.querySelector("#ex-progress");
        VA.followJob(data.job, function (job) {
          progressByRun[run] = job;
          render();
          if (backdrop.parentNode) followProgress(job, bar, result);
        }).then(function (job) {
          delete progressByRun[run];
          bar.hidden = true;
          var report = job.result || {};
          var text = job.status === "failed" ? job.error :
            "VEGA Annotator wrote " + report.folder + ": " + VA.plural(report.frames || 0, "frame", "frames") + ", " +
            VA.plural(report.masks || 0, "mask", "masks") + ", " + sizeText(report.bytes) + ". Hand that one folder to whoever trains it in VEGA.";
          if (backdrop.parentNode) {
            result.textContent = text;
            result.className = "va-note va-selectable " + (job.status === "failed" ? "bad" : "good");
            if (job.status === "failed") VA.setLocked(go, false, "", "VEGA Annotator writes the new folder for VEGA.");
            else {
              go.hidden = true;
              var done = backdrop.querySelector(".modal-actions .btn-ghost");
              done.textContent = "Close";
              done.title = "Close this window. The folder is written.";
            }
          } else {
            VA.toast(text, job.status === "failed" ? "err" : "ok");
          }
          refresh();
        });
      });
    }
  }

  function editDescription(element) {
    if (element.querySelector("textarea")) return;
    var run = element.getAttribute("data-run");
    var card = cardFor(run);
    if (!card || !canOpen(card)) { VA.toast(card ? descriptionLock(card) : "Bring the project in to edit its description.", "info"); return; }
    var area = document.createElement("textarea");
    area.className = "field-input va-desc-edit";
    area.rows = 2;
    area.value = card.description || "";
    element.innerHTML = "";
    element.appendChild(area);
    area.focus();
    var cancelled = false;
    var byKey = false;
    function redraw() {
      area.classList.remove("va-desc-edit");
      lastListHtml = "";
      refresh().then(function () {
        var again = byKey && document.querySelector('.run-card-desc[data-run="' + CSS.escape(run) + '"]');
        if (again) again.focus();
      });
    }
    area.addEventListener("blur", function () {
      if (cancelled) { redraw(); return; }
      VA.postJson("/api/projects/" + encodeURIComponent(run) + "/description", { text: area.value }).then(function (data) {
        if (!data.ok) VA.toast(data.error, "warn");
        redraw();
      });
    });
    area.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); byKey = true; area.blur(); }
      if (event.key === "Escape") { event.preventDefault(); byKey = true; cancelled = true; area.blur(); }
    });
  }

  $("runs-list").addEventListener("click", function (event) {
    var action = event.target.closest(".run-action");
    if (action) {
      var run = action.getAttribute("data-run");
      if (VA.isLocked(action)) { VA.toast(action.title, "info"); return; }
      switch (action.getAttribute("data-action")) {
        case "bring-in": bringIn(run, false); break;
        case "take-over": bringIn(run, true); break;
        case "open": openProject(run); break;
        case "put-back": putBack(run); break;
        case "remove": removeProject(run); break;
        case "add-images": addImages(run); break;
        case "labels": showLabels(run); break;
        case "export-for-vega": exportForVega(run); break;
        case "stop-annotator":
          VA.postJson("/api/annotator/stop", {}).then(function (data) {
            if (!data.ok) VA.toast(data.error, "warn");
            refresh();
          });
          break;
        default: break;
      }
      return;
    }
    var description = event.target.closest(".run-card-desc");
    if (description) { editDescription(description); return; }
    // As on the VICARIUS page, a click anywhere else on a card opens the project.
    var card = event.target.closest(".run-card");
    if (!card || event.target.closest("button, a, input, textarea")) return;
    var run2 = card.getAttribute("data-run");
    var item = cardFor(run2);
    if (item && canOpen(item)) openProject(run2);
  });

  $("runs-list").addEventListener("keydown", function (event) {
    if ((event.key !== "Enter" && event.key !== " ") || !event.target.classList.contains("run-card-desc")) return;
    event.preventDefault();
    editDescription(event.target);
  });

  $("choose-folder").addEventListener("click", function () {
    VA.postJson("/api/choose-server-folder", {}).then(function (data) {
      if (!data.ok) VA.toast(data.error, "err");
      refresh();
    });
  });

  $("new-project").addEventListener("click", newProject);

  function showSamProgress(job) {
    var bar = $("sam3-progress");
    bar.hidden = !job.total;
    if (job.total) bar.firstElementChild.style.width = Math.round(job.done * 100 / job.total) + "%";
    var note = $("sam3-result");
    note.className = "va-note";
    note.removeAttribute("data-from");
    note.textContent = job.total ? job.message + " " + Math.round(job.done * 100 / job.total) + "%" : (job.message || "Working");
  }

  function samFinished(job, doneText) {
    samBusy = false;
    $("sam3-progress").hidden = true;
    var result = $("sam3-result");
    result.removeAttribute("data-from");
    result.textContent = job.status === "done" ? doneText : job.error;
    result.className = "va-note " + (job.status === "done" ? "good" : "bad");
    return refresh();
  }

  // Follows a SAM3 job this page did not start, as after a reload.
  function followSamJob(jobId) {
    samBusy = true;
    VA.followJob(jobId, showSamProgress).then(function (job) {
      samFinished(job, job.kind === "self_test" ? "The SAM3 test finished. Run it again to see its times." : "SAM3 is installed.");
    });
  }

  function startSamJob(url) {
    samBusy = true;
    renderPanels();
    VA.postJson(url, {}).then(function (data) {
      if (!data.ok) { samBusy = false; VA.toast(data.error, "err"); return refresh(); }
      return VA.followJob(data.job, showSamProgress).then(function (job) { return samFinished(job, "SAM3 is installed."); });
    });
  }

  $("download-sam3").addEventListener("click", function () { startSamJob("/api/sam3/download"); });

  $("install-sam3").addEventListener("click", function () {
    var install = $("install-sam3");
    if (VA.isLocked(install)) { VA.toast(install.title, "info"); return; }
    startSamJob("/api/sam3/install");
  });

  $("test-sam3").addEventListener("click", function () {
    samBusy = true;
    renderPanels();
    var result = $("sam3-result");
    result.className = "va-note";
    result.removeAttribute("data-from");
    result.textContent = "Testing SAM3. The first run on a computer takes a few minutes.";
    VA.postJson("/api/self-test", {}).then(function (data) {
      if (!data.ok) { samBusy = false; result.textContent = data.error; result.className = "va-note bad"; return refresh(); }
      return VA.followJob(data.job, function () { /* The test reports only at its end. */ }).then(function (job) {
        samBusy = false;
        var report = job.result || {};
        if (job.status === "done" && report.ok) {
          result.textContent = "A frame takes " + report.encode_s + " s to prepare, and a click takes " + report.click_s + " s. The test outline matched its object.";
          result.className = "va-note good";
        } else {
          result.textContent = "SAM3 test failed: " + (report.error || job.error || ("the outline overlap was " + report.iou));
          result.className = "va-note bad";
        }
        return refresh();
      });
    });
  });

  refresh();
  setInterval(function () { if (!document.hidden) refresh(); }, REFRESH_MS);
})();
