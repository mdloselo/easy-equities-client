import json
import logging
from datetime import date, timedelta
from typing import Any, List, Optional

from bs4 import BeautifulSoup
from requests import Session

from easy_equities_client import constants
from easy_equities_client.accounts.parsers import (
    AccountHoldingsParser,
    AccountOverviewParser,
    get_transactions_from_page,
)
from easy_equities_client.accounts.rest import (
    PortfolioApiError,
    account_id_from_account_number,
    fetch_portfolio_overview,
    rest_account_to_account,
    rest_assets_to_holdings,
)
from easy_equities_client.accounts.types import (
    Account,
    Holding,
    Transaction,
    TransactionForPeriod,
    Valuation,
)
from easy_equities_client.types import Client

logger = logging.getLogger(__name__)


class UnsupportedAccountError(Exception):
    """
    Raised when an "account" from AccountsClient.list() isn't actually a
    trust account on this platform - EasyProperties, EasyCrypto, and other
    EasyEquities-family products appear in the same account list, but are
    hosted on entirely separate sites (their own login, their own data).
    Switching to one doesn't select anything server-side here - it's a
    signal meant for a browser to open a new tab - so silently proceeding
    would leave whatever account was previously active still selected,
    corrupting the next holdings()/valuations() call for this account_id.
    """


class AccountsClient(Client):
    def __init__(self, base_url: str = "", session: Session = None):
        super().__init__(base_url, session)
        self.current_account: Optional[str] = None

    def _get_account_overview_page(self) -> str:
        response = self.session.get(self._url(constants.PLATFORM_ACCOUNT_OVERVIEW_PATH))
        assert response.status_code == 200, (
            "Account overview page should return 200 status code"
        )
        assert "My Investments" in str(response.content)
        return str(response.content)

    def list(self) -> List[Account]:
        page = self._get_account_overview_page()
        parser = AccountOverviewParser(page)
        return parser.extract_accounts()

    def _switch_account(self, account_id: str) -> None:
        """
        Switch the currently selected account to account with ID account_id.

        :raises UnsupportedAccountError: if account_id belongs to a
            different EasyEquities-family platform (EasyProperties,
            EasyCrypto, ...) rather than a real trust account here.
        """
        if self.current_account != account_id:
            data = {"trustAccountId": account_id}
            response = self.session.post(
                self._url(constants.PLATFORM_UPDATE_CURRENCY_PATH), data
            )
            response.raise_for_status()
            assert response.status_code == 200, (
                "Update currency request should return 200 status code"
            )
            if response.text.strip().strip('"').startswith("NEWTAB-"):
                target = response.text.strip().strip('"')[len("NEWTAB-") :]
                raise UnsupportedAccountError(
                    f"Account {account_id} is hosted on a separate platform ({target}) - "
                    "its holdings can't be fetched through this client."
                )
            self.current_account = account_id

    def valuations(self, account_id: str) -> Valuation:
        self._switch_account(account_id)
        response = self.session.get(
            self._url(constants.PLATFORM_ACCOUNT_VALUATIONS_PATH)
        )
        response.raise_for_status()
        return json.loads(response.json())

    def transactions(self, account_id: str) -> List[Transaction]:
        """
        Gets JSON-formatted transactions for the last year.
        """
        self._switch_account(account_id)
        response = self.session.get(self._url(constants.PLATFORM_TRANSACTIONS_PATH))
        response.raise_for_status()
        return response.json()

    def transactions_for_period(
        self, account_id: str, start_date: date, end_date: date
    ) -> List[TransactionForPeriod]:
        """
        Gets transactions for a given period. Unfortunately not JSON-formatted
        and contains less useful data than the yearly data, because we need
        to get it from the UI.

        Returns transactions ordered in reverse chronological order (newest to oldest).
        """
        self._switch_account(account_id)
        transactions: List[Any] = []

        current_start = start_date
        current_end = min(end_date, current_start + timedelta(days=90))

        while current_start < end_date:
            logger.debug(f"Current start: {current_start}, Current end: {current_end}")
            transactions_for_date_range = []

            page_number = 1
            while True:
                next_url = self._url(
                    constants.PLATFORM_TRANSACTIONS_SEARCH_PATH_NEXT_PAGE.format(
                        start_date=f"{current_start.month}/{current_start.day}/{current_start.year}",
                        end_date=f"{current_end.month}/{current_end.day}/{current_end.year}",
                        page_number=page_number,
                    )
                )
                response = self.session.get(next_url)
                new_transactions = get_transactions_from_page(response.content)
                if len(new_transactions) == 0:
                    # No more transactions left
                    break
                transactions_for_date_range += new_transactions
                page_number += 1

            transactions = transactions_for_date_range + transactions

            current_start = current_end + timedelta(days=1)
            current_end = min(end_date, current_start + timedelta(days=90))

        return transactions

    def holdings(self, account_id: str, include_shares: bool = False) -> List[Holding]:
        """
        Get an account's holdings/stocks.

        :param account_id: String account ID.
        :param include_shares: Whether to fetch the number of shares per holding. Create an extra
        HTTP request per holding.
        """
        self._switch_account(account_id)
        response = self.session.get(self._url(constants.PLATFORM_HOLDINGS_PATH))
        response.raise_for_status()
        parser = AccountHoldingsParser(response.content)
        holdings = parser.extract_holdings()
        if include_shares:
            for holding in holdings:
                response = self.session.get(self._url(holding["view_url"]))
                soup = BeautifulSoup(response.content, "html.parser")
                whole_shares = soup.find(
                    lambda tag: "#Shares" in tag
                ).next_sibling.next_sibling.text.strip()
                partial_shares = soup.find(
                    lambda tag: "#FSR" in tag
                ).next_sibling.next_sibling.text.strip()
                holding["shares"] = f"{whole_shares}{partial_shares}"
        return holdings


class EasyEquitiesAccountsClient(AccountsClient):
    """
    Same as AccountsClient, but tries the modern portfolio-overview REST
    API first (see easy_equities_client.accounts.rest) - one JSON call
    covering every EasyEquities-family account, including EasyProperties
    and EasyCrypto, which the HTML-scraped account list can't reach at
    all (they live on entirely separate sites). Falls back to the
    existing HTML-scraping behavior if the REST API is unavailable for
    any reason, so callers keep working even if EasyEquities changes or
    restricts it.
    """

    def __init__(self, base_url: str = "", session: Session = None):
        super().__init__(base_url, session)
        self._portfolio_cache: Optional[dict] = None

    def _portfolio(self) -> dict:
        # Cached per instance - list() followed by holdings() for each of
        # its accounts would otherwise redo the whole silent-reauth +
        # token exchange + REST call every time, even though one response
        # already has everything.
        if self._portfolio_cache is None:
            self._portfolio_cache = fetch_portfolio_overview(self.session)
        return self._portfolio_cache

    def list(self) -> List[Account]:
        try:
            portfolio = self._portfolio()
        except PortfolioApiError as exc:
            logger.warning(
                f"Portfolio API unavailable, falling back to HTML scraping: {exc}"
            )
            return super().list()
        return [
            rest_account_to_account(entry)
            for entry in portfolio.get("investmentAccounts", [])
        ]

    def holdings(self, account_id: str, include_shares: bool = False) -> List[Holding]:
        try:
            portfolio = self._portfolio()
        except PortfolioApiError as exc:
            logger.warning(
                f"Portfolio API unavailable, falling back to HTML scraping: {exc}"
            )
            return super().holdings(account_id, include_shares=include_shares)

        for entry in portfolio.get("investmentAccounts", []):
            if (
                account_id_from_account_number(entry.get("accountNumber", ""))
                == account_id
            ):
                # include_shares has no REST equivalent (no per-holding
                # detail page to fetch from) - the HTML-scraped units
                # count is already in the mapped holding either way.
                return rest_assets_to_holdings(entry)

        # Not present in the REST response at all - shouldn't normally
        # happen since it returns everything, but fall back rather than
        # silently returning nothing for a real account_id.
        logger.warning(
            f"Account {account_id} not found in the portfolio API response, falling back to HTML scraping"
        )
        return super().holdings(account_id, include_shares=include_shares)
