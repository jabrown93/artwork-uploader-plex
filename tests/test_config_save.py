"""Saving config.json must not be able to destroy it (core/config.py).

config.json holds the Plex token, the webhook token, the OIDC client secret, the basic
auth password hash and every schedule. It used to be written over the top of the live
file, so a process stopped part way through a save left it truncated and the next load
could not parse it, and all of that had to be entered again by hand. It is now written
to a temporary file alongside it and moved into place, the same shape as the run history
and the Kometa asset writer use.
"""

import errno
import json
import os
from unittest.mock import patch

import pytest

from core.config import Config
from core.exceptions import ConfigSaveError, ConfigCreationError


@pytest.fixture
def config(tmp_path):
    config = Config(str(tmp_path / "config.json"))
    config.load()
    return config


@pytest.mark.unit
def test_save_writes_the_configuration(config):
    config.token = "a-plex-token"
    config.oidc_client_secret = "an-oidc-secret"
    config.save()

    with open(config.path, encoding="utf-8") as saved:
        saved_config = json.load(saved)

    assert saved_config["token"] == "a-plex-token"
    assert saved_config["oidc_client_secret"] == "an-oidc-secret"


@pytest.mark.unit
def test_saving_leaves_no_temporary_file_behind(config):
    config.save()

    assert os.path.isfile(config.path)
    assert not os.path.exists(f"{config.path}.tmp")


@pytest.mark.unit
def test_save_keeps_the_permissions_the_file_already_had(config):
    """An administrator who restricted config.json to its owner keeps that after a save."""
    config.save()
    os.chmod(config.path, 0o600)

    config.token = "a-plex-token"
    config.save()

    assert os.stat(config.path).st_mode & 0o777 == 0o600


@pytest.mark.unit
def test_a_failed_save_leaves_the_existing_configuration_intact(config):
    """A write that dies part way cannot take the credentials already on disk with it."""
    config.token = "a-plex-token"
    config.auth_password_hash = "a-password-hash"
    config.save()

    with patch("core.config.json.dump", side_effect=OSError("no space left on device")):
        with pytest.raises(ConfigSaveError):
            config.token = "a-newer-token"
            config.save()

    reloaded = Config(config.path)
    reloaded.load()

    assert reloaded.token == "a-plex-token"
    assert reloaded.auth_password_hash == "a-password-hash"
    assert not os.path.exists(f"{config.path}.tmp")


@pytest.mark.unit
def test_save_falls_back_to_in_place_write_when_replace_is_ebusy(config):
    """Docker's documented layout bind-mounts the individual config.json file
    (docker-compose.example.yml: `./config.json:/config/config.json`), making the target itself
    a mount point. The kernel refuses to rename anything over an active mount point (EBUSY),
    even though writing into the existing inode is fine, so os.replace must fall back to an
    in-place write there instead of failing the save."""
    config.token = "a-plex-token"
    config.save()

    with patch("core.config.os.replace", side_effect=OSError(errno.EBUSY, "Device or resource busy")):
        config.token = "a-newer-token"
        config.save()

    reloaded = Config(config.path)
    reloaded.load()

    assert reloaded.token == "a-newer-token"
    assert not os.path.exists(f"{config.path}.tmp")


@pytest.mark.unit
def test_save_still_raises_for_non_ebusy_replace_failures(config):
    """Only EBUSY gets the bind-mount fallback; any other os.replace failure still surfaces as
    a ConfigSaveError rather than silently writing in place."""
    config.token = "a-plex-token"
    config.save()

    with patch("core.config.os.replace", side_effect=OSError(errno.EACCES, "Permission denied")):
        with pytest.raises(ConfigSaveError):
            config.token = "a-newer-token"
            config.save()

    reloaded = Config(config.path)
    reloaded.load()

    assert reloaded.token == "a-plex-token"
    assert not os.path.exists(f"{config.path}.tmp")


@pytest.mark.unit
def test_the_first_load_creates_a_readable_file(tmp_path):
    """The default config is written the same way, with nothing there to replace."""
    config = Config(str(tmp_path / "config.json"))
    config.load()

    with open(config.path, encoding="utf-8") as created:
        json.load(created)

    assert not os.path.exists(f"{config.path}.tmp")


@pytest.mark.unit
def test_a_failed_creation_leaves_no_temporary_file_behind(tmp_path):
    """The first load creates the file, and that write goes the same way."""
    config = Config(str(tmp_path / "config.json"))

    with patch("core.config.json.dump", side_effect=OSError("no space left on device")):
        with pytest.raises(ConfigCreationError):
            config.create()

    assert not os.path.exists(config.path)
    assert not os.path.exists(f"{config.path}.tmp")


@pytest.mark.unit
def test_a_fresh_config_follows_the_active_path(tmp_path, monkeypatch):
    """Fork: scrapers and the upload processor re-read the config themselves; under
    CONFIG_PATH/--config they must read the file the app runs with, not the default."""
    from core import globals
    active = Config(str(tmp_path / "custom" / "config.json"))
    monkeypatch.setattr(globals, "config", active)

    assert Config.at_active_path().path == active.path

    monkeypatch.setattr(globals, "config", None)
    assert Config.at_active_path().path == Config().path


@pytest.mark.unit
def test_scrapers_and_processor_read_the_active_config(tmp_path, monkeypatch):
    from core import globals
    from scrapers.theposterdb_scraper import ThePosterDBScraper
    from scrapers.mediux_scraper import MediuxScraper
    from processors.upload_processor import UploadProcessor
    from models.callbacks import ProcessingCallbacks

    path = tmp_path / "custom" / "config.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"tpdb_filters": ["background"], "mediux_filters": ["title_card"]}))
    active = Config(str(path))
    active.load()
    monkeypatch.setattr(globals, "config", active)
    monkeypatch.setattr(globals, "arr", None)

    assert ThePosterDBScraper("https://theposterdb.com/set/1", ProcessingCallbacks()).config.tpdb_filters == ["background"]
    assert MediuxScraper("https://mediux.pro/sets/1", ProcessingCallbacks()).config.mediux_filters == ["title_card"]
    assert UploadProcessor(object()).config.tpdb_filters == ["background"]
