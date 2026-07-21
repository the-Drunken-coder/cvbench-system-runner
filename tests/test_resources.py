import pytest

from cvbench.resources import parse_cpu_stat, parse_docker_stats, parse_size


def test_resource_size_parser() -> None:
    assert parse_size("1.5MiB") == 1.5 * 1024**2
    assert parse_size("2 GB") == 2_000_000_000
    with pytest.raises(ValueError):
        parse_size("not-a-size")


def test_docker_resource_data_parser() -> None:
    parsed = parse_docker_stats(
        {
            "CPUPerc": "12.5%",
            "MemUsage": "10MiB / 1GiB",
            "NetIO": "2kB / 3kB",
            "BlockIO": "4MB / 5MB",
            "PIDs": "7",
        }
    )
    assert parsed["cpu_percent"] == 12.5
    assert parsed["memory_bytes"] == 10 * 1024**2
    assert parsed["network_tx_bytes"] == 3000
    assert parsed["process_count"] == 7


def test_cgroup_cpu_time_parser() -> None:
    assert parse_cpu_stat("usage_usec 1250000\nuser_usec 1000000\n") == 1.25
    assert parse_cpu_stat("usage_nsec 2500000000\n") == 2.5
    assert parse_cpu_stat("user_usec 1\n") is None
