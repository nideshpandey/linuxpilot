import json
import subprocess
import sys

from linuxpilot.cli import EXIT_CRITICAL, EXIT_OK, EXIT_USAGE, EXIT_WARNING, _exit_code, build_parser, main
from linuxpilot.context import Context
from linuxpilot.models import Finding, Status, worst_status
from linuxpilot.registry import Check, all_checks, select_checks
from linuxpilot.render import render_human, render_json
from linuxpilot.runner import run_checks


def f(status, check_id="x.y"):
    return Finding(check_id, "X", status, "summary")


def test_worst_status_ignores_not_applicable():
    assert worst_status([f(Status.OK), f(Status.UNSUPPORTED), f(Status.SKIPPED)]) is Status.OK
    assert worst_status([f(Status.OK), f(Status.WARNING)]) is Status.WARNING
    assert worst_status([f(Status.WARNING), f(Status.CRITICAL), f(Status.UNKNOWN)]) is Status.CRITICAL
    assert worst_status([f(Status.UNSUPPORTED)]) is Status.UNKNOWN


def test_exit_codes():
    assert _exit_code([f(Status.OK)]) == EXIT_OK
    assert _exit_code([f(Status.OK), f(Status.WARNING)]) == EXIT_WARNING
    assert _exit_code([f(Status.WARNING), f(Status.CRITICAL)]) == EXIT_CRITICAL
    # Unknown means "could not inspect", not "the system is broken".
    assert _exit_code([f(Status.UNKNOWN), f(Status.UNSUPPORTED)]) == EXIT_OK


def test_finding_to_dict_is_json_serialisable():
    d = Finding("a.b", "A", Status.WARNING, "s", details={"n": 1}, limitations=["x"]).to_dict()
    assert d["status"] == "warning"
    json.dumps(d)


def test_check_ids_are_unique_and_categorised():
    checks = all_checks()
    ids = [c.id for c in checks]
    assert len(ids) == len(set(ids))
    assert all("." in c.id and c.description for c in checks)


def test_select_checks_by_category_and_id():
    checks = all_checks()
    only_disk = select_checks(checks, only=["disk"])
    assert {c.category for c in only_disk} == {"disk"}
    one = select_checks(checks, only=["cpu.temperature"])
    assert [c.id for c in one] == ["cpu.temperature"]
    no_net = select_checks(checks, skip=["network"])
    assert not any(c.category == "network" for c in no_net)


def test_runner_turns_crash_into_unknown():
    def boom(ctx):
        raise RuntimeError("kaboom")

    res = run_checks(Context(), [Check("t.crash", "Crash", "t", boom)])
    assert res.findings[0].status is Status.UNKNOWN
    assert "kaboom" in res.findings[0].limitations[0]


def test_runner_skips_network_checks_when_disabled():
    called = []

    def net(ctx):
        called.append(1)
        return f(Status.OK)

    res = run_checks(Context(network=False), [Check("t.net", "Net", "t", net, network=True)])
    assert res.findings[0].status is Status.SKIPPED
    assert called == []


def test_runner_accepts_lists_and_keeps_order():
    checks = [
        Check("a.one", "One", "a", lambda c: [f(Status.OK, "a.one"), f(Status.WARNING, "a.one")]),
        Check("b.two", "Two", "b", lambda c: f(Status.OK, "b.two")),
    ]
    res = run_checks(Context(), checks)
    assert [x.check_id for x in res.findings] == ["a.one", "a.one", "b.two"]


def test_render_human_lists_problems_with_next_steps():
    problem = Finding("d.u", "Disk /", Status.CRITICAL, "97% used", reason="It is full.", recommendation="Clean up.")
    text = render_human([f(Status.OK), problem, f(Status.UNSUPPORTED)], 0.2, color=False)
    assert "Needs attention" in text and "Clean up." in text and "It is full." in text
    assert "Overall: CRITICAL" in text
    assert "1 check(s) not applicable" in text
    assert "\033[" not in text  # no colour codes when colour is off


def test_render_human_ascii_mode():
    text = render_human([f(Status.WARNING)], 0.1, unicode=False)
    assert "[WARN]" in text and "✔" not in text


def test_render_json_structure():
    data = json.loads(render_json([f(Status.WARNING)], 0.123))
    assert data["overall_status"] == "warning"
    assert data["findings"][0]["check_id"] == "x.y"
    assert {"tool", "version", "hostname", "duration_seconds"} <= data.keys()


def test_flags_work_before_and_after_subcommand():
    p = build_parser()
    assert p.parse_args(["--json", "status"]).json is True
    assert p.parse_args(["status", "--json"]).json is True
    assert p.parse_args(["status"]).json is False
    assert p.parse_args(["--only", "disk"]).only == ["disk"]


def test_usage_errors_exit_64(capsys):
    assert main(["status", "--only", "nonexistent"]) == EXIT_USAGE
    try:
        main(["--definitely-not-a-flag"])
    except SystemExit as exc:
        assert exc.code == EXIT_USAGE


def test_list_checks_json(capsys):
    assert main(["list-checks", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert any(c["id"] == "disk.usage" for c in data)


def test_one_shot_check_stays_lightweight():
    """A normal check must not load the dashboard, the interactive view or anything outside the stdlib."""
    code = (
        "import io, sys\n"
        "from linuxpilot.cli import main\n"
        "main(['--quick', '--no-network'], out=io.StringIO())\n"
        "heavy = ['linuxpilot.tui', 'linuxpilot.dashboard', 'linuxpilot.dashboard_assets',\n"
        "         'linuxpilot.live', 'http.server', 'termios', 'webbrowser']\n"
        "print(','.join(m for m in heavy if m in sys.modules))\n"
        "import importlib.metadata as md\n"
        "print(md.requires('linuxpilot') and [r for r in md.requires('linuxpilot') if 'extra' not in r])\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    loaded, deps = proc.stdout.splitlines()
    assert loaded == "", f"one-shot check loaded heavy modules: {loaded}"
    assert deps in ("None", "[]"), f"unexpected runtime dependencies: {deps}"


def test_end_to_end_on_this_host_produces_valid_json():
    """Integration test: runs real checks on the test machine; asserts structure only."""
    proc = subprocess.run(
        [sys.executable, "-m", "linuxpilot", "status", "--json", "--no-network", "--quick"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode in (0, 1, 2), proc.stderr
    data = json.loads(proc.stdout)
    statuses = {s.value for s in Status}
    assert all(item["status"] in statuses for item in data["findings"])
    assert not any(item["check_id"].startswith("network.dns") and item["status"] != "skipped"
                   for item in data["findings"])
