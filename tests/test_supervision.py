"""Supervision Debian en lecture seule : arbres /sys et /proc factices + exécuteur de commandes factice."""
import pytest

from mcpanel.errors import AppError
from mcpanel.supervision import LOG_UNITS, Supervision, WATCHED_UNITS


@pytest.fixture
def fake_fs(tmp_path):
    sysd, proc = tmp_path / "sys", tmp_path / "proc"
    z = sysd / "class/thermal/thermal_zone0"
    z.mkdir(parents=True)
    (z / "temp").write_text("52000\n")
    (z / "type").write_text("x86_pkg_temp\n")
    bad = sysd / "class/thermal/thermal_zone1"
    bad.mkdir()
    (bad / "temp").write_text("pas-un-nombre\n")
    h = sysd / "class/hwmon/hwmon3"
    h.mkdir(parents=True)
    (h / "name").write_text("coretemp\n")
    (h / "temp1_input").write_text("47500\n")
    (h / "temp1_label").write_text("Package id 0\n")
    for n, st in (("eth0", "up"), ("lo", "unknown")):
        d = sysd / "class/net" / n
        d.mkdir(parents=True)
        (d / "operstate").write_text(st + "\n")
    (proc / "net").mkdir(parents=True)
    (proc / "net/dev").write_text("Inter-|   Receive\n face |bytes\n"
                                  "    lo: 100 1 0 0 0 0 0 0 100 1 0 0 0 0 0 0\n"
                                  "  eth0: 5000 10 0 0 0 0 0 0 7000 12 0 0 0 0 0 0\n"
                                  " veth12: 1 1 0 0 0 0 0 0 1 1 0 0 0 0 0 0\n")
    (proc / "self").mkdir()
    (proc / "self/status").write_text("Name:\tpython\nVmRSS:\t  51200 kB\n")
    return sysd, proc


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, timeout=4.0):
        self.calls.append(cmd)
        if cmd[0] == "ip":
            return 0, "2: eth0    inet 192.168.1.3/24 brd 192.168.1.255 scope global eth0\n"
        if cmd[:2] == ["systemctl", "show"]:
            if cmd[2] == "ssh.service":
                return 0, "LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled\n"
            if cmd[2] == "minecraft-panel.service":
                return 0, "LoadState=loaded\nActiveState=failed\nSubState=failed\nUnitFileState=enabled\n"
            return 0, "LoadState=not-found\nActiveState=inactive\n"
        if cmd[0] == "journalctl":
            return 0, "\n".join(f"2026-10-30T10:00:{i:02d}+0100 host x[1]: ligne {i}\x07" for i in range(50))
        return 127, "inconnu"


@pytest.fixture
def sup(panel, fake_fs):
    r = Runner()
    return Supervision(panel, fake_fs[0], fake_fs[1], r), r


def test_temperatures_both_sources_and_bad_values_skipped(sup):
    t = {x["label"]: x["celsius"] for x in sup[0].temperatures()}
    assert t == {"x86_pkg_temp": 52.0, "coretemp Package id 0": 47.5}


def test_temperatures_absent_is_empty_not_error(panel, tmp_path):
    assert Supervision(panel, tmp_path / "vide", tmp_path / "vide").temperatures() == []


def test_network_skips_virtual_and_computes_rates(sup):
    s, _ = sup
    first = s.network()
    assert [n["name"] for n in first] == ["eth0"] and first[0]["addresses"] == ["192.168.1.3/24"] and first[0]["state"] == "up"
    assert first[0]["rx_per_s"] == 0
    s.proc.joinpath("net/dev").write_text(s.proc.joinpath("net/dev").read_text().replace("5000 10", "9000 10"))
    import time
    time.sleep(0.05)
    assert s.network()[0]["rx_per_s"] > 0


def test_services_hide_missing_units_and_report_failures(sup):
    out = {u["unit"]: u for u in sup[0].services()}
    assert set(out) == {"ssh.service", "minecraft-panel.service"} and out["minecraft-panel.service"]["active"] == "failed"


def test_commands_have_fixed_arguments_no_shell(sup):
    s, r = sup
    s.services()
    s.logs("ssh.service", 20)
    s.network()
    for c in r.calls:
        assert isinstance(c, list) and c[0] in ("systemctl", "ip", "journalctl")
    assert all(c[2] in WATCHED_UNITS for c in r.calls if c[:2] == ["systemctl", "show"])


def test_logs_whitelist_clamp_and_sanitize(sup):
    s, r = sup
    for bad in ("sshd.service; rm -rf /", "../../etc/passwd", "cron.service", ""):
        with pytest.raises(AppError):
            s.logs(bad)
    out = s.logs(LOG_UNITS[0], 100000)
    assert out["available"] and len(out["lines"]) == 50 and "\x07" not in out["lines"][0]
    assert r.calls[-1][r.calls[-1].index("-n") + 1] == "300"


def test_logs_unavailable_without_journal_access(panel, fake_fs):
    s = Supervision(panel, fake_fs[0], fake_fs[1], lambda cmd, timeout=4.0: (1, "Hint: You are currently not seeing messages from other users"))
    out = s.logs("ssh.service")
    assert out["available"] is False and "systemd-journal" in out["hint"]


def test_overview_has_everything_and_survives_missing_tools(panel, fake_fs, make_server):
    make_server()
    s = Supervision(panel, fake_fs[0], fake_fs[1], lambda cmd, timeout=4.0: (127, "absent"))
    o = s.overview()
    assert set(o) == {"system", "temperatures", "network", "services", "processes", "panel"}
    assert o["services"] == [] and o["panel"]["rss_mb"] == 50
    assert {"cpu_percent", "mem", "disk_data", "uptime"} <= set(o["system"])


