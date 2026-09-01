import pytest

from core.exceptions import InvalidUrl
from utils.utils import is_valid_url, parse_url_and_options


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/poster.jpg",
        "http://127.0.0.1/poster.jpg",
        "http://[::1]/poster.jpg",
        "https://münich.example/poster.jpg",
        "http://localhost/poster.jpg",
        "https://example.com./poster.jpg",
        "https://example.com/poster.jpg --temp",
    ],
)
def test_is_valid_url_accepts_http_urls(url):
    assert is_valid_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/poster.jpg",
        "example.com/poster.jpg",
        "https://example.com:abc/poster.jpg",
        "https://example.com:99999/poster.jpg",
        "https://example..com/poster.jpg",
        "https://-example.com/poster.jpg",
        "https://example-.com/poster.jpg",
        "http://999.1.1.1/poster.jpg",
        "http://[::gg]/poster.jpg",
        "https://" + "a" * 64 + ".example/poster.jpg",
        "https://"
        + ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 62])
        + "/poster.jpg",
        "https://" + chr(0xD800) + ".example/poster.jpg",
        "",
    ],
)
def test_is_valid_url_rejects_non_http_or_malformed_urls(url):
    assert not is_valid_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/poster.jpg",
        "https://exa mple.com/poster.jpg",
        "https://example.com:abc/poster.jpg",
        "https://example.com:99999/poster.jpg",
        "https://example..com/poster.jpg",
        "https://-example.com/poster.jpg",
    ],
)
def test_parse_url_and_options_rejects_non_http_or_malformed_url(url):
    with pytest.raises(InvalidUrl):
        parse_url_and_options(url)


def test_parse_url_and_options_keeps_local_html_support():
    item = parse_url_and_options("saved-page.html")

    assert item.url == "saved-page.html"
