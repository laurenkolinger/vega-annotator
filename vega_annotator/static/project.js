/* The project window: VEGA's rail and Place + Segment panel, driven by this project's records.
   A project made here from a folder of images has Labels, Add Images and Export for VEGA
   where a project from the server has Put Back. */
(function () {
  "use strict";
  var VA = window.VA;
  var RUN = document.body.getAttribute("data-run");
  var MADE_HERE = document.body.getAttribute("data-made-here") === "true";
  var REFRESH_MS = 3000;
  var WINDOW_FEATURES = "popup,width=1480,height=960,scrollbars=yes,resizable=yes";
  var CLOSED_FIRST = "Stop the annotator on this project first, so it saves its work and lets go of the project.";
  var MAINFRAME_STEPS = {
    "1": ["Step 1: Fetch Points", "VEGA on the mainframe ran this step when it made the project."],
    "2": ["Step 2: Fetch Labels", "VEGA on the mainframe ran this step when it made the project."],
    "3": ["Step 3: Choose Images", "VEGA on the mainframe ran this step when it made the project."],
    "6": ["Step 5: Train Model", "VEGA on the mainframe runs this step after the project is put back."],
    "7": ["Step 6: Evaluate Model", "VEGA on the mainframe runs this step after the project is put back."],
    "8": ["Step 7: Model Inference", "VEGA on the mainframe runs this step after the project is put back."]
  };
  var FOLDER_STEPS = {
    "1": ["Step 1: Fetch Points", "This project came from a folder of images, so it has no survey points to fetch."],
    "2": ["Step 2: Fetch Labels", "The person who made this project typed its labels. Labels, lower on the rail, adds more."],
    "3": ["Step 3: Choose Images", "This project's images came from a folder. Add Images, lower on the rail, copies in more."],
    "6": ["Step 5: Train Model", "VEGA runs this step on the mainframe, in its own training project, from the folder EXPORT FOR VEGA writes."],
    "7": ["Step 6: Evaluate Model", "VEGA runs this step on the mainframe, in its own training project, from the folder EXPORT FOR VEGA writes."],
    "8": ["Step 7: Model Inference", "VEGA runs this step on the mainframe, in its own training project, from the folder EXPORT FOR VEGA writes."]
  };
  var STATE_NOTES = {
    conflict: "This computer and the server folder both changed. Put Back saves this computer's work beside the project and overwrites nothing.",
    saved_beside: "This computer's work is saved beside the project, in its conflicts folder. The home screen can bring in the server's copy.",
    put_back: "The server folder has this project's latest work. The home screen can bring it in again.",
    server_newer: "The server folder has newer work. The home screen can bring it in.",
    held_elsewhere: "Another computer holds this project. The home screen can take it over.",
    not_on_server: "This server folder does not hold this project, so its work stays on this computer. Check the chosen folder on the home screen.",
    error: "VEGA Annotator cannot read this project's records. The home screen says why."
  };
  var STATE_WORDS = {
    conflict: "changed in two places",
    saved_beside: "saved beside the project",
    put_back: "back on the server",
    server_newer: "newer on the server",
    held_elsewhere: "in use elsewhere",
    not_on_server: "not in this server folder",
    error: "cannot be read"
  };
  var card = null;
  var serverConnected = false;
  var savedInitials = "";
  var putBackBusy = false;
  var annotatorWindow = null;
  var annotatorOpen = false;
  var labelsEditor = null;
  var addFolder = "";
  var addReady = false;
  var addBusy = false;
  var exportFolder = "";
  var exportBusy = false;

  function $(id) { return document.getElementById(id); }

  function setRailStatus(key, status) {
    var item = document.querySelector('#step-nav li[data-step="' + key + '"]');
    var text = $("status-" + key);
    if (text) text.textContent = status;
    if (item) {
      item.classList.remove("completed", "pending", "locked", "running", "error");
      if (status === "completed" || status === "pending" || status === "locked") item.classList.add(status);
    }
  }

  function renderPutBack(state, held, note, waiting) {
    var canSend = serverConnected && (held || state === "conflict" ||
      (card.unsent_records && ["put_back", "server_newer", "held_elsewhere"].indexOf(state) !== -1));
    setRailStatus("putback", !serverConnected ? "server not connected" : (STATE_WORDS[state] || waiting));
    $("putback-readout").textContent = !serverConnected ? "Connect the server to put this project back." : (note || waiting);
    $("putback-btn").disabled = putBackBusy || !canSend;
  }

  // Labels, Add Images and Export for VEGA wait while the annotator holds the project.
  function renderFolderProject() {
    var labels = card.target_labels || [];
    var exported = card.frames_exported || 0;
    setRailStatus("labels", VA.plural(labels.length, "label", "labels"));
    setRailStatus("addimages", VA.plural(card.frames_total || 0, "frame", "frames"));
    setRailStatus("export", exported ? exported + " exported" : "nothing exported yet");
    $("export-readout").innerHTML = "<strong>" + exported + "</strong>&nbsp;of&nbsp;" + (card.frames_total || 0) +
      " frames exported. EXPORT FOR VEGA carries these, with their masks and labels.";
    var addWhy = addBusy ? "VEGA Annotator is adding images." : (annotatorOpen ? CLOSED_FIRST : (!addFolder ? "Choose a folder of images first." : (!addReady ? "Choose a folder this computer has room for, with images in it." : "")));
    VA.setLocked($("add-images-btn"), !!addWhy, addWhy, "VEGA Annotator copies the images in this folder into the project.");
    VA.setLocked($("add-folder-btn"), addBusy, "VEGA Annotator is adding images.", "Open the folder chooser. VEGA Annotator then looks through the folder and says what it found.");
    var exportWhy = exportBusy ? "VEGA Annotator is writing the folder." : (annotatorOpen ? CLOSED_FIRST :
      (!exported ? "Export a batch in the annotator first. EXPORT FOR VEGA hands VEGA the frames the annotator has exported." :
        (!exportFolder ? "Choose where to write the folder first." : "")));
    VA.setLocked($("export-btn"), !!exportWhy, exportWhy, "VEGA Annotator writes the new folder for VEGA.");
    if (labelsEditor && $("panel-labels").classList.contains("active")) labelsEditor.reload();
  }

  function renderCard() {
    $("topbar-project-name").textContent = card.project_name || RUN;
    document.title = (card.project_name || RUN) + " | VEGA Annotator";
    Object.keys(card.steps || {}).forEach(function (key) { setRailStatus(key, card.steps[key]); });
    $("frames-readout").innerHTML = "<strong>" + card.frames_left + "</strong>&nbsp;frames left,&nbsp;" +
      card.frames_exported + " exported, " + card.frames_scrapped + " scrapped, of " + card.frames_total;
    var pending = card.pending_files || 0;
    var waiting = pending ? pending + (pending === 1 ? " changed file" : " changed files") + " to send" : "no labeling work to send";
    var state = card.state;
    var held = state === "here" || state === "changed";
    var canOpen = held || state === "made_here" || (state === "not_on_server" && card.holding);
    var note = STATE_NOTES[state] || "";
    if (MADE_HERE) renderFolderProject();
    else renderPutBack(state, held, note, waiting);
    $("s4test-run-btn").disabled = !canOpen;
    $("s4test-state").textContent = canOpen ? "" : note;
    $("s4test-state").style.display = canOpen ? "none" : "block";
  }

  function showNote(text) {
    var line = $("s4test-note");
    line.textContent = text;
    line.className = "va-result bad";
  }

  function renderAnnotator(status) {
    var mine = status.run === RUN;
    var service = $("s4test-service");
    var health = $("s4test-health");
    var text = $("s4test-health-text");
    var log = $("s4test-log");
    var label = "combined annotator";
    annotatorOpen = mine && (status.state === "loading" || status.state === "ready");
    service.style.display = mine && status.state !== "stopped" ? "block" : "none";
    log.style.display = "none";
    if (mine && status.state === "loading") {
      health.className = "health-dot checking";
      text.textContent = "Loading SAM3...";
      label = "starting";
    } else if (mine && status.state === "ready") {
      health.className = "health-dot healthy";
      text.textContent = "Annotator running";
      label = "open";
    } else if (mine && status.state === "failed") {
      health.className = "health-dot unhealthy";
      text.textContent = "The annotator stopped.";
      log.textContent = status.log || "";
      log.style.display = log.textContent ? "block" : "none";
      label = "error";
    }
    setRailStatus("step4test", label);
  }

  function refresh() {
    return fetch("/api/state").then(function (response) { return response.json(); }).then(function (state) {
      card = state.projects.filter(function (item) { return item.run === RUN; })[0] || null;
      serverConnected = !!state.server.connected;
      savedInitials = state.settings.initials;
      renderAnnotator(state.annotator);
      if (card) renderCard();
      if (MADE_HERE && !$("add-initials").value && savedInitials) $("add-initials").value = savedInitials;
    }).catch(function () { /* The next refresh tries again. */ });
  }

  var PANELS = { putback: "panel-putback", labels: "panel-labels", addimages: "panel-addimages", export: "panel-export" };

  function showStep(key) {
    document.querySelectorAll("#step-nav li[data-step]").forEach(function (item) {
      item.classList.toggle("active", item.getAttribute("data-step") === key);
    });
    document.querySelectorAll(".step-panel").forEach(function (panel) { panel.classList.remove("active"); });
    var steps = MADE_HERE ? FOLDER_STEPS : MAINFRAME_STEPS;
    if (steps[key]) {
      $("mainframe-title").textContent = steps[key][0];
      $("mainframe-note").textContent = steps[key][1];
      $("panel-mainframe").classList.add("active");
    } else {
      $(PANELS[key] || "panel-step4test").classList.add("active");
    }
    if (key === "labels" && labelsEditor) labelsEditor.reload();
  }

  document.querySelectorAll("#step-nav li[data-step]").forEach(function (item) {
    item.addEventListener("click", function () { showStep(item.getAttribute("data-step")); });
  });

  $("s4test-run-btn").addEventListener("click", function () {
    showNote("");
    var opened = window.open("/loading/" + encodeURIComponent(RUN), "reefpointseg_annotator", WINDOW_FEATURES);
    annotatorWindow = opened;
    if (!opened) showNote("Allow popups for this page, then click Open Annotator again.");
    var targetOnly = $("s4test-target-only");
    var reference = $("s4test-reference");
    VA.postJson("/api/projects/" + encodeURIComponent(RUN) + "/start-annotator", {
      batch_size: $("s4test-batch-size").value,
      target_only: targetOnly ? targetOnly.checked : false,
      reference: reference ? reference.checked : false
    }).then(function (data) {
      if (!data.ok && opened) opened.close();
      if (!data.ok) showNote(data.error);
      refresh();
    });
  });

  function closeAnnotatorWindow() {
    if (annotatorWindow && !annotatorWindow.closed) annotatorWindow.close();
    annotatorWindow = null;
  }

  $("s4test-stop-btn").addEventListener("click", function () {
    VA.postJson("/api/annotator/stop", {}).then(function (data) {
      if (data.ok) {
        showNote("");
        closeAnnotatorWindow();
      } else {
        showNote(data.error);
      }
      return refresh();
    });
  });

  function openFolder() {
    VA.postJson("/api/projects/" + encodeURIComponent(RUN) + "/open-folder", {}).then(function (data) {
      if (!data.ok) showNote(data.error);
    });
  }
  $("s4test-folder-btn").addEventListener("click", openFolder);
  $("open-project-folder").addEventListener("click", openFolder);

  // Follows one job on this project, drawing its bar and its line until it ends.
  function followInto(jobId, bar, line) {
    return VA.followJob(jobId, function (job) {
      if (!job.total) { line.textContent = job.message || "Starting"; return; }
      bar.style.display = "block";
      bar.firstElementChild.style.width = Math.round(job.done * 100 / job.total) + "%";
      line.textContent = job.message + " " + Math.round(job.done * 100 / job.total) + "%";
    }).then(function (job) {
      bar.style.display = "none";
      return job;
    });
  }

  function setLine(line, text, kind) {
    line.textContent = text;
    line.className = "va-result" + (kind ? " " + kind : "");
  }

  function wireFolderProject() {
    labelsEditor = VA.labelsEditor($("labels-editor"), RUN, {
      buttonClass: "btn btn-outline btn-sm va-mini", liveClass: "btn btn-outline btn-sm va-mini va-live",
      addClass: "btn btn-outline", onChange: function () { refresh(); }
    });

    $("add-folder-btn").addEventListener("click", function () {
      if (VA.isLocked(this)) { setLine($("add-summary"), this.title, "bad"); return; }
      var summary = $("add-summary");
      // A cancelled chooser leaves the folder chosen before, so its summary comes back.
      var before = { html: summary.innerHTML, cls: summary.className };
      VA.pickImages(function (step) {
        if (step.phase === "choosing") { setLine(summary, "Waiting for the folder chooser."); return; }
        if (step.phase === "cancelled") { summary.className = before.cls; summary.innerHTML = before.html; return; }
        if (step.folder) { $("add-folder").textContent = step.folder; $("add-folder").classList.remove("va-path-empty"); addFolder = step.folder; }
        if (step.phase === "scanning") { addReady = false; setLine(summary, "Looking through the folder."); renderCard(); return; }
        if (step.phase === "failed") { addReady = false; setLine(summary, step.error, "bad"); renderCard(); return; }
        var said = VA.scanSentence(step.summary);
        addReady = said.ok;
        summary.className = "va-result " + (said.ok ? "good" : "bad");
        summary.innerHTML = VA.esc(said.text) + VA.skippedHtml(step.summary);
        renderCard();
      });
    });

    $("add-images-btn").addEventListener("click", function () {
      var result = $("add-result");
      if (VA.isLocked(this)) { setLine(result, this.title, "bad"); return; }
      var initials = $("add-initials").value.trim().toUpperCase();
      if (!/^[A-Za-z]{1,4}$/.test(initials)) { setLine(result, "Initials must be 1 to 4 letters.", "bad"); $("add-initials").focus(); return; }
      addBusy = true;
      renderCard();
      setLine(result, "Starting");
      VA.postJson("/api/projects/" + encodeURIComponent(RUN) + "/add-images", { folder: addFolder, initials: initials }).then(function (data) {
        if (!data.ok) { addBusy = false; setLine(result, data.error, "bad"); return refresh(); }
        return followInto(data.job, $("add-progress"), result).then(function (job) {
          addBusy = false;
          var report = job.result || {};
          var skipped = report.skipped_count || 0;
          if (job.status === "failed") setLine(result, job.error, "bad");
          else {
            setLine(result, "", "good");
            result.innerHTML = VA.esc(VA.plural(report.added || 0, "image", "images") + " added" + (skipped ? ", " + VA.plural(skipped, "file", "files") + " skipped." : ".") +
              " The annotator shows them the next time it opens.") + VA.skippedHtml(report);
          }
          addFolder = "";
          addReady = false;
          $("add-folder").textContent = "No folder chosen";
          $("add-folder").classList.add("va-path-empty");
          $("add-summary").textContent = "";
          return refresh();
        });
      });
    });

    fetch("/api/projects/" + encodeURIComponent(RUN) + "/export-for-vega").then(function (response) { return response.json(); }).then(function (data) {
      if (data.ok) $("export-originals-size").textContent = "(" + VA.sizeText(data.originals_bytes) + ")";
    });

    $("export-folder-btn").addEventListener("click", function () {
      var free = $("export-free");
      var before = free.textContent;
      setLine($("export-result"), "");
      free.textContent = "Waiting for the folder chooser.";
      VA.chooseFolder("destination").then(function (answer) {
        if (!answer.ok) { free.textContent = before; setLine($("export-result"), answer.error, "bad"); return; }
        if (answer.cancelled) { free.textContent = before; return; }
        exportFolder = answer.folder;
        $("export-folder").textContent = exportFolder;
        $("export-folder").classList.remove("va-path-empty");
        free.textContent = VA.sizeText(answer.free_bytes) + " free there.";
        renderCard();
      });
    });

    $("export-btn").addEventListener("click", function () {
      var result = $("export-result");
      if (VA.isLocked(this)) { setLine(result, this.title, "bad"); return; }
      exportBusy = true;
      renderCard();
      setLine(result, "Starting");
      VA.postJson("/api/projects/" + encodeURIComponent(RUN) + "/export-for-vega",
        { destination: exportFolder, include_originals: $("export-originals").checked }).then(function (data) {
        if (!data.ok) { exportBusy = false; setLine(result, data.error, "bad"); return refresh(); }
        return followInto(data.job, $("export-progress"), result).then(function (job) {
          exportBusy = false;
          var report = job.result || {};
          if (job.status === "failed") setLine(result, job.error, "bad");
          else setLine(result, "VEGA Annotator wrote " + report.folder + ": " + VA.plural(report.frames || 0, "frame", "frames") + ", " +
            VA.plural(report.masks || 0, "mask", "masks") + ", " + VA.sizeText(report.bytes) + ". Hand that one folder to whoever trains it in VEGA.", "good");
          return refresh();
        });
      });
    });
  }

  function wirePutBack() {
    $("putback-btn").addEventListener("click", function () {
      var bar = $("putback-progress");
      var result = $("putback-result");
      putBackBusy = true;
      $("putback-btn").disabled = true;
      result.className = "va-result";
      result.textContent = "Saving and closing the annotator";
      closeAnnotatorWindow();
      VA.postJson("/api/projects/" + encodeURIComponent(RUN) + "/put-back", { initials: savedInitials }).then(function (data) {
        if (!data.ok) {
          putBackBusy = false;
          result.className = "va-result bad";
          result.textContent = data.error;
          return refresh();
        }
        return VA.followJob(data.job, function (job) {
          if (job.total) {
            bar.style.display = "block";
            bar.firstElementChild.style.width = Math.round(job.done * 100 / job.total) + "%";
            result.textContent = job.message + " " + Math.round(job.done * 100 / job.total) + "%";
          }
        }).then(function (job) {
          putBackBusy = false;
          bar.style.display = "none";
          var report = job.result || {};
          if (job.status === "failed") {
            result.className = "va-result bad";
            result.textContent = job.error;
          } else if (report.conflict) {
            result.className = "va-result warn";
            result.textContent = "This computer's work is saved beside the project, and nothing was overwritten. " + report.reason;
          } else if (!report.changed) {
            result.className = "va-result good";
            result.textContent = "Nothing was waiting to send, and this computer no longer holds the project.";
          } else {
            result.className = "va-result good";
            result.textContent = report.changed + (report.changed === 1 ? " file" : " files") + " sent. The server folder has this project's latest work.";
          }
          return refresh();
        });
      });
    });
  }

  if (MADE_HERE) wireFolderProject();
  else wirePutBack();
  refresh();
  setInterval(function () { if (!document.hidden) refresh(); }, REFRESH_MS);
})();
