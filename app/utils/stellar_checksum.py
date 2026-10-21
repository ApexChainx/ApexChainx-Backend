"""CRC16-CCITT-XModem checksum verification for Stellar addresses, so
checksum-invalid keys are rejected instead of only grammar-checked.
"""


def crc16_xmodem(data: bytes) -> int:
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def has_valid_checksum(version_byte: bytes, payload: bytes, checksum: bytes) -> bool:
    expected = crc16_xmodem(version_byte + payload)
    actual = int.from_bytes(checksum, byteorder="little")
    return expected == actual
