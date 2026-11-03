"""Tests that saving artwork to the Kometa asset directory retries a transient download failure,
the same as a direct Plex upload does.
"""

import requests

from kometa.kometa_saver import KometaSaver
from models.options import Options


class _FakeResponse:
    def __init__(self, status_code=200, content_type="image/jpeg", body=b"fake-image-bytes"):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error", response=self)

    def iter_content(self, chunk_size):
        yield self._body


def _saver(tmp_path, retry_attempts=3):
    saver = KometaSaver("Poster", "Movies")
    saver.set_artwork({"id": 123, "url": "https://theposterdb.com/api/assets/123", "source": "theposterdb"})
    saver.set_description("The Dark Knight")
    saver.set_options(Options())
    saver.dest_dir = str(tmp_path)
    saver.dest_file_name = "poster"
    saver.dest_file_ext = ".jpg"
    saver.retry_attempts = retry_attempts
    saver.retry_backoff = 1
    return saver


def test_transient_failure_that_recovers_is_saved_and_not_reported_as_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr("kometa.kometa_saver.time.sleep", lambda *a: None)
    calls = {"n": 0}

    def flaky_get(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ConnectionError("dropped")
        return _FakeResponse()

    monkeypatch.setattr("kometa.kometa_saver.requests.get", flaky_get)

    result = _saver(tmp_path).save_to_kometa()

    assert result.startswith("✅")
    assert calls["n"] == 3
    assert (tmp_path / "poster.jpg").exists()


def test_exhausted_retries_are_reported_as_an_error_with_attempt_count(tmp_path, monkeypatch):
    monkeypatch.setattr("kometa.kometa_saver.time.sleep", lambda *a: None)
    calls = {"n": 0}

    def always_503(*args, **kwargs):
        calls["n"] += 1
        return _FakeResponse(status_code=503)

    monkeypatch.setattr("kometa.kometa_saver.requests.get", always_503)

    result = _saver(tmp_path, retry_attempts=3).save_to_kometa()

    assert result.startswith("❌")
    assert calls["n"] == 3
    assert "after 3 attempt(s)" in result


def test_404_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr("kometa.kometa_saver.time.sleep", lambda *a: None)
    calls = {"n": 0}

    def always_404(*args, **kwargs):
        calls["n"] += 1
        return _FakeResponse(status_code=404)

    monkeypatch.setattr("kometa.kometa_saver.requests.get", always_404)

    result = _saver(tmp_path, retry_attempts=3).save_to_kometa()

    assert result.startswith("❌")
    assert calls["n"] == 1
    assert "attempt(s)" not in result


def test_forced_replacement_removes_stale_assets_with_other_extensions(tmp_path, monkeypatch):
    """Fork: a forced save over poster.jpg with a PNG must not leave the JPG beside it, or
    Kometa sees competing posters and the next unforced save skips as 'already exists'."""
    (tmp_path / "poster.jpg").write_bytes(b"old")
    (tmp_path / "poster.webp").write_bytes(b"older")
    source = tmp_path / "upload.png"
    source.write_bytes(b"new")
    saver = _saver(tmp_path)
    saver.set_options(Options(force=True))
    saver.set_artwork({"id": "Upload", "path": str(source), "source": "upload"})

    result = saver.save_to_kometa()

    assert result.startswith("♻️")
    assert (tmp_path / "poster.png").read_bytes() == b"new"
    assert not (tmp_path / "poster.jpg").exists()
    assert not (tmp_path / "poster.webp").exists()
    assert not (tmp_path / "poster.png.tmp").exists()
