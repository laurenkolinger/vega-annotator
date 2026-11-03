/* The frames-left readout beside VEGA's frame counter. VEGA's own page file stays
   unchanged, and this script reads the same queue count the page's tooltip reports. */
(function () {
  "use strict";
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
})();
