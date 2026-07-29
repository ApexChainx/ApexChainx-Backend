"""Tests for the Stellar network-key separation guard script."""

from scripts.check_stellar_networks import check_network_key_separation


class TestCheckStellarNetworks:
    def test_testnet_with_testnet_key(self):
        env = {
            "STELLAR_NETWORK": "testnet",
            "STELLAR_POOL_SECRET_KEY": "SABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890ABCDEFGHIJKLMNOPQRSTUV",
        }
        errors = check_network_key_separation(env)
        assert errors == []

    def test_missing_network(self):
        errors = check_network_key_separation({})
        assert any("STELLAR_NETWORK is not set" in e for e in errors)

    def test_key_not_starting_with_s(self):
        env = {
            "STELLAR_NETWORK": "testnet",
            "STELLAR_POOL_SECRET_KEY": "TABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890ABCDEFGHIJKLMNOPQRSTUV",
        }
        errors = check_network_key_separation(env)
        assert any("must start with 'S'" in e for e in errors)

    def test_strict_testnet_horizon_mismatch(self):
        env = {
            "STELLAR_NETWORK": "testnet",
            "STELLAR_POOL_SECRET_KEY": "SABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890ABCDEFGHIJKLMNOPQRSTUV",
            "STELLAR_HORIZON_URL": "https://horizon-mainnet.stellar.org",
        }
        errors = check_network_key_separation(env, strict=True)
        assert any("HORIZON_URL points to mainnet" in e for e in errors)

    def test_strict_mainnet_horizon_mismatch(self):
        env = {
            "STELLAR_NETWORK": "mainnet",
            "STELLAR_POOL_SECRET_KEY": "SABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890ABCDEFGHIJKLMNOPQRSTUV",
            "STELLAR_HORIZON_URL": "https://horizon-testnet.stellar.org",
        }
        errors = check_network_key_separation(env, strict=True)
        assert any("HORIZON_URL points to testnet" in e for e in errors)

    def test_unknown_network(self):
        env = {
            "STELLAR_NETWORK": "unknown",
            "STELLAR_POOL_SECRET_KEY": "SABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890ABCDEFGHIJKLMNOPQRSTUV",
        }
        errors = check_network_key_separation(env)
        assert any("Unknown" in e for e in errors)
