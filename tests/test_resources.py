from types import SimpleNamespace

import pytest

from cvbench.resources import (
    ResourceMonitor,
    cgroup_v2_path,
    parse_cpu_stat,
    parse_docker_stats,
    parse_io_stat,
    parse_size,
)


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


def test_external_cgroup_v2_accounting_never_executes_in_submitted_image(
    tmp_path, monkeypatch
) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    (proc_root / "123").mkdir(parents=True)
    (proc_root / "123" / "cgroup").write_text("0::/docker/test\n")
    group = cgroup_root / "docker/test"
    group.mkdir(parents=True)
    (group / "cpu.stat").write_text("usage_usec 1000000\n")
    (group / "io.stat").write_text("8:0 rbytes=10 wbytes=20 rios=1 wios=1\n")
    (group / "memory.current").write_text("1024\n")
    (group / "memory.max").write_text("2048\n")
    (group / "memory.peak").write_text("1536\n")
    (group / "pids.current").write_text("3\n")
    cidfile = tmp_path / "container.cid"
    cidfile.write_text("container-id")
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="123\n", stderr="")

    monkeypatch.setattr("cvbench.resources.subprocess.run", fake_run)
    monitor = ResourceMonitor(
        SimpleNamespace(pid=1),
        cidfile=cidfile,
        proc_root=proc_root,
        cgroup_root=cgroup_root,
    )
    assert monitor.capture_checkpoint()
    (group / "cpu.stat").write_text("usage_usec 1200000\n")
    assert monitor.capture_checkpoint()
    monitor.finalize_accounting()
    summary = monitor.summary(1)

    assert cgroup_v2_path("0::/docker/test\n", cgroup_root) == group
    assert parse_io_stat("8:0 rbytes=4 wbytes=5\n") == (4, 5)
    assert summary["cpu_time_seconds"] == 1.2
    assert summary["peak_ram_bytes"] == 1536
    assert summary["accounting_scope"] == "container_cgroup_v2_external"
    assert summary["authoritative"] is True
    assert all(sample.get("accounting_source") == "host_cgroup_v2" for sample in summary["over_time"])
    assert commands == [["docker", "inspect", "--format", "{{.State.Pid}}", "container-id"]]
    assert not any("exec" in command for command in commands)
