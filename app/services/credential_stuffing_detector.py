from time import time

from redis import Redis

from app.core.config import settings


class CredentialStuffingDetector:
    def __init__(self, redis_client: Redis | None = None):
        self.redis = redis_client or Redis.from_url(settings.CELERY_BROKER_URL)

    def _prefix_key(self, ip: str) -> str:
        return f"cred_stuffing:{ip}"

    def record_attempt(self, ip: str, password: str) -> None:
        prefix = password[:4]
        now = time()
        key = self._prefix_key(ip)
        window = settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60
        self.redis.zadd(key, {prefix: now})
        self.redis.zremrangebyscore(key, "-inf", now - window)
        self.redis.expire(key, int(window) + 60)

    def detect_stuffing(self, ip: str) -> bool:
        count = self.get_suspicious_ip_count(ip)
        return count > settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD

    def get_suspicious_ip_count(self, ip: str) -> int:
        now = time()
        window = settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60
        key = self._prefix_key(ip)
        self.redis.zremrangebyscore(key, "-inf", now - window)
        unique = self.redis.zrangebyscore(key, now - window, "+inf")
        return len(set(u.decode() if isinstance(u, bytes) else u for u in unique))


credential_stuffing_detector = CredentialStuffingDetector()
