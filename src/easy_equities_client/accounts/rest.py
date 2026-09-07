"""
Modern REST API used by the portfolio-overview web app
(portfolio-overview.apps.easyequities.io) - a separate SPA from
platform.easyequities.io, registered as its own OAuth2 client, calling a
clean JSON API instead of platform.easyequities.io's server-rendered
pages.

This is EasyEquities-specific (Satrix has no equivalent app/API as far as
this library knows), reverse-engineered from that SPA's own network
traffic rather than documented anywhere, so it's used as the *preferred*
source with the existing HTML-scraping AccountsClient as a fallback -
see EasyEquitiesAccountsClient. If EasyEquities changes or restricts this
API, callers keep working via the fallback rather than breaking outright.
"""

import base64
import hashlib
import os
from urllib.parse import parse_qs, urlparse

from requests import Session

from easy_equities_client.accounts.types import Account, Holding

# Registered specifically to the portfolio-overview SPA - different from
# the client_id PlatformClient.login() uses for the platform.easyequities.io
# login itself.
CLIENT_ID = "fa4d2622bc1e45a7be79395d941e2548"
REDIRECT_URI = "https://portfolio-overview.apps.easyequities.io/auth/callback"
SCOPE = "openid profile api_gateway user_profile_api static_data_api easy_protect_api easy_lending_api thrive_api"
PORTFOLIO_OVERVIEW_URL = (
    "https://rest.synatic.openeasy.io/easyequities/portfolios/v3/portfolio-overview"
)


class PortfolioApiError(Exception):
    """Raised for any failure obtaining or using the portfolio REST API -
    callers should catch this specifically and fall back to HTML scraping."""


def _generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


def get_portfolio_access_token(session: Session) -> str:
    """
    Given a session already authenticated against identity.openeasy.io
    (via PlatformClient.login()/verify_mfa()), silently obtains a Bearer
    access token for the portfolio-overview REST API.

    The session's existing SSO cookie means requesting a fresh
    authorization code for this API's own OAuth2 client redirects straight
    back with a code - no login or MFA prompt - which is then exchanged
    for an access token via the standard PKCE authorization_code grant
    (the code_verifier/code_challenge pair is generated fresh here, no
    dependency on how the session was originally authenticated).

    :raises PortfolioApiError: if the silent re-authorization or token
        exchange fails for any reason (e.g. the session isn't actually
        authenticated, or EasyEquities changed this flow).
    """
    code_verifier, code_challenge = _generate_pkce_pair()

    try:
        authorize_response = session.get(
            "https://identity.openeasy.io/connect/authorize",
            params={
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": SCOPE,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "response_mode": "query",
            },
            allow_redirects=False,
        )
    except Exception as exc:
        raise PortfolioApiError(
            f"Could not reach identity.openeasy.io for portfolio access: {exc}"
        ) from exc

    if authorize_response.status_code != 302:
        raise PortfolioApiError(
            f"Unexpected response requesting portfolio access (status {authorize_response.status_code}) - "
            "the session may not be logged in"
        )

    location = authorize_response.headers.get("Location", "")
    code = parse_qs(urlparse(location).query).get("code", [None])[0]
    if not code:
        raise PortfolioApiError(
            "Did not receive an authorization code for the portfolio API"
        )

    try:
        token_response = session.post(
            "https://identity.openeasy.io/connect/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code_verifier": code_verifier,
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
        )
        token_data = token_response.json()
    except Exception as exc:
        raise PortfolioApiError(
            f"Could not exchange the authorization code for a token: {exc}"
        ) from exc

    access_token = token_data.get("access_token")
    if not access_token:
        raise PortfolioApiError(f"Token response had no access_token: {token_data}")
    return access_token


def fetch_portfolio_overview(session: Session) -> dict:
    """
    Fetches the raw portfolio-overview JSON: every EasyEquities-family
    account (including EasyProperties/EasyCrypto, which live on entirely
    separate sites and have no equivalent in AccountsClient's HTML-scraped
    account list) with exact current/purchase values, in one call.

    :raises PortfolioApiError: if the access token or the API call fails.
    """
    access_token = get_portfolio_access_token(session)
    try:
        response = session.get(
            PORTFOLIO_OVERVIEW_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json, text/plain, */*",
            },
        )
    except Exception as exc:
        raise PortfolioApiError(f"Could not reach the portfolio API: {exc}") from exc

    if response.status_code != 200:
        raise PortfolioApiError(f"Portfolio API returned status {response.status_code}")

    try:
        return response.json()
    except Exception as exc:
        raise PortfolioApiError(
            f"Portfolio API returned an unexpected (non-JSON) response: {exc}"
        ) from exc


def account_id_from_account_number(account_number: str) -> str:
    """A regular trust account's accountNumber is "<entity>-<numeric id>" -
    that numeric id matches the id AccountsClient's HTML-scraping already
    uses, so an account linked via one source keeps the same id via the
    other. EasyEquities-family products that aren't a numbered trust
    account (EasyCrypto) get their own standalone reference instead."""
    suffix = account_number.rsplit("-", 1)[-1]
    return suffix if suffix.isdigit() else account_number


def rest_account_to_account(entry: dict) -> Account:
    account_number = entry.get("accountNumber", "")
    product_id = entry.get("productId")
    return Account(
        id=account_id_from_account_number(account_number),
        name=entry.get("productName", account_number),
        trading_currency_id=str(product_id) if product_id is not None else "",
    )


def _format_currency(amount: float, symbol: str) -> str:
    """Matches the string format AccountHoldingsParser scrapes from the
    HTML pages (e.g. "R2 490.68", "-$0.11") - space-grouped thousands, no
    space between the symbol and the number - so a Holding built from
    either source looks the same to callers."""
    sign = "-" if amount < 0 else ""
    grouped = f"{abs(amount):,.2f}".replace(",", " ")
    return f"{sign}{symbol}{grouped}"


def rest_assets_to_holdings(account_entry: dict) -> list[Holding]:
    """Best-effort mapping of one account's REST `assets` into the same
    Holding shape AccountHoldingsParser produces from HTML. Not a perfect
    match - the REST API has no equivalent of view_url (holding detail
    page link), which HTML-scraped holdings.include_shares=True needs, so
    that option only ever adds data via the HTML fallback."""
    symbol = account_entry.get("currencySymbol", "")
    holdings: list[Holding] = []
    for asset in account_entry.get("assets", []):
        holding: Holding = {
            "name": asset.get("assetName", ""),
            "contract_code": asset.get("contractCode", ""),
            "purchase_value": _format_currency(asset.get("purchaseValue") or 0, symbol),
            "current_value": _format_currency(asset.get("currentValue") or 0, symbol),
            "current_price": _format_currency(asset.get("currentPrice") or 0, symbol),
            "isin": asset.get("assetCode", ""),
            "shares": str(asset.get("units", "")),
        }
        image_uri = asset.get("imageUri")
        if image_uri:
            try:
                holding["img"] = base64.b64decode(image_uri).decode()
            except Exception:
                pass
        holdings.append(holding)
    return holdings
