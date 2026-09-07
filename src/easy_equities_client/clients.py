import html
import re
from urllib.parse import urljoin

from requests import Response, Session

from easy_equities_client import constants
from easy_equities_client.accounts.clients import (
    AccountsClient,
    EasyEquitiesAccountsClient,
)
from easy_equities_client.instruments.clients import InstrumentsClient
from easy_equities_client.types import Client

# EasyEquities' WAF blocks the default python-requests User-Agent outright
# (403) before the request even reaches the login page - any realistic
# browser UA gets through.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class MfaRequiredError(Exception):
    """
    Raised by PlatformClient.login() when EasyEquities requires an
    authenticator-app (TOTP) code to finish signing in.

    Call verify_mfa(code) on the same client instance, with the current
    6-digit code from the person's authenticator app, to complete login.
    """


def _extract_value(name: str, html_text: str) -> str | None:
    m = re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), html_text)
    return m.group(1) if m else None


def _parse_auto_submit_form(html_text: str) -> tuple[str | None, dict[str, str]]:
    """Parses an OIDC response_mode=form_post callback page: instead of an
    HTTP redirect, the identity provider returns an HTML page with an
    auto-submitting <form> that a real browser's JS posts immediately."""
    form_match = re.search(
        r"<form[^>]*action=['\"]([^'\"]*)['\"][^>]*>(.*?)</form>", html_text, re.DOTALL
    )
    if not form_match:
        return None, {}
    action = html.unescape(form_match.group(1))
    fields = {
        m.group(1): html.unescape(m.group(2))
        for m in re.finditer(
            r"<input[^>]*name=['\"]([^'\"]+)['\"][^>]*value=['\"]([^'\"]*)['\"]",
            form_match.group(2),
        )
    }
    return action, fields


class PlatformClient(Client):
    """
    Generic client for https://platform.easyequities.io
    and https://platform.satrixnow.co.za.
    """

    # Subclasses (EasyEquitiesClient) override this to plug in a richer
    # AccountsClient without duplicating the rest of __init__.
    accounts_client_class = AccountsClient

    def __init__(self, base_url, session: Session = None):
        super().__init__(base_url, session)
        self.accounts = self.accounts_client_class(base_url, self.session)
        self.instruments = InstrumentsClient(base_url, self.session)
        self._mfa_token: str | None = None
        self._mfa_url: str | None = None

    def login(self, username: str, password: str) -> bool:
        """
        Login to the platform.

        EasyEquities now routes login through a separate OIDC identity
        provider (identity.openeasy.io, "EasyID") instead of handling it
        directly on the platform site, and may require a TOTP
        authenticator-app code as a second factor.

        :param username: Username.
        :param password: Password.

        :return: True if login completed without needing a second factor.
        :raises MfaRequiredError: if an authenticator-app code is required -
            call verify_mfa(code) on this same client to finish logging in.
        :raises Exception: if the request failed outright (e.g. wrong
            username/password, or EasyEquities changed their login page
            again and the expected form fields weren't found).
        """
        self.session.headers["User-Agent"] = _BROWSER_USER_AGENT

        signin_page = self.session.get(self._url(constants.PLATFORM_SIGN_IN_PATH))
        token = _extract_value("__RequestVerificationToken", signin_page.text)
        return_url = html.unescape(_extract_value("ReturnUrl", signin_page.text) or "")
        client_id = _extract_value("ClientIdForProperties", signin_page.text)
        if not token or not client_id:
            raise Exception(
                "Could not find the login form - EasyEquities may have changed their login page"
            )

        response = self.session.post(
            signin_page.url,
            data={
                "ReturnUrl": return_url,
                "ClientIdForProperties": client_id,
                "Response": "",
                "Username": username,
                "IsUsernameProvided": "False",
                "Password": password,
                "button": "login",
                "__RequestVerificationToken": token,
            },
            allow_redirects=False,
        )
        if response.status_code != 302:
            raise Exception("Login failed - check your username/password")

        next_location = response.headers.get("Location", "")
        if "Mfa" not in next_location:
            self._follow_oidc_redirect(response)
            return True

        mfa_url = urljoin(response.url, next_location)
        mfa_page = self.session.get(mfa_url)
        mfa_token = _extract_value("__RequestVerificationToken", mfa_page.text)
        if not mfa_token:
            raise Exception(
                "Could not find the verification code form - EasyEquities may have changed their MFA page"
            )

        self._mfa_token = mfa_token
        self._mfa_url = mfa_page.url
        raise MfaRequiredError(
            "An authenticator-app code is required - call verify_mfa(code) to continue."
        )

    def verify_mfa(self, code: str) -> bool:
        """
        Completes login after login() raised MfaRequiredError.

        :param code: the current 6-digit code from the authenticator app.
        :return: True once login is fully complete.
        :raises Exception: if the code is invalid, or verification
            otherwise failed.
        """
        if not self._mfa_url or not self._mfa_token:
            raise Exception(
                "verify_mfa() called without a pending MFA challenge - call login() first."
            )

        response = self.session.post(
            self._mfa_url,
            data={
                "ProviderName": "totp",
                "Code": code,
                "button": "send",
                "__RequestVerificationToken": self._mfa_token,
                "RememberMe": "false",
            },
            allow_redirects=False,
        )
        if response.status_code == 200 and "Invalid Code" in response.text:
            raise Exception("Invalid verification code")
        if response.status_code not in (301, 302, 303, 307, 308):
            raise Exception(
                f"Unexpected response from EasyEquities during verification (status {response.status_code})"
            )

        self._follow_oidc_redirect(response)
        self._mfa_token = None
        self._mfa_url = None
        return True

    def _follow_oidc_redirect(self, response: Response, max_hops: int = 10) -> None:
        """Follows the rest of the OIDC callback chain back to the platform
        site, given the response that started it (a 3xx redirect).

        The final hop uses response_mode=form_post - an HTML page with an
        auto-submitting <form> instead of a plain HTTP redirect - so plain
        redirects alone aren't enough to complete authentication. Every hop
        is resolved with urljoin() against the *actual* URL of the response
        that produced it (not a hardcoded host), since this chain crosses
        from the identity provider back to the platform site partway
        through.
        """
        hops = 0
        while hops < max_hops:
            if response.status_code in (301, 302, 303, 307, 308):
                next_url = urljoin(response.url, response.headers.get("Location", ""))
                response = self.session.get(next_url, allow_redirects=False)
                hops += 1
                continue

            action, fields = _parse_auto_submit_form(response.text)
            if action and fields:
                next_url = urljoin(response.url, action)
                response = self.session.post(
                    next_url, data=fields, allow_redirects=False
                )
                hops += 1
                continue

            return


class EasyEquitiesClient(PlatformClient):
    """
    Client to interact with EasyEquities.
    """

    # Tries the modern portfolio-overview REST API first, falling back to
    # the HTML-scraping AccountsClient - see EasyEquitiesAccountsClient.
    accounts_client_class = EasyEquitiesAccountsClient

    def __init__(self, base_url: str = constants.EASY_EQUITIES_BASE_PLATFORM_URL):
        return super().__init__(base_url)


class SatrixClient(PlatformClient):
    """
    Client to interact with Satrix.
    """

    def __init__(self, base_url: str = constants.SATRIX_BASE_PLATFORM_URL):
        return super().__init__(base_url)
