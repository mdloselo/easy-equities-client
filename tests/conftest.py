import json

import pytest

from easy_equities_client import constants
from easy_equities_client.clients import (
    EasyEquitiesClient,
    PlatformClient,
    SatrixClient,
)


@pytest.fixture
def base_platform_url():
    return "https://platform.test.io"


_SIGNIN_PAGE_HTML = """
<html><body><form>
<input type="hidden" name="__RequestVerificationToken" value="test-token" />
<input type="hidden" name="ReturnUrl" value="/connect/authorize/callback?state=abc" />
<input type="hidden" name="ClientIdForProperties" value="test-client-id" />
</form></body></html>
"""

_MFA_PAGE_HTML = """
<html><body><form>
<input type="hidden" name="__RequestVerificationToken" value="test-mfa-token" />
</form></body></html>
"""

_FORM_POST_CALLBACK_HTML = """
<html><body><form method='post' action='{platform_url}'>
<input type='hidden' name='code' value='test-auth-code' />
</form></body></html>
"""


@pytest.fixture
def mock_success_login_response(requests_mock):
    """Mocks a full login with no MFA challenge: sign-in page -> POST ->
    redirect straight through to a final landing page."""

    def _mock_success_login_response(base_url):
        signin_url = base_url + constants.PLATFORM_SIGN_IN_PATH
        requests_mock.get(signin_url, text=_SIGNIN_PAGE_HTML)
        requests_mock.post(
            signin_url, status_code=302, headers={"Location": "/Dashboard"}
        )
        requests_mock.get(base_url + "/Dashboard", status_code=200, text="ok")

    return _mock_success_login_response


@pytest.fixture
def mock_mfa_login_response(requests_mock):
    """Mocks a login that requires an authenticator-app code: sign-in page
    -> POST -> redirect to the MFA challenge page."""

    def _mock_mfa_login_response(base_url):
        signin_url = base_url + constants.PLATFORM_SIGN_IN_PATH
        # Real EasyID lives on a separate domain (identity.openeasy.io);
        # this test keeps everything on base_url as a stand-in for "wherever
        # the redirect chain currently is" - the code under test resolves
        # each hop with urljoin() against the actual response URL, so it
        # doesn't care which host is used as long as it's followed
        # consistently.
        mfa_url = base_url + "/Mfa/Authenticate"
        requests_mock.get(signin_url, text=_SIGNIN_PAGE_HTML)
        requests_mock.post(
            signin_url, status_code=302, headers={"Location": "/Mfa/Authenticate"}
        )
        requests_mock.get(mfa_url, text=_MFA_PAGE_HTML)
        return mfa_url

    return _mock_mfa_login_response


@pytest.fixture
def mock_verify_mfa_response(requests_mock):
    """Mocks a successful code verification: POST to the MFA endpoint ->
    redirect to an OIDC form_post callback page -> auto-submit POST to the
    platform site -> final landing page."""

    def _mock_verify_mfa_response(base_url, mfa_url, valid_code):
        callback_url = base_url + "/connect/authorize/callback"

        requests_mock.post(
            mfa_url,
            status_code=200,
            text="Error\nInvalid Code",
            additional_matcher=lambda r: f"Code={valid_code}" not in (r.text or ""),
        )
        requests_mock.post(
            mfa_url,
            status_code=302,
            headers={"Location": callback_url},
            additional_matcher=lambda r: f"Code={valid_code}" in (r.text or ""),
        )
        requests_mock.get(
            callback_url,
            text=_FORM_POST_CALLBACK_HTML.format(
                platform_url=base_url + "/AccountOverview"
            ),
        )
        requests_mock.post(base_url + "/AccountOverview", status_code=200, text="ok")

    return _mock_verify_mfa_response


@pytest.fixture
def platform_client(base_platform_url):
    return PlatformClient(base_platform_url)


@pytest.fixture
def easy_equities_client():
    return EasyEquitiesClient()


@pytest.fixture
def satrix_client():
    return SatrixClient()


@pytest.fixture
def account_overview_page():
    with open("./tests/data/account-overview.html", "r") as f:
        return f.read()


@pytest.fixture
def account_holdings_page():
    with open("./tests/data/account-holdings.html", "r") as f:
        return f.read()


@pytest.fixture
def holding_details_page():
    with open("./tests/data/holding-details.html", "r") as f:
        return f.read()


@pytest.fixture
def valuations():
    with open("./tests/data/account-valuations.json", "r") as f:
        return f.read()


@pytest.fixture
def account_transactions():
    with open("./tests/data/account-transactions.json", "r") as f:
        return json.loads(f.read())


@pytest.fixture
def historical_prices():
    with open("./tests/data/historical-prices.json", "r") as f:
        return json.loads(f.read())


@pytest.fixture
def account_transactions_page1():
    with open("./tests/data/transactions-history-page1.html", "r") as f:
        return f.read()


@pytest.fixture
def account_transactions_empty():
    with open("./tests/data/transactions-history-empty.html", "r") as f:
        return f.read()
