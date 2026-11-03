"""Start VEGA's annotator, unchanged, with this computer's settings.

The home screen runs this as its own process, with the environment
projects.annotator_environment() builds, the same way VEGA's main window starts
app.py. This file does what VEGA's app.py does under its __main__ guard, with three
differences for a laptop:

- It listens on 127.0.0.1 and refuses other hosts and web pages. VEGA's launch listens
  on every network interface, which suits the lab machine and not a laptop.
- It serves VEGA's fonts from local files, so the page looks the same with no internet.
- It adds the frames-left readout next to the frame counter.
"""

import argparse
import atexit
import os
import re
import signal
import sys
from urllib.parse import urlparse

from . import paths, shims

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
TRACKER_DTYPE_ENV = "VEGA_ANNOTATOR_TRACKER_DTYPE"
THREADS_ENV = "VEGA_ANNOTATOR_THREADS"
DEFAULT_PORT = 5080
GOOGLE_FONT_LINK_RE = re.compile(
    r'[ \t]*<link[^>]*(?:fonts\.googleapis\.com|fonts\.gstatic\.com)[^>]*>[ \t]*\n?')
LOCAL_PAGE_HEAD = '<link rel="stylesheet" href="/portable/fonts.css">\n'
LOCAL_PAGE_TAIL = ('<link rel="stylesheet" href="/portable/annotator_extras.css">\n'
                   '<script src="/portable/annotator_extras.js" defer></script>\n')


def localize_page(html):
    """Swap the page's Google Fonts links for local ones and add the readout's files.

    Parameters:
        html: the annotator page as VEGA renders it.
    Returns:
        str: the page with local fonts and the extras attached.
    """
    html, removed = GOOGLE_FONT_LINK_RE.subn("", html)
    if removed:
        html = html.replace("<style>", LOCAL_PAGE_HEAD + "<style>", 1)
    return html.replace("</body>", LOCAL_PAGE_TAIL + "</body>", 1)


def is_local_request(host_header, origin_header):
    """Decide whether a request comes from this computer's own pages.

    Parameters:
        host_header: the request's Host header, with or without a port.
        origin_header: the request's Origin header, or None.
    Returns:
        bool: False for another host name, which also stops DNS rebinding, and
        for a page from anywhere but this computer.
    """
    host = urlparse(f"//{host_header or ''}").hostname or ""
    if host not in LOCAL_HOSTS:
        return False
    if origin_header and origin_header != "null":
        return (urlparse(origin_header).hostname or "") in LOCAL_HOSTS
    return not origin_header


def attach_portable_layer(flask_app, static_dir):
    """Add the local-only guard, the local fonts, and the extras to VEGA's Flask app.

    Parameters:
        flask_app: VEGA's Flask application object.
        static_dir: the folder holding fonts.css and the extras.
    Returns:
        None.
    """
    from flask import abort, request, send_from_directory

    @flask_app.before_request
    def refuse_outside_requests():
        """Refuse a request from another host name or from someone else's web page."""
        if not is_local_request(request.host, request.headers.get("Origin")):
            abort(403)

    def serve_local_page(response):
        """Rewrite the annotator page, and drop VEGA's open cross-origin header."""
        response.headers.pop("Access-Control-Allow-Origin", None)
        if request.path == "/" and response.mimetype == "text/html":
            response.set_data(localize_page(response.get_data(as_text=True)))
        return response

    # Flask runs these handlers last registered first, and VEGA's flask_cors handler adds
    # its header when the app is created, so this one goes at the front to run after it.
    flask_app.after_request_funcs.setdefault(None, []).insert(0, serve_local_page)

    def portable_file(name):
        """Serve one of the app's own files to the annotator page."""
        return send_from_directory(static_dir, name)

    flask_app.add_url_rule("/portable/<path:name>", "portable_file", portable_file)


def choose_runtime_devices(torch_module, environ):
    """Fall back to the CPU when the planned device turns out to be missing.

    The home screen plans without importing PyTorch, so this is where a missing
    CUDA build or an unavailable Metal device is found.

    Parameters:
        torch_module: the torch module.
        environ: the environment mapping, updated in place.
    Returns:
        list: one sentence per fallback made, for the log.
    """
    notes = []
    for key in ("TCRMP_SAM3_DEVICE_TRACKER", "TCRMP_SAM3_DEVICE_EXEMPLAR"):
        device = environ.get(key, "cpu:0")
        missing = (device.startswith("cuda") and not torch_module.cuda.is_available()) or (
            device.startswith("mps") and not torch_module.backends.mps.is_available())
        if missing:
            environ[key] = "cpu:0"
            if key == "TCRMP_SAM3_DEVICE_TRACKER":
                environ[TRACKER_DTYPE_ENV] = "float32"
            notes.append(f"{key}: {device} is not available here, so SAM3 uses the CPU.")
    return notes


def wrap_tracker_dtype(sam_engine_module, dtype):
    """Make VEGA's engine load its tracker in the given dtype.

    VEGA's engine asks for bfloat16 outright. That took 67 s per frame on a CPU and
    float32 took 3.6 s, so on a CPU the request is replaced here and VEGA's file
    stays as it is.

    Parameters:
        sam_engine_module: VEGA's sam_engine module.
        dtype: the torch dtype to load with.
    Returns:
        None.
    """
    original = sam_engine_module._load_image_tracker
    sam_engine_module._load_image_tracker = lambda _requested: original(dtype)


def main(argv=None):
    """Run the annotator until it is stopped.

    Parameters:
        argv: the command line, without the program name.
    Returns:
        int: 0 on a normal stop.
    """
    parser = argparse.ArgumentParser(description="Start VEGA's annotator on this computer.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    shims.install()
    import torch

    for note in choose_runtime_devices(torch, os.environ):
        print(f"[vega-annotator] {note}", flush=True)
    threads = os.environ.get(THREADS_ENV)
    if threads:
        torch.set_num_threads(int(threads))

    sys.path.insert(0, str(paths.vendor_scripts()))
    sys.path.insert(0, str(paths.annotator_src()))
    import app as vega_app
    import config as vega_config
    import sam_engine

    dtype_name = os.environ.get(TRACKER_DTYPE_ENV, "bfloat16")
    wrap_tracker_dtype(sam_engine, getattr(torch, dtype_name))
    attach_portable_layer(vega_app.app, str(paths.app_root() / "vega_annotator" / "static"))

    print("Loading SAM3 model...", flush=True)
    vega_app.session["sam_engine"] = sam_engine.SAM3Engine(vega_config)
    vega_app.session["sam_engine"].warm_exemplar_async()
    signal.signal(signal.SIGTERM, vega_app._handle_quit_signal)
    signal.signal(signal.SIGINT, vega_app._handle_quit_signal)
    atexit.register(vega_app.flush_before_exit)
    vega_app.log("SAM3 model loaded, server starting")
    print(f"Server ready on http://127.0.0.1:{args.port}", flush=True)
    vega_app.app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
