/* The app's own additions to VEGA's annotator page. VEGA's page file stays unchanged.

   - The frames-left readout beside VEGA's frame counter. It reads the same queue count
     the page's tooltip reports.
   - On a project with its own labels, which annotator_boot.py flags before this file
     loads (window.VEGA_ANNOTATOR.labelSet is "custom"), no reef shortcuts. The
     stylesheet hides the Sponge, Macroalgae, Gorgonian and Turf chips and sets every
     label name upright, because VEGA italicizes a two-word name as a species. This
     script stops the label popup from taking s, m, g and t, the keys that file a mask
     under those four categories. Outside the popup the same letters keep VEGA's other
     shortcuts: S finds look-alikes, M merges, G and T show the guide points. A custom
     label's code is the label itself, so the code chip that would repeat a name beside
     it is hidden too. */
(function () {
  "use strict";
  var flags = window.VEGA_ANNOTATOR || {};
  var REEF_KEYS = { s: true, m: true, g: true, t: true };
  var CATEGORY_HINT = ", s / m / g / t for a category";

  // VEGA's keydown handler sends letters to the label popup while a mask or a scan
  // candidate waits for its label, and the open popup shows that same state.
  function popupTakesLetters() {
    try {
      if (typeof pendingAddMaskIdx === "number" && pendingAddMaskIdx >= 0) return true;
      if (typeof pendingScanCandidateIdx === "number" && pendingScanCandidateIdx >= 0) return true;
    } catch (error) {
      /* VEGA renamed its state: the popup itself still answers below. */
    }
    var popup = document.getElementById("cat-popup");
    return !!popup && popup.classList.contains("visible");
  }

  // VEGA leaves keys alone while a person types in a field, and so does this script.
  function typingInto(target) {
    if (!target) return false;
    var tag = target.tagName;
    var type = String(target.type || "").toLowerCase();
    return (tag === "INPUT" && type !== "range" && type !== "checkbox" && type !== "radio") ||
      tag === "TEXTAREA" || tag === "SELECT" || !!target.isContentEditable;
  }

  // Marks each code chip that repeats the name just before it, in the label popup, the
  // quick-add bar and the mask list.
  function markRepeatedCodes() {
    document.querySelectorAll(".cat-code, .qa-code, .m-species").forEach(function (code) {
      var name = code.previousElementSibling;
      code.classList.toggle("va-repeated-code", !!name && name.textContent.trim() === code.textContent.trim());
    });
  }

  function dropReefShortcuts() {
    document.documentElement.classList.add("va-custom-labels");
    // The capture phase on window runs before VEGA's own listener on document.
    window.addEventListener("keydown", function (event) {
      if (!REEF_KEYS[String(event.key || "").toLowerCase()]) return;
      if (typingInto(event.target) || !popupTakesLetters()) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    document.querySelectorAll("#kbd-help .kh-desc").forEach(function (line) {
      if (line.textContent.indexOf(CATEGORY_HINT) !== -1) line.textContent = line.textContent.replace(CATEGORY_HINT, "");
    });
    markRepeatedCodes();
    var masks = document.getElementById("mask-list");
    if (masks) new MutationObserver(markRepeatedCodes).observe(masks, { childList: true });
  }

  function addFramesLeftReadout() {
    var counter = document.getElementById("img-counter");
    var nextButton = document.getElementById("btn-next-frame");
    if (!counter || !nextButton) return;

    var readout = document.createElement("span");
    readout.id = "va-frames-left";
    readout.title = "VEGA counts the frames in this project that are not exported yet.";
    nextButton.parentNode.insertBefore(readout, nextButton.nextSibling);

    var pending = false;
    function refresh() {
      if (pending) return;
      pending = true;
      fetch("/api/status").then(function (response) {
        return response.ok ? response.json() : null;
      }).then(function (status) {
        if (status && typeof status.review_total === "number") {
          readout.textContent = status.review_total + " left";
        }
      }).catch(function () {
        /* The next frame change tries again; the page never depends on this count. */
      }).finally(function () {
        pending = false;
      });
    }

    new MutationObserver(refresh).observe(counter, { childList: true, characterData: true, subtree: true });
    refresh();
  }

  if (flags.labelSet === "custom") dropReefShortcuts();
  addFramesLeftReadout();
})();
