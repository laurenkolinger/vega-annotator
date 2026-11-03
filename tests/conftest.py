"""Fixtures: every test gets its own app folder, so a real install is never touched."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    """Point the app at a temporary home folder."""
    home = tmp_path / "app_home"
    home.mkdir()
    monkeypatch.setenv("VEGA_ANNOTATOR_HOME", str(home))
    return home


@pytest.fixture
def server_folder(tmp_path):
    """Return an empty server folder."""
    folder = tmp_path / "server" / "VEGA_Annotator"
    (folder / "projects").mkdir(parents=True)
    return folder


@pytest.fixture
def source_project(tmp_path):
    """Return a small VEGA project in the mainframe's layout."""
    return helpers.make_project(tmp_path / "inprocess")


@pytest.fixture
def published(server_folder, source_project):
    """Return (server folder, run name) with the project published."""
    helpers.publish_to(server_folder, source_project)
    return server_folder, source_project.name
