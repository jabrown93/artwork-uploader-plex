"""The socket does the real work of the web UI, so it must refuse a session that has not logged in.

The gate sits on connect, and a per-event guard re-checks it on every handler too (Fork:
socket_login_required in web_routes.py), so a client that connected before auth was turned on -
or whose session has since logged out or whose OIDC id_token has since expired - does not keep
full access to the handlers below.
"""

import time

import pytest


@pytest.mark.unit
def test_a_socket_connects_when_no_authentication_is_configured(web_harness):
    assert web_harness.socket_client().is_connected()


@pytest.mark.unit
def test_a_socket_is_refused_when_basic_auth_is_on_and_nobody_has_logged_in(web_harness):
    web_harness.config.auth_enabled = True

    assert not web_harness.socket_client().is_connected()


@pytest.mark.unit
def test_a_socket_is_refused_when_oidc_is_on_and_nobody_has_logged_in(web_harness):
    web_harness.config.oidc_enabled = True

    assert not web_harness.socket_client().is_connected()


@pytest.mark.unit
def test_a_logged_in_session_connects(web_harness):
    web_harness.config.auth_enabled = True

    http_client = web_harness.http_client()
    with http_client.session_transaction() as flask_session:
        flask_session["authenticated"] = True

    assert web_harness.socket_client(http_client).is_connected()


@pytest.mark.unit
def test_a_refused_socket_reaches_no_handler(web_harness):
    """load_config sends the whole config object back, tokens and password hash included."""
    web_harness.config.auth_enabled = True
    client = web_harness.socket_client()

    with pytest.raises(RuntimeError):
        client.emit("load_config", {"instance_id": "abc"})


@pytest.mark.unit
def test_a_session_that_connected_before_auth_turned_on_is_rejected_on_the_next_event(web_harness):
    """Fork: socket_login_required re-checks on every event, not only at connect, so a client
    that connected while auth was off does not keep full access once auth is turned on."""
    client = web_harness.socket_client()
    assert client.is_connected()

    web_harness.config.auth_enabled = True
    client.emit("load_config", {"instance_id": "abc"})

    assert not client.is_connected()


@pytest.mark.unit
def test_a_session_whose_oidc_id_token_has_expired_is_rejected_on_the_next_event(web_harness):
    """Fork: socket_login_required also enforces the stored OIDC exp (session['oidc_exp']).
    The connect handler only checks session['authenticated'] (exp enforcement happens on the
    per-event guard, same as the already-logged-out case above), so a connection carrying an
    already-expired token still connects but the first event after that is rejected - not kept
    alive until the Flask session cookie itself expires."""
    web_harness.config.oidc_enabled = True
    http_client = web_harness.http_client()
    with http_client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
        flask_session["oidc_exp"] = time.time() - 1

    client = web_harness.socket_client(http_client)
    assert client.is_connected()

    client.emit("load_config", {"instance_id": "abc"})

    assert not client.is_connected()
