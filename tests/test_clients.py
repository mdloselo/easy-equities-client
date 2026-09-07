import pytest

from easy_equities_client import constants
from easy_equities_client.clients import MfaRequiredError


class TestPlatformClient:
    def test_login(
        self, platform_client, base_platform_url, mock_success_login_response
    ):
        mock_success_login_response(base_platform_url)
        assert platform_client.login("username", "password") is True

    def test_login_requires_mfa(
        self, platform_client, base_platform_url, mock_mfa_login_response
    ):
        mock_mfa_login_response(base_platform_url)
        with pytest.raises(MfaRequiredError):
            platform_client.login("username", "password")

    def test_verify_mfa(
        self,
        platform_client,
        base_platform_url,
        mock_mfa_login_response,
        mock_verify_mfa_response,
    ):
        mfa_url = mock_mfa_login_response(base_platform_url)
        with pytest.raises(MfaRequiredError):
            platform_client.login("username", "password")
        mock_verify_mfa_response(base_platform_url, mfa_url, valid_code="123456")
        assert platform_client.verify_mfa("123456") is True

    def test_verify_mfa_invalid_code(
        self,
        platform_client,
        base_platform_url,
        mock_mfa_login_response,
        mock_verify_mfa_response,
    ):
        mfa_url = mock_mfa_login_response(base_platform_url)
        with pytest.raises(MfaRequiredError):
            platform_client.login("username", "password")
        mock_verify_mfa_response(base_platform_url, mfa_url, valid_code="123456")
        with pytest.raises(Exception, match="Invalid verification code"):
            platform_client.verify_mfa("000000")


class TestEasyEquitiesClient:
    def test_login(self, easy_equities_client, mock_success_login_response):
        mock_success_login_response(constants.EASY_EQUITIES_BASE_PLATFORM_URL)
        assert easy_equities_client.login("username", "password") is True


class TestSatrixClient:
    def test_login(self, satrix_client, mock_success_login_response):
        mock_success_login_response(constants.SATRIX_BASE_PLATFORM_URL)
        assert satrix_client.login("username", "password") is True
