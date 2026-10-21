"""登录失败计数：按账号、按 IP 各自封顶，窗口过后自动解除。"""

from knowwork_server.identity.throttle import InMemoryLoginThrottle, Limits, account_key, ip_key


class Ticker:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_ip_limit_spans_accounts_and_window_expires():
    ticker = Ticker()
    throttle = InMemoryLoginThrottle(Limits(window_seconds=60, per_account=5, per_ip=3), clock=ticker)
    for index in range(3):
        throttle.record_failure((account_key(f"user{index}@x.com"), ip_key("10.0.0.1")))
    assert throttle.blocked((account_key("fresh@x.com"), ip_key("10.0.0.1")))
    assert not throttle.blocked((account_key("fresh@x.com"), ip_key("10.0.0.2")))
    ticker.now += 61
    assert not throttle.blocked((ip_key("10.0.0.1"),))


def test_prune_drops_stale_keys():
    ticker = Ticker()
    throttle = InMemoryLoginThrottle(Limits(window_seconds=60, per_account=5, per_ip=5), clock=ticker)
    throttle.MAX_TRACKED_KEYS = 2
    throttle.record_failure(("ip:a", "ip:b"))
    ticker.now += 120
    throttle.record_failure(("ip:c",))
    assert set(throttle._failures) == {"ip:c"}
