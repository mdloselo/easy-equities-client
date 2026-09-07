import pytest
from requests import Session

from easy_equities_client.accounts.rest import (
    PortfolioApiError,
    account_id_from_account_number,
    fetch_portfolio_overview,
    get_portfolio_access_token,
    rest_account_to_account,
    rest_assets_to_holdings,
)
from easy_equities_client.accounts.types import Account


class TestAccountIdFromAccountNumber:
    def test_regular_trust_account(self):
        # Matches the id AccountsClient's HTML-scraping already uses for
        # this account, so a linked account keeps the same row either way.
        assert account_id_from_account_number("EE1744408-7944922") == "7944922"

    def test_non_numbered_product(self):
        # EasyCrypto and similar EasyEquities-family products aren't a
        # numbered trust account at all.
        assert account_id_from_account_number("NBF523") == "NBF523"


class TestRestAccountToAccount:
    def test_maps_fields(self):
        account = rest_account_to_account(
            {
                "accountNumber": "EE1744408-7944922",
                "productName": "EasyEquities ZAR",
                "productId": 2,
            }
        )
        assert account == Account(
            id="7944922", name="EasyEquities ZAR", trading_currency_id="2"
        )

    def test_missing_product_id(self):
        account = rest_account_to_account(
            {"accountNumber": "NBF523", "productName": "EasyCrypto"}
        )
        assert account.trading_currency_id == ""


class TestRestAssetsToHoldings:
    def test_maps_and_formats_currency(self):
        holdings = rest_assets_to_holdings(
            {
                "currencySymbol": "R",
                "assets": [
                    {
                        "assetName": "Kibo Energy PLC",
                        "contractCode": "EQU.ZA.KBO",
                        "assetCode": "IE00B97C0C31",
                        "purchaseValue": 2490.68,
                        "currentValue": 2605.45,
                        "currentPrice": 64.08,
                        "units": 200.0123,
                        "imageUri": "aHR0cHM6Ly9leGFtcGxlLmNvbS9sb2dvLnBuZw==",
                    }
                ],
            }
        )
        assert holdings == [
            {
                "name": "Kibo Energy PLC",
                "contract_code": "EQU.ZA.KBO",
                "purchase_value": "R2 490.68",
                "current_value": "R2 605.45",
                "current_price": "R64.08",
                "isin": "IE00B97C0C31",
                "shares": "200.0123",
                "img": "https://example.com/logo.png",
            }
        ]

    def test_negative_value(self):
        holdings = rest_assets_to_holdings(
            {
                "currencySymbol": "$",
                "assets": [
                    {
                        "assetName": "Losing position",
                        "contractCode": "X",
                        "assetCode": "X",
                        "purchaseValue": 1,
                        "currentValue": -0.11,
                        "currentPrice": 1,
                        "units": 1,
                    }
                ],
            }
        )
        assert holdings[0]["current_value"] == "-$0.11"

    def test_no_assets(self):
        assert rest_assets_to_holdings({"currencySymbol": "R", "assets": []}) == []


class TestGetPortfolioAccessToken:
    def test_raises_when_not_a_redirect(self, requests_mock):
        requests_mock.get(
            "https://identity.openeasy.io/connect/authorize",
            status_code=200,
            text="not logged in",
        )
        with pytest.raises(PortfolioApiError):
            get_portfolio_access_token(Session())

    def test_raises_when_token_exchange_fails(self, requests_mock):
        requests_mock.get(
            "https://identity.openeasy.io/connect/authorize",
            status_code=302,
            headers={
                "Location": "https://portfolio-overview.apps.easyequities.io/auth/callback?code=abc&state=xyz"
            },
        )
        requests_mock.post(
            "https://identity.openeasy.io/connect/token",
            status_code=200,
            json={"error": "invalid_grant"},
        )
        with pytest.raises(PortfolioApiError):
            get_portfolio_access_token(Session())

    def test_returns_access_token(self, requests_mock):
        requests_mock.get(
            "https://identity.openeasy.io/connect/authorize",
            status_code=302,
            headers={
                "Location": "https://portfolio-overview.apps.easyequities.io/auth/callback?code=abc&state=xyz"
            },
        )
        requests_mock.post(
            "https://identity.openeasy.io/connect/token",
            status_code=200,
            json={"access_token": "test-token"},
        )
        assert get_portfolio_access_token(Session()) == "test-token"


class TestFetchPortfolioOverview:
    def test_raises_on_non_200(self, requests_mock, mocker):
        mocker.patch(
            "easy_equities_client.accounts.rest.get_portfolio_access_token",
            return_value="test-token",
        )
        requests_mock.get(
            "https://rest.synatic.openeasy.io/easyequities/portfolios/v3/portfolio-overview",
            status_code=500,
        )
        with pytest.raises(PortfolioApiError):
            fetch_portfolio_overview(Session())

    def test_returns_json(self, requests_mock, mocker):
        mocker.patch(
            "easy_equities_client.accounts.rest.get_portfolio_access_token",
            return_value="test-token",
        )
        requests_mock.get(
            "https://rest.synatic.openeasy.io/easyequities/portfolios/v3/portfolio-overview",
            status_code=200,
            json={"investmentAccounts": []},
        )
        assert fetch_portfolio_overview(Session()) == {"investmentAccounts": []}
