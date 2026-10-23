import threading
import time
from collections import defaultdict

from app.services.webhook_service import WebhookDispatchLimiter


def test_dispatch_limiter_respects_global_and_per_webhook_caps():
    limiter = WebhookDispatchLimiter(global_limit=2, per_webhook_limit=1)
    active = 0
    max_active = 0
    per_webhook_active = defaultdict(int)
    max_per_webhook = defaultdict(int)
    lock = threading.Lock()

    def worker(webhook_id: str) -> None:
        nonlocal active, max_active
        with limiter.acquire(webhook_id):
            with lock:
                active += 1
                max_active = max(max_active, active)
                per_webhook_active[webhook_id] += 1
                max_per_webhook[webhook_id] = max(max_per_webhook[webhook_id], per_webhook_active[webhook_id])
            time.sleep(0.05)
            with lock:
                active -= 1
                per_webhook_active[webhook_id] -= 1

    threads = [threading.Thread(target=worker, args=(str(i % 2),)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_active <= 2
    assert max(max_per_webhook.values()) <= 1
