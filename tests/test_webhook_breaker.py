import time

from app.services.webhook_breaker import CircuitBreaker


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker(fail_threshold=3)
        assert cb.get_state("http://example.com/hook") == "closed"

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(fail_threshold=3, window_seconds=300)
        url = "http://example.com/hook"
        for _ in range(3):
            cb.on_failure(url)
        assert cb.get_state(url) == "open"
        assert cb.allow_request(url) is False

    def test_allows_request_before_threshold(self):
        cb = CircuitBreaker(fail_threshold=3)
        url = "http://example.com/hook"
        cb.on_failure(url)
        cb.on_failure(url)
        assert cb.allow_request(url) is True

    def test_closes_after_success(self):
        cb = CircuitBreaker(fail_threshold=2)
        url = "http://example.com/hook"
        cb.on_failure(url)
        cb.on_failure(url)
        assert cb.get_state(url) == "open"
        cb.reset(url)
        cb.on_success(url)
        assert cb.get_state(url) == "closed"

    def test_half_open_probe_on_reset(self):
        cb = CircuitBreaker(fail_threshold=2, reset_seconds=0)
        url = "http://example.com/hook"
        cb.on_failure(url)
        cb.on_failure(url)
        assert cb.get_state(url) == "open"
        time.sleep(0.1)
        assert cb.allow_request(url) is True
        assert cb.get_state(url) == "half_open"

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(fail_threshold=2, reset_seconds=0)
        url = "http://example.com/hook"
        cb.on_failure(url)
        cb.on_failure(url)
        time.sleep(0.1)
        cb.allow_request(url)
        cb.on_failure(url)
        assert cb.get_state(url) == "open"

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(fail_threshold=2, reset_seconds=0)
        url = "http://example.com/hook"
        cb.on_failure(url)
        cb.on_failure(url)
        time.sleep(0.1)
        cb.allow_request(url)
        cb.on_success(url)
        assert cb.get_state(url) == "closed"

    def test_only_one_probe_in_half_open(self):
        cb = CircuitBreaker(fail_threshold=2, reset_seconds=0)
        url = "http://example.com/hook"
        cb.on_failure(url)
        cb.on_failure(url)
        time.sleep(0.1)
        assert cb.allow_request(url) is True
        assert cb.allow_request(url) is False

    def test_no_failure_budget_consumed_no_cross_host_contamination(self):
        cb = CircuitBreaker(fail_threshold=3)
        url1 = "http://host1.com/hook"
        url2 = "http://host2.com/hook"
        for _ in range(3):
            cb.on_failure(url1)
        assert cb.allow_request(url1) is False
        assert cb.allow_request(url2) is True

    def test_reset_clears_state(self):
        cb = CircuitBreaker(fail_threshold=2)
        url = "http://example.com/hook"
        cb.on_failure(url)
        cb.on_failure(url)
        cb.reset(url)
        assert cb.get_state(url) == "closed"
        assert cb.allow_request(url) is True
