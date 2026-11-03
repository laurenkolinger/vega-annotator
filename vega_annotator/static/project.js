/* The project window: VEGA's rail and Place + Segment panel, driven by this project's records. */
(function () {
  "use strict";
  var VA = window.VA;
  var RUN = document.body.getAttribute("data-run");
  var REFRESH_MS = 3000;
  var WINDOW_FEATURES = "popup,width=1480,height=960,scrollbars=yes,resizable=yes";
  var MAINFRAME_STEPS = {
    "1": ["Step 1: Fetch Points", "VEGA on the mainframe ran this step when it made the project."],
    "2": ["Step 2: Fetch Labels", "VEGA on the mainframe ran this step when it made the project."],
    "3": ["Step 3: Choose Images", "VEGA on the mainframe ran this step when it made the project."],
    "6": ["Step 5: Train Model", "VEGA on the mainframe runs this step after the project is put back."],
    "7": ["Step 6: Evaluate Model", "VEGA on the mainframe runs this step after the project is put back."],
    "8": ["Step 7: Model Inference", "VEGA on the mainframe runs this step after the project is put back."]
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
    var canOpen = held || (state === "not_on_server" && card.holding);
    var canSend = serverConnected && (held || state === "conflict" ||
      (card.unsent_records && ["put_back", "server_newer", "held_elsewhere"].indexOf(state) !== -1));
    var note = STATE_NOTES[state] || "";
    setRailStatus("putback", !serverConnected ? "server not connected" : (STATE_WORDS[state] || waiting));
    $("putback-readout").textContent = !serverConnected ? "Connect the server to put this project back." : (note || waiting);
    $("putback-btn").disabled = putBackBusy || !canSend;
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
      if (card) renderCard();
      renderAnnotator(state.annotator);
    }).catch(function () { /* The next refresh tries again. */ });
  }

  function showStep(key) {
    document.querySelectorAll("#step-nav li[data-step]").forEach(function (item) {
      item.classList.toggle("active", item.getAttribute("data-step") === key);
    });
    document.querySelectorAll(".step-panel").forEach(function (panel) { panel.classList.remove("active"); });
    if (MAINFRAME_STEPS[key]) {
      $("mainframe-title").textContent = MAINFRAME_STEPS[key][0];
      $("mainframe-note").textContent = MAINFRAME_STEPS[key][1];
      $("panel-mainframe").classList.add("active");
    } else {
      $(key === "putback" ? "panel-putback" : "panel-step4test").classList.add("active");
    }
  }

  document.querySelectorAll("#step-nav li[data-step]").forEach(function (item) {
    item.addEventListener("click", function () { showStep(item.getAttribute("data-step")); });
  });

  $("s4test-run-btn").addEventListener("click", function () {
    showNote("");
    var opened = window.open("/loading/" + encodeURIComponent(RUN), "reefpointseg_annotator", WINDOW_FEATURES);
    annotatorWindow = opened;
    if (!opened) showNote("Allow popups for this page, then click Open Annotator again.");
    VA.postJson("/api/projects/" + encodeURIComponent(RUN) + "/start-annotator", {
      batch_size: $("s4test-batch-size").value,
      target_only: $("s4test-target-only").checked,
      reference: $("s4test-reference").checked
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

  refresh();
  setInterval(function () { if (!document.hidden) refresh(); }, REFRESH_MS);
})();
