from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_container_uses_init_then_drops_privileges():
    dockerfile = (ROOT / "Dockerfile").read_text()
    entrypoint = (ROOT / "entrypoint.sh").read_text()

    assert (
        'ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh", '
        '"python", "/app/src/artwork_uploader.py"]' in dockerfile
    )
    assert 'CMD ["--debug"]' in dockerfile
    assert "USER artwork" not in dockerfile
    assert 'exec gosu "$PUID:$PGID" "$@"' in entrypoint


def test_published_compose_example_uses_runtime_mount_paths():
    readme = (ROOT / "README.md").read_text()
    marker = "image: ghcr.io/jabrown93/artwork-uploader:latest"
    assert marker in readme, "published GHCR compose example is missing"
    published_example = readme.split(marker, 1)[1].split("```", 1)[0]

    assert "./bulk_imports:/bulk_imports:rw" in published_example
    assert "./config.json:/config/config.json:rw" in published_example
    assert "/artwork-uploader/" not in published_example
