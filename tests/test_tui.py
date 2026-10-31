"""Tests for the interactive terminal view (state, keys and drawing; no real terminal needed)."""

import re

from linuxpilot.models import Finding, Status
from linuxpilot.render import bar, meter_percent, sparkline, verdict
from linuxpilot.runner import RunResult
from linuxpilot.tui import (
    DOWN, END, ENTER, ESC, HOME, UP, View, build_frame, parse_keys,
)

ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")


def findings(disk=Status.WARNING):
    return RunResult([
        Finding("system.info", "System", Status.OK, "Debian 12", details={"os": "Debian 12", "uptime_human": "3h"}),
        Finding("cpu.usage", "CPU usage", Status.OK, "12% busy", details={"busy_percent": 12.0}),
        Finding("memory.usage", "Memory", Status.OK, "40% used", details={"available_percent": 60.0, "total_bytes": 8 * 1024**3}),
        Finding("disk.usage", "Disk /", disk, "91% used", details={"used_percent": 91.0},
                reason="It is nearly full.", recommendation="Look: `sudo du -xh / | sort -h` then `df -h`."),
        Finding("pi.power", "Pi power", Status.UNSUPPORTED, "not a Raspberry Pi"),
    ], 0.4)


def ready_view(**kw):
    v = View(**kw)
    v.apply(findings(), now=1000.0)
    return v


# ------------------------------------------------------------------ keys


def test_parse_keys():
    assert parse_keys("\x1b[A\x1b[B\x1b[C\x1b[D") == ["up", "down", "right", "left"]
    assert parse_keys("\x1bOA") == ["up"]
    assert parse_keys("jk\r q") == ["j", "k", "enter", " ", "q"]
    assert parse_keys("\x1b") == ["esc"]
    assert parse_keys("\x7f\t") == ["backspace", "tab"]
    assert parse_keys("\x1b[5~\x1b[6~\x1b[H\x1b[F") == ["pgup", "pgdn", "home", "end"]
    assert parse_keys("\x1b[1;5Cx") == ["x"]  # unknown sequences are skipped


# ------------------------------------------------------------------ state


def test_first_load_opens_problems_and_selects_worst():
    v = ready_view()
    assert v.current().title == "Disk /"
    assert "disk.usage|Disk /" in v.expanded
    assert all(f.status.is_applicable for f in v.visible())  # N/A hidden by default


def test_navigation_is_bounded():
    v = ready_view()
    v.handle_key(HOME)
    assert v.selected == 0
    v.handle_key(UP)
    assert v.selected == 0
    v.handle_key(END)
    last = v.selected
    v.handle_key(DOWN)
    assert v.selected == last == len(v.visible()) - 1


def test_enter_toggles_details():
    v = ready_view()
    v.handle_key(HOME)
    k = "system.info|System"
    v.handle_key(ENTER)
    assert k in v.expanded
    v.handle_key(" ")
    assert k not in v.expanded
    v.handle_key("l")
    assert k in v.expanded
    v.handle_key("h")
    assert k not in v.expanded


def test_expand_all_and_collapse_all():
    v = ready_view()
    v.handle_key("e")
    assert {f"{f.check_id}|{f.title}" for f in v.visible()} <= v.expanded
    v.handle_key("e")
    assert not ({f"{f.check_id}|{f.title}" for f in v.visible()} & v.expanded)


def test_filter_cycles_and_counts():
    v = ready_view()
    v.handle_key("f")
    assert v.filter == "problems" and [f.title for f in v.visible()] == ["Disk /"]
    v.handle_key("f")
    assert v.filter == "ok" and all(f.status is Status.OK for f in v.visible())
    v.handle_key("f")
    assert v.filter == "all"
    v.handle_key("2")
    assert v.filter == "problems"


def test_search_typing_and_escape():
    v = ready_view()
    for k in ["/", "m", "e", "m"]:
        v.handle_key(k)
    assert v.searching and [f.title for f in v.visible()] == ["Memory"]
    v.handle_key("q")  # typing q while searching is text, not quit
    assert v.query == "memq" and v.visible() == []
    v.handle_key("backspace")
    v.handle_key(ENTER)
    assert not v.searching and v.query == "mem"
    assert v.handle_key(ESC) is None and v.query == ""  # first Esc clears the search
    assert v.handle_key(ESC) == "quit"  # second Esc quits


def test_quit_refresh_pause_and_speed():
    v = ready_view(interval=5)
    assert v.handle_key("q") == "quit"
    assert v.handle_key("r") == "refresh"
    assert v.handle_key("p") is None and v.paused
    assert v.handle_key("p") == "refresh" and not v.paused
    assert v.handle_key("+") == "reschedule" and v.interval == 10
    v.handle_key("-")
    v.handle_key("-")
    assert v.interval == 2
    v.handle_key("-")
    assert v.interval == 2  # minimum


def test_copy_returns_first_command():
    v = ready_view()
    assert v.handle_key("c") == ("copy", "sudo du -xh / | sort -h")
    v.handle_key(HOME)
    assert v.handle_key("c") is None
    assert "no command" in v.message


def test_help_closes_on_any_key():
    v = ready_view()
    v.handle_key("?")
    assert v.help
    assert v.handle_key("j") is None and not v.help
    v.handle_key("?")
    assert v.handle_key("q") == "quit"


def test_show_not_applicable():
    v = ready_view()
    v.handle_key("v")
    assert any(f.title == "Pi power" for f in v.visible())


def test_selection_follows_check_across_refresh_and_marks_changes():
    v = ready_view()
    v.handle_key(HOME)
    v.handle_key(DOWN)  # CPU usage
    v.apply(findings(disk=Status.CRITICAL), now=1005.0)
    assert v.current().title == "CPU usage"
    assert "disk.usage|Disk /" in v.changed


def test_history_records_metrics():
    v = ready_view()
    v.apply(findings(), now=1002.0)
    assert v.history["cpu"] == [12.0, 12.0]
    assert v.history["mem"] == [40.0, 40.0]
    assert v.history["temp"] == []  # no sensor in this data


# ------------------------------------------------------------------ drawing


def plain(lines):
    return [ANSI.sub("", line) for line in lines]


def test_frame_has_exact_size_and_key_parts():
    v = ready_view()
    lines = build_frame(v, 100, 30, color=True, unicode=True, now=1001.0)
    assert len(lines) == 30
    text = plain(lines)
    assert all(len(line) <= 100 for line in text)
    joined = "\n".join(text)
    assert "1 warning needs attention" in joined
    assert "Problems 1" in joined
    assert "Why:" in joined and "sudo du -xh" in joined
    assert "q quit" in text[-1]
    assert "`" not in joined  # backticks are turned into highlighted text


def test_frame_ascii_and_no_color():
    v = ready_view()
    lines = build_frame(v, 80, 24, color=False, unicode=False, now=1001.0)
    assert not any("\033[" in line for line in lines)
    assert all(ord(ch) < 128 for line in lines for ch in line)


def test_frame_scrolls_to_keep_selection_visible():
    v = ready_view()
    v.handle_key("e")  # open everything so the list is long
    v.handle_key(END)
    text = "\n".join(plain(build_frame(v, 80, 14, color=False, unicode=True, now=1001.0)))
    assert "Memory" in text  # the last check is on screen
    assert "more" in text  # and the screen says there is more above


def test_frame_too_small():
    lines = build_frame(ready_view(), 30, 8, color=False, unicode=True)
    assert len(lines) == 8 and "too small" in lines[0]


def test_frame_before_first_result():
    text = "\n".join(plain(build_frame(View(), 80, 20, color=False, unicode=True)))
    assert "Checking this machine" in text


# ------------------------------------------------------------------ shared render helpers


def test_verdict_text():
    assert verdict(findings().findings) == (Status.WARNING, "1 warning needs attention")
    assert verdict(findings(disk=Status.CRITICAL).findings)[1] == "1 critical problem needs attention"
    assert verdict(findings(disk=Status.OK).findings) == (Status.OK, "All clear")


def test_bar_and_sparkline():
    assert bar(0, 10, True) == ("", "░" * 10)
    assert bar(3, 10, True)[0] == "█"  # tiny but non-zero still shows
    assert bar(100, 10, False) == ("#" * 10, "")
    assert sparkline([0, 50, 100], True, 0, 100) == "▁▅█"
    assert len(sparkline([1, 2, 3, 4], False)) == 4


def test_meter_percent():
    f = findings().findings
    assert meter_percent(f[1]) == 12.0
    assert meter_percent(f[2]) == 40.0
    assert meter_percent(f[0]) is None
    ro = Finding("disk.usage", "Disk /media", Status.OK, "ro", details={"used_percent": 99, "read_only": True})
    assert meter_percent(ro) is None
