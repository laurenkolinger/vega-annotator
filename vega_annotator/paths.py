"""Where the app keeps its files on each computer.

Everything lives in one per-user folder, so an install needs no administrator
rights and an uninstall is one folder. VEGA_ANNOTATOR_HOME overrides the folder,
which the tests use to stay out of a real install.
"""

import os
import sys
from pathlib import Path

from . import APP_NAME

HOME_ENV = "VEGA_ANNOTATOR_HOME"
SERVER_FOLDER_NAME = "VEGA_Annotator"
PROJECTS_SUBFOLDER = "projects"
SAM3_SUBFOLDER = "sam3"
INSTALL_SUBFOLDER = "install"
SAM3_MODEL_FOLDER = "models--facebook--sam3"


def app_data_dir(platform=None, environ=None):
    """Return the app's per-user folder for this operating system.

    Parameters:
        platform: a sys.platform value; defaults to the running system.
        environ: the environment mapping; defaults to os.environ.
    Returns:
        Path: the folder. It is not created here.
    """
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    override = environ.get(HOME_ENV)
    if override:
        return Path(override)
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if platform == "win32":
        base = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def projects_dir():
    """Return the folder holding this computer's working copies of projects."""
    return app_data_dir() / PROJECTS_SUBFOLDER


def sam3_home():
    """Return the folder holding the SAM3 weights and their license."""
    return app_data_dir() / SAM3_SUBFOLDER


def hf_home():
    """Return the HF_HOME the annotator runs with, inside sam3_home()."""
    return sam3_home() / "hf"


def sam3_model_dir():
    """Return the Hugging Face cache folder of the facebook/sam3 checkpoint."""
    return hf_home() / "hub" / SAM3_MODEL_FOLDER


def sam3_cache_dir():
    """Return the folder for VEGA's cached tracker weights (TCRMP_SAM3_CACHE_DIR)."""
    return sam3_home() / "tracker_cache"


def logs_dir():
    """Return the folder holding the home and annotator logs."""
    return app_data_dir() / "logs"


def settings_path():
    """Return the path of settings.json."""
    return app_data_dir() / "settings.json"


def app_root():
    """Return the folder holding the vega_annotator package and vendor/."""
    return Path(__file__).resolve().parent.parent


def vendor_scripts():
    """Return VEGA's vendored scripts/ folder."""
    return app_root() / "vendor" / "vega" / "scripts"


def annotator_src():
    """Return the vendored annotator's src/ folder, the one that holds app.py."""
    return vendor_scripts() / "TCRMPclip_combinedAnnotate" / "src"


def vega_static():
    """Return the vendored copy of VEGA's main window static files."""
    return vendor_scripts() / "pipeline_orchestrator" / "static"
