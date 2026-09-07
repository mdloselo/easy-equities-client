import json
from datetime import date

import pytest

from easy_equities_client import constants
from easy_equities_client.accounts.clients import (
    AccountsClient,
    EasyEquitiesAccountsClient,
    UnsupportedAccountError,
)
from easy_equities_client.accounts.rest import PortfolioApiError
from easy_equities_client.accounts.types import Account


class TestAccountsClient:
    def test_switch_account(self, base_platform_url, requests_mock):
        url = base_platform_url + constants.PLATFORM_UPDATE_CURRENCY_PATH
        requests_mock.post(url, status_code=200, text="")
        client = AccountsClient(base_platform_url)
        client._switch_account("1")
        assert client.current_account == "1"

    def test_switch_account_raises_for_separate_platform(
        self, base_platform_url, requests_mock
    ):
        # EasyProperties, EasyCrypto etc. appear in the account list but are
        # hosted on an entirely separate site - "switching" to one just
        # tells a browser to open a new tab, it doesn't select an account
        # here.
        url = base_platform_url + constants.PLATFORM_UPDATE_CURRENCY_PATH
        requests_mock.post(
            url,
            status_code=200,
            text='"NEWTAB-https://platform.easyproperties.co.za"',
        )
        client = AccountsClient(base_platform_url)
        with pytest.raises(UnsupportedAccountError):
            client._switch_account("10328939")
        # Must not look like a successful switch happened - otherwise the
        # *next* real account's holdings() would silently reuse whatever
        # was actually selected before this call.
        assert client.current_account is None

    def test_get_account_overview_page(self, base_platform_url, requests_mock):
        url = base_platform_url + constants.PLATFORM_ACCOUNT_OVERVIEW_PATH
        text = b"My Investments"
        requests_mock.get(url, status_code=200, content=text)
        client = AccountsClient(base_platform_url)
        assert client._get_account_overview_page() == str(text)

    def test_list(self, mocker):
        get_account_overview_page_mock = mocker.patch(
            "easy_equities_client.accounts.clients.AccountsClient._get_account_overview_page"
        )
        account_overview_parser_mock = mocker.patch(
            "easy_equities_client.accounts.clients.AccountOverviewParser"
        )
        accounts = [Account("1", "Test", "1000")]
        get_account_overview_page_mock.return_value = None
        account_overview_parser_mock.return_value.extract_accounts.return_value = (
            accounts
        )
        client = AccountsClient()
        assert client.list() == accounts

    def test_account_valuations(
        self, base_platform_url, requests_mock, mocker, valuations
    ):
        mocker.patch(
            "easy_equities_client.accounts.clients.AccountsClient._switch_account"
        )
        url = base_platform_url + constants.PLATFORM_ACCOUNT_VALUATIONS_PATH
        requests_mock.get(url, status_code=200, json=valuations)
        client = AccountsClient(base_platform_url)
        assert client.valuations("1") == json.loads(valuations)

    def test_account_transactions(
        self, base_platform_url, requests_mock, mocker, account_transactions
    ):
        mocker.patch(
            "easy_equities_client.accounts.clients.AccountsClient._switch_account"
        )
        url = base_platform_url + constants.PLATFORM_TRANSACTIONS_PATH
        requests_mock.get(url, status_code=200, json=account_transactions)
        client = AccountsClient(base_platform_url)
        assert client.transactions("1") == account_transactions

    def test_account_holdings(
        self,
        base_platform_url,
        requests_mock,
        mocker,
        account_holdings_page,
        holding_details_page,
    ):
        mocker.patch(
            "easy_equities_client.accounts.clients.AccountsClient._switch_account"
        )
        # Mock holdings
        requests_mock.get(
            base_platform_url + constants.PLATFORM_HOLDINGS_PATH,
            status_code=200,
            content=str.encode(account_holdings_page),
        )
        # Mock holding stocks
        requests_mock.get(
            base_platform_url
            + "/AccountOverview/GetInstrumentDetailAction/?IsinCode=ZAE000249512",
            status_code=200,
            content=str.encode(holding_details_page),
        )
        requests_mock.get(
            base_platform_url
            + "/AccountOverview/GetInstrumentDetailAction/?IsinCode=ZAE000249538",
            status_code=200,
            content=str.encode(holding_details_page),
        )

        client = AccountsClient(base_platform_url)
        holdings = client.holdings("1", include_shares=True)
        expected_data = [
            {
                "name": "SYGNIA ITRIX EUROSTOXX50",
                "purchase_value": "R2 490.68",
                "current_value": "R2 605.45",
                "current_price": "R64.08",
                "img": "https://resources.easyequities.co.za/logos/TFSA.SYGEU.png",
                "view_url": "/AccountOverview/GetInstrumentDetailAction/?IsinCode=ZAE000249512",
                "isin": "ZAE000249512",
                "contract_code": "TFSA.SYGEU",
                "shares": "200.0123",
            },
            {
                "name": "SYGNIA ITRIX MSCI JAPAN",
                "purchase_value": "R2 527.83",
                "current_value": "R2 621.85",
                "current_price": "R15.81",
                "img": "https://resources.easyequities.co.za/logos/TFSA.SYGJP.png",
                "view_url": "/AccountOverview/GetInstrumentDetailAction/?IsinCode=ZAE000249538",
                "isin": "ZAE000249538",
                "contract_code": "TFSA.SYGJP",
                "shares": "200.0123",
            },
        ]

        assert sorted(holdings, key=lambda x: x["name"]) == expected_data

    def test_account_transactions_for_period(
        self,
        base_platform_url,
        requests_mock,
        mocker,
        account_transactions_page1,
        account_transactions_empty,
    ):
        mocker.patch(
            "easy_equities_client.accounts.clients.AccountsClient._switch_account"
        )
        start_date = date(2021, 8, 1)
        end_date = date(2021, 8, 31)
        page1_url = (
            base_platform_url
            + constants.PLATFORM_TRANSACTIONS_SEARCH_PATH_NEXT_PAGE.format(
                start_date=f"{start_date.month}/{start_date.day}/{start_date.year}",
                end_date=f"{end_date.month}/{end_date.day}/{end_date.year}",
                page_number=1,
            )
        )
        page2_url = (
            base_platform_url
            + constants.PLATFORM_TRANSACTIONS_SEARCH_PATH_NEXT_PAGE.format(
                start_date=f"{start_date.month}/{start_date.day}/{start_date.year}",
                end_date=f"{end_date.month}/{end_date.day}/{end_date.year}",
                page_number=2,
            )
        )
        requests_mock.get(
            page1_url,
            status_code=200,
            content=str.encode(account_transactions_page1),
        )
        requests_mock.get(
            page2_url, status_code=200, content=str.encode(account_transactions_empty)
        )
        expected_output = [
            {
                "date": "2021-08-31",
                "description": "Account Balance Carried Forward",
                "amount": "R63.55",
                "currency_code": "ZAR",
                "currency_symbol": "R",
                "value": 63.55,
            },
            {
                "date": "2021-08-16",
                "description": "Schroder European Real Estate Inv Trust PLC-Foreign Dividends @31.49625",
                "amount": "R52.21",
                "currency_code": "ZAR",
                "currency_symbol": "R",
                "value": 52.21,
            },
            {
                "date": "2021-08-16",
                "description": "Dividend Withholding Tax SCD-Dividend Withholding Tax @20%",
                "amount": "-R10.44",
                "currency_code": "ZAR",
                "currency_symbol": "R",
                "value": -10.44,
            },
            {
                "date": "2021-08-01",
                "description": "Interest",
                "amount": "R0.03",
                "currency_code": "ZAR",
                "currency_symbol": "R",
                "value": 0.03,
            },
            {
                "date": "2021-08-01",
                "description": "Cash Management Fee",
                "amount": "-R0.01",
                "currency_code": "ZAR",
                "currency_symbol": "R",
                "value": -0.01,
            },
            {
                "date": "2021-08-01",
                "description": "VAT on Cash Management Fee",
                "amount": "R0.00",
                "currency_code": "ZAR",
                "currency_symbol": "R",
                "value": 0,
            },
            {
                "date": "2021-08-01",
                "description": "Account Balance Brought Forward",
                "amount": "R21.77",
                "currency_code": "ZAR",
                "currency_symbol": "R",
                "value": 21.77,
            },
        ]
        client = AccountsClient(base_platform_url)
        result = client.transactions_for_period("1", start_date, end_date)
        assert result == expected_output


class TestEasyEquitiesAccountsClient:
    """EasyEquitiesAccountsClient prefers the portfolio REST API and falls
    back to the same HTML-scraping AccountsClient already tested above -
    these tests only cover the REST-vs-fallback wiring, not re-testing the
    HTML scraping or REST mapping logic themselves (see test_clients.py's
    AccountsClient tests and test_rest.py)."""

    def test_list_uses_rest_api(self, base_platform_url, mocker):
        mocker.patch(
            "easy_equities_client.accounts.clients.fetch_portfolio_overview",
            return_value={
                "investmentAccounts": [
                    {
                        "accountNumber": "EE1-1",
                        "productName": "EasyEquities ZAR",
                        "productId": 2,
                    }
                ]
            },
        )
        client = EasyEquitiesAccountsClient(base_platform_url)
        assert client.list() == [
            Account(id="1", name="EasyEquities ZAR", trading_currency_id="2")
        ]

    def test_list_falls_back_to_html_on_rest_failure(self, base_platform_url, mocker):
        mocker.patch(
            "easy_equities_client.accounts.clients.fetch_portfolio_overview",
            side_effect=PortfolioApiError("REST API unavailable"),
        )
        html_fallback = mocker.patch(
            "easy_equities_client.accounts.clients.AccountsClient.list",
            return_value=[Account(id="1", name="Test", trading_currency_id="1000")],
        )
        client = EasyEquitiesAccountsClient(base_platform_url)
        assert client.list() == [
            Account(id="1", name="Test", trading_currency_id="1000")
        ]
        html_fallback.assert_called_once()

    def test_holdings_uses_rest_api(self, base_platform_url, mocker):
        mocker.patch(
            "easy_equities_client.accounts.clients.fetch_portfolio_overview",
            return_value={
                "investmentAccounts": [
                    {
                        "accountNumber": "EE1-1",
                        "currencySymbol": "R",
                        "assets": [
                            {
                                "assetName": "Test Asset",
                                "contractCode": "TST",
                                "assetCode": "ZAE000000001",
                                "purchaseValue": 100,
                                "currentValue": 110,
                                "currentPrice": 11,
                                "units": 10,
                            }
                        ],
                    }
                ]
            },
        )
        client = EasyEquitiesAccountsClient(base_platform_url)
        holdings = client.holdings("1")
        assert len(holdings) == 1
        assert holdings[0]["name"] == "Test Asset"
        assert holdings[0]["current_value"] == "R110.00"

    def test_holdings_falls_back_to_html_on_rest_failure(
        self, base_platform_url, mocker
    ):
        mocker.patch(
            "easy_equities_client.accounts.clients.fetch_portfolio_overview",
            side_effect=PortfolioApiError("REST API unavailable"),
        )
        html_fallback = mocker.patch(
            "easy_equities_client.accounts.clients.AccountsClient.holdings",
            return_value=[{"name": "HTML fallback holding"}],
        )
        client = EasyEquitiesAccountsClient(base_platform_url)
        assert client.holdings("1") == [{"name": "HTML fallback holding"}]
        html_fallback.assert_called_once_with("1", include_shares=False)

    def test_holdings_falls_back_when_account_not_in_rest_response(
        self, base_platform_url, mocker
    ):
        mocker.patch(
            "easy_equities_client.accounts.clients.fetch_portfolio_overview",
            return_value={"investmentAccounts": []},
        )
        html_fallback = mocker.patch(
            "easy_equities_client.accounts.clients.AccountsClient.holdings",
            return_value=[{"name": "HTML fallback holding"}],
        )
        client = EasyEquitiesAccountsClient(base_platform_url)
        assert client.holdings("1") == [{"name": "HTML fallback holding"}]
        html_fallback.assert_called_once()

    def test_portfolio_is_cached_across_calls(self, base_platform_url, mocker):
        rest_mock = mocker.patch(
            "easy_equities_client.accounts.clients.fetch_portfolio_overview",
            return_value={"investmentAccounts": []},
        )
        client = EasyEquitiesAccountsClient(base_platform_url)
        client.list()
        client.list()
        rest_mock.assert_called_once()
