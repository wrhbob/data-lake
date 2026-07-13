from app.crawler_platform.host_rate_limiter import HostLimiter


def test_host_limiter_delays_second_request_for_same_host():
    current_time = [100.0]
    sleeps = []

    def clock():
        return current_time[0]

    def sleeper(seconds):
        sleeps.append(seconds)
        current_time[0] += seconds

    limiter = HostLimiter(clock=clock, sleeper=sleeper, jitter=lambda _seconds: 0.0)

    limiter.wait("zjj.deyang.gov.cn", min_delay_seconds=5)
    current_time[0] += 1
    limiter.wait("zjj.deyang.gov.cn", min_delay_seconds=5)

    assert sleeps == [4.0]


def test_host_limiter_tracks_hosts_independently():
    current_time = [100.0]
    sleeps = []
    limiter = HostLimiter(
        clock=lambda: current_time[0],
        sleeper=lambda seconds: sleeps.append(seconds),
        jitter=lambda _seconds: 0.0,
    )

    limiter.wait("zjj.deyang.gov.cn", min_delay_seconds=5)
    current_time[0] += 1
    limiter.wait("example.gov.cn", min_delay_seconds=5)

    assert sleeps == []
