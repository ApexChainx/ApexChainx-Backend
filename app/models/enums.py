from enum import Enum


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class OutageStatus(str, Enum):
    open = "open"
    resolved = "resolved"


class Role(str, Enum):
    admin = "admin"
    engineer = "engineer"
