/* The home screen: project cards in the VICARIUS launcher's layout. */
(function () {
  "use strict";
  var VA = window.VA;
  var MODULE_NAME = "reef_point_seg";
  var REFRESH_MS = 4000;
  var SPECIES_CHIP_MAX = 4;
  var WINDOW_FEATURES = "popup,width=1480,height=960,scrollbars=yes,resizable=yes";
  var state = null;
  var progressByRun = {};
  var adoptedRuns = {};
  var lastListHtml = "";
  var NO_SERVER_SAM_NOTE = "Connect the server, then choose its VEGA_Annotator folder, to install SAM3.";
  var NO_COPY_SAM_NOTE = "This server folder holds no copy of SAM3 yet. The mainframe's publish tool adds it.";

  function $(id) { return document.getElementById(id); }
  function esc(value) { return VA.esc(value); }

  var STATE_TEXT = {
    on_server: { chip: "On the server", kind: "", tip: "VEGA Annotator holds this project in the server folder only. Bring it in to label it on this computer." },
    here: { chip: "On this computer", kind: "good", tip: "This computer holds the project, and no labeling work waits to go back." },
    changed: { chip: "On this computer, not put back", kind: "warn", tip: "This computer holds labeling work the server folder does not have yet." },
    put_back: { chip: "Back on the server", kind: "good", tip: "The server folder has this project's latest work. Bring it in again to keep labeling." },
    server_newer: { chip: "Newer on the server", kind: "warn", tip: "Another computer put newer work in the server folder. Bring it in to get that work." },
    held_elsewhere: { chip: "In use elsewhere", kind: "warn", tip: "Another computer holds this project. Take over only when that computer is done with it." },
    conflict: { chip: "Changed in two places", kind: "bad", tip: "This computer has work the server folder lacks, and the server copy moved on too. Put Back saves this computer's work beside the project and overwrites nothing." },
    saved_beside: { chip: "Saved beside the project", kind: "warn", tip: "This computer's work is safe in the project's conflicts folder on the server. Bringing the project in replaces this computer's copy with the server's." },
    no_server: { chip: "Server not connected", kind: "", tip: "The work is safe in the server folder. Connect the server to bring the project in again." },
    not_on_server: { chip: "Not in this server folder", kind: "warn", tip: "This server folder does not hold this project, so this computer's copy is the one to keep. Check the chosen folder, or publish the project again from the mainframe." },
    error: { chip: "Cannot be read", kind: "bad", tip: "VEGA Annotator cannot read this project's records." }
  };

  function speciesChips(card) {
    var names = card.label_names || {};
    var pairs = Object.keys(card.species || {}).map(function (code) { return [code, card.species[code]]; })
      .sort(function (a, b) { return b[1] - a[1] || (a[0] < b[0] ? -1 : 1); });
    var html = pairs.slice(0, SPECIES_CHIP_MAX).map(function (pair) {
      return '<span class="run-species-chip">' + VA.labelHtml(pair[0], names) + " <b>" + pair[1] + "</b></span>";
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

  function sizeText(bytes) {
    if (bytes == null) return "";
    if (bytes < 1e8) return Math.round(bytes / 1e6) + " MB";
    if (bytes < 1e10) return (bytes / 1e9).toFixed(2) + " GB";
    return bytes < 1e12 ? Math.round(bytes / 1e9) + " GB" : (bytes / 1e12).toFixed(1) + " TB";
  }

  function statsLine(card) {
    return '<div class="run-card-stats"><span class="run-card-stat-count">' + (card.n_masks || 0) + " masks, " + (card.n_images || 0) + " images</span>" +
      (card.size_bytes == null ? "" : '<span class="run-card-stat-count va-size" title="VEGA Annotator counts this project\'s files: frames, labels, and records. Image links count as nothing.">' + sizeText(card.size_bytes) + "</span>") +
      speciesChips(card) + reviewerChip(card) + "</div>";
  }

  function stageDots(card) {
    return (card.stages || []).map(function (stage) {
      return '<span class="step-dot step-' + esc(stage.status) + '" title="' + esc(stage.name + " is " + stage.status + ".") + '"></span>';
    }).join("");
  }

  function targets(card) {
    return (card.target_labels || []).map(function (label) { return VA.labelHtml(label.code, card.label_names); }).join(", ");
  }

  function holderText(card) {
    var holder = card.holder;
    if (holder && holder.computer === state.computer) return "";
    if (!holder) return "";
    return "In use on " + holder.computer + (holder.initials ? " by " + holder.initials : "") + " since " + String(holder.at || "").slice(0, 16).replace("T", " ");
  }

  function canOpen(card) {
    return card.state === "here" || card.state === "changed" || (card.state === "not_on_server" && card.holding);
  }

  function buttonsFor(card) {
    var button = function (action, label, kind, tip) {
      return '<button type="button" class="' + kind + ' run-action" data-action="' + action + '" data-run="' + esc(card.run) + '" title="' + esc(tip) + '">' + label + "</button>";
    };
    var bringIn = button("bring-in", "BRING IN &rarr;", "btn-accent", "Copy this project from the server folder to this computer and mark it as held here. VEGA Annotator checks every file as it copies.");
    var open = button("open", "OPEN &rarr;", "btn-accent-outline", "Prompt for initials and purpose, then open this project in its own window.");
    var putBack = button("put-back", "PUT BACK", "btn-accent", "Send this computer's work to the server folder. VEGA Annotator saves and closes the annotator first, and checks every file it sends.");
    var takeOver = button("take-over", "TAKE OVER", "btn-accent-outline", "Mark this project as held here even though another computer holds it. Work on that computer stays there and goes beside the project when it is put back.");
    if (progressByRun[card.run]) return "";
    var heldByOther = card.holder && card.holder.computer !== state.computer;
    var unsent = card.unsent_records ? putBack : "";
    switch (card.state) {
      case "on_server": case "put_back": case "server_newer": return bringIn + unsent;
      case "saved_beside": return heldByOther ? takeOver : bringIn;
      case "here": case "changed": return open + putBack;
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
    var done = card.completed_count || 0;
    var total = card.total_steps || 0;
    return total ? done + " / " + total + " complete (" + Math.round(done * 100 / total) + "%)" : "not started";
  }

  function canRemove(card) {
    if (card.where === "server") return false;
    if (card.state === "not_on_server") return !card.holding && !card.pending_files && !card.unsent_records;
    return ["put_back", "server_newer", "no_server", "saved_beside", "held_elsewhere"].indexOf(card.state) !== -1;
  }

  function removeButton(card) {
    if (!canRemove(card)) return "";
    var tip = card.state === "not_on_server" ? "Remove this computer\'s copy of the project." : "Remove this computer\'s copy of the project. The server folder keeps it.";
    return '<div class="run-card-archive"><button class="run-archive-btn run-action" data-action="remove" data-run="' + esc(card.run) + '" type="button" aria-label="Remove from this computer" title="' + tip + '">' +
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
    var words = STATE_TEXT[card.state] || STATE_TEXT.error;
    var title = esc(card.project_name || card.run);
    var when = String(card.updated_at || card.created_at || "").slice(0, 19);
    if (card.error && !card.project_name) {
      return '<div class="run-card"><div class="run-card-head"><div class="run-card-title">' + esc(card.run) + "</div></div>" +
        '<div class="run-card-error">' + esc(card.error) + "</div></div>";
    }
    var left = card.frames_left == null ? "" : card.frames_left + " frames left of " + card.frames_total;
    var holder = holderText(card);
    var editable = canOpen(card);
    var descriptionAttributes = editable ? ' role="button" tabindex="0" title="Click to edit this description."' : ' title="Bring the project in to edit its description."';
    var noDescription = editable ? "add a description..." : "no description";
    return '<div class="run-card' + (editable ? " va-openable" : "") + '" data-run="' + esc(card.run) + '">' +
      '<div class="run-card-head"><div class="run-card-title" title="This project\'s run folder is ' + esc(card.run) + '.">' + title + "</div>" +
      '<div class="run-card-when" title="This line names the run folder and when the project was last saved.">' + esc(card.run) + " &middot; " + esc(when) + "</div></div>" +
      '<div class="run-card-desc' + (card.description ? "" : " empty") + '" data-run="' + esc(card.run) + '"' + descriptionAttributes + ">" + (card.description ? esc(card.description) : noDescription) + "</div>" +
      statsLine(card) +
      '<div class="run-card-steps">' + stageDots(card) + "</div>" +
      '<div class="run-card-state"><span class="launcher-chip ' + words.kind + '" title="' + esc(words.tip) + '">' + words.chip + "</span>" +
      '<span class="run-card-frames" title="VEGA Annotator counts the frames not yet exported, out of all the project\'s frames.">' + esc(left) + "</span>" +
      stopButton(card) + "</div>" +
      '<div class="va-targets" title="VEGA took these target labels from this project\'s Choose Images step.">Target: ' + targets(card) + "</div>" +
      (holder ? '<div class="va-note">' + esc(holder) + "</div>" : "") +
      (card.conflict_folder ? '<div class="va-note">This computer\'s work is saved in ' + esc(card.conflict_folder) + "</div>" : "") +
      (card.error ? '<div class="run-card-error">' + esc(card.error) + "</div>" : "") +
      progressHtml(card.run) +
      '<div class="run-card-foot"><span class="run-card-progress" title="VEGA Annotator counts the stages this project has finished, as the VICARIUS page does.">' + progressLine(card) + "</span>" +
      '<div class="run-card-actions">' + buttonsFor(card) + "</div></div>" +
      removeButton(card) + "</div>";
  }

  function renderPanels() {
    var server = state.server;
    var chip = $("server-chip");
    chip.textContent = server.connected ? "Connected" : "Not connected";
    chip.className = "launcher-chip " + (server.connected ? "good" : "warn");
    $("server-path").textContent = server.path || "No folder chosen";
    $("server-reason").textContent = server.connected ? "" : server.reason;

    var sam = state.sam3;
    var samChip = $("sam3-chip");
    samChip.textContent = state.unsupported ? "Cannot run here" : (sam.installed ? "SAM3 ready" : "SAM3 not installed");
    samChip.className = "launcher-chip " + (state.unsupported ? "bad" : (sam.installed ? "good" : "warn"));
    $("plan-label").textContent = state.unsupported || (state.plan.label + " " + state.ram_gb + " GB of memory.");
    $("install-sam3").hidden = sam.installed || !sam.on_server || !!state.unsupported || samBusy;
    $("test-sam3").hidden = !sam.installed || !!state.unsupported || samBusy;
    // These notes follow the state. A result from Install or Test stays until the next one.
    var disk = state.disk;
    var here = state.projects.filter(function (card) { return card.where !== "server"; })
      .reduce(function (sum, card) { return sum + (card.size_bytes || 0); }, 0);
    $("disk-note").textContent = sizeText(disk.free_bytes) + " free of " + sizeText(disk.total_bytes) + " on this computer. Projects here use " + sizeText(here) + ".";
    var note = $("sam3-result");
    if (!note.textContent || note.textContent === NO_SERVER_SAM_NOTE || note.textContent === NO_COPY_SAM_NOTE) {
      note.textContent = sam.installed || sam.on_server ? "" : (server.connected ? NO_COPY_SAM_NOTE : NO_SERVER_SAM_NOTE);
    }
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
      : '<div class="activity-empty">&gt; no projects yet, connect the server folder</div>';
    // A refresh that changes nothing leaves the page alone, so keyboard focus stays put.
    if (html === lastListHtml) return;
    var selector = focusSelector(focus);
    list.innerHTML = html;
    lastListHtml = html;
    var again = selector && list.querySelector(selector);
    if (again) again.focus();
  }

  // A copy keeps running when this page reloads, so its progress comes back from the state.
  function adoptRunningJobs(running) {
    var live = {};
    running.forEach(function (job) {
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
      if (!data.ok) { delete progressByRun[run]; VA.toast(data.error, "err"); return refresh(); }
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
          VA.toast(doneText, "ok");
        }
        return refresh();
      });
    });
  }

  function spaceNote(run) {
    var card = state.projects.filter(function (item) { return item.run === run; })[0];
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

  function removeProject(run) {
    var card = state.projects.filter(function (item) { return item.run === run; })[0];
    var words = card && card.state === "not_on_server"
      ? "This server folder does not hold " + esc(run) + ". This computer's copy goes away."
      : "The server folder keeps " + esc(run) + ". This computer's copy goes away.";
    var backdrop = VA.buildModal({
      title: "Remove from this computer",
      bodyHtml: '<div class="va-note">' + words + "</div>",
      actions: [
        { label: "Cancel", kind: "btn-ghost", handler: function (node) { VA.closeModal(node); } },
        { label: "REMOVE", kind: "btn-accent", handler: function (node) {
          VA.closeModal(node);
          VA.postJson("/api/projects/" + encodeURIComponent(run) + "/remove", {}).then(function (data) {
            VA.toast(data.ok ? run + " is off this computer." : data.error, data.ok ? "ok" : "err");
            refresh();
          });
        } }
      ]
    });
    return backdrop;
  }

  function editDescription(element) {
    if (element.querySelector("textarea")) return;
    var run = element.getAttribute("data-run");
    var card = state.projects.filter(function (item) { return item.run === run; })[0];
    if (!card || !canOpen(card)) { VA.toast("Bring the project in to edit its description.", "info"); return; }
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
      switch (action.getAttribute("data-action")) {
        case "bring-in": bringIn(run, false); break;
        case "take-over": bringIn(run, true); break;
        case "open": openProject(run); break;
        case "put-back": putBack(run); break;
        case "remove": removeProject(run); break;
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
    var item = state.projects.filter(function (project) { return project.run === run2; })[0];
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

  var samBusy = false;
  function showSamProgress(job) {
    var bar = $("sam3-progress");
    bar.hidden = !job.total;
    if (job.total) bar.firstElementChild.style.width = Math.round(job.done * 100 / job.total) + "%";
    $("sam3-result").textContent = job.total ? job.message + " " + Math.round(job.done * 100 / job.total) + "%" : "Working";
  }

  $("install-sam3").addEventListener("click", function () {
    samBusy = true;
    renderPanels();
    VA.postJson("/api/sam3/install", {}).then(function (data) {
      if (!data.ok) { samBusy = false; VA.toast(data.error, "err"); return refresh(); }
      return VA.followJob(data.job, showSamProgress).then(function (job) {
        samBusy = false;
        $("sam3-progress").hidden = true;
        var result = $("sam3-result");
        result.textContent = job.status === "done" ? "SAM3 is installed." : job.error;
        result.className = "va-note " + (job.status === "done" ? "good" : "bad");
        return refresh();
      });
    });
  });

  $("test-sam3").addEventListener("click", function () {
    samBusy = true;
    renderPanels();
    var result = $("sam3-result");
    result.className = "va-note";
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
