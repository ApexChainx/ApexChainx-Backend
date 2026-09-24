"""Generates a real Stellar ed25519 keypair address instead of a fake
33-character 'G' + uuid string that fails the platform's own validator.
"""
import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from app.utils.stellar_checksum import crc16_xmodem

ACCOUNT_ID_VERSION_BYTE = 6 << 3  # Stellar StrKey "G..." version byte.


def generate_stellar_public_key() -> str:
    """Return a valid 56-char Stellar public key from a fresh ed25519 keypair."""
    private_key = Ed25519PrivateKey.generate()
    raw_public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = bytes([ACCOUNT_ID_VERSION_BYTE]) + raw_public_key
    checksum = crc16_xmodem(payload).to_bytes(2, byteorder="little")
    return base64.b32encode(payload + checksum).decode("ascii").rstrip("=")
