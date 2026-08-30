import pytest

from services.bulk_file_service import BulkFileService

pytestmark = pytest.mark.unit


@pytest.fixture
def service(tmp_path):
    """Service rooted at an absolute bulk dir, matching the Docker layout."""
    bulk_dir = tmp_path / "bulk_imports"
    bulk_dir.mkdir()
    return BulkFileService(base_dir="", bulk_imports_dir=str(bulk_dir))


class TestPathContainment:
    @pytest.mark.parametrize("filename", [
        "/config/config.json",
        "../config.json",
        "../../etc/passwd",
        "subdir/../../escape.txt",
    ])
    def test_escaping_filename_is_rejected(self, service, filename):
        with pytest.raises(ValueError):
            service.get_bulk_file_path(filename)

    def test_ordinary_filename_resolves_inside_base(self, service, tmp_path):
        path = service.get_bulk_file_path("bulk_import.txt")
        assert path == str(tmp_path / "bulk_imports" / "bulk_import.txt")

    def test_default_filename_still_works(self, service, tmp_path):
        assert service.get_bulk_file_path().startswith(
            str(tmp_path / "bulk_imports"))


class TestMutatingOperationsAreContained:
    def test_read_refuses_to_escape(self, service, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("plex-token")
        with pytest.raises(ValueError):
            service.read_file("../secret.txt")

    def test_write_refuses_to_escape(self, service, tmp_path):
        with pytest.raises(ValueError):
            service.write_file("owned", "../escaped.txt")
        assert not (tmp_path / "escaped.txt").exists()

    def test_delete_refuses_to_escape(self, service, tmp_path):
        victim = tmp_path / "victim.txt"
        victim.write_text("keep me")
        with pytest.raises(ValueError):
            service.delete_file("../victim.txt")
        assert victim.exists()

    def test_rename_refuses_to_escape(self, service, tmp_path):
        service.write_file("contents", "real.txt")
        with pytest.raises(ValueError):
            service.rename_file("real.txt", "../moved.txt")
        assert not (tmp_path / "moved.txt").exists()


class TestSymlinksInsideBase:
    def test_delete_removes_the_symlink_not_its_target(self, service, tmp_path):
        bulk_dir = tmp_path / "bulk_imports"
        target = bulk_dir / "real.txt"
        target.write_text("contents")
        alias = bulk_dir / "alias.txt"
        alias.symlink_to(target)

        service.delete_file("alias.txt")

        assert not alias.exists()
        assert target.exists()

    def test_symlink_escaping_base_is_rejected(self, service, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        (tmp_path / "bulk_imports" / "escape.txt").symlink_to(outside)

        with pytest.raises(ValueError):
            service.read_file("escape.txt")


class TestFileExistsIsAPredicate:
    """Runs on a scheduler thread, so it answers False rather than raising."""

    def test_escaping_filename_reports_missing(self, service, tmp_path):
        (tmp_path / "outside.txt").write_text("x")
        assert service.file_exists("../outside.txt") is False

    def test_real_file_still_found(self, service):
        service.write_file("contents", "real.txt")
        assert service.file_exists("real.txt") is True
