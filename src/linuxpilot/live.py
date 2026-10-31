"""`linuxpilot watch`: re-run checks on an interval until the user presses Ctrl+C.

Nothing is left running afterwards: this is an ordinary foreground loop.
"""

from __future__ import annotations

import time
from typing import Callable, TextIO

from linuxpilot.runner import RunResult

# ANSI sequences used only when writing to a real terminal.
_ALT_SCREEN_ON = "\033[?1049h"  # like `top`: restore the previous screen on exit
_ALT_SCREEN_OFF = "\033[?1049l"
_HOME_CLEAR = "\033[H\033[J"
_CURSOR_HIDE = "\033[?25l"
_CURSOR_SHOW = "\033[?25h"


def watch(
    provider: Callable[[], RunResult],
    render: Callable[[RunResult], str],
    out: TextIO,
    interval: float,
    *,
    interactive: bool,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> None:
    """Run ``provider`` every ``interval`` seconds and write ``render(result)``.

    interactive=True redraws one screen in place (for terminals).
    interactive=False appends each result (for pipes and log files).
    Raises KeyboardInterrupt to the caller when the user presses Ctrl+C.
    """
    if interactive:
        out.write(_ALT_SCREEN_ON + _CURSOR_HIDE)
        out.flush()
    try:
        cycles = 0
        while True:
            result = provider()
            text = render(result)
            if interactive:
                stamp = time.strftime("%H:%M:%S")
                footer = f"\nLive: updated {stamp}, refreshing every {interval:g}s. Press Ctrl+C to stop.\n"
                out.write(_HOME_CLEAR + text + footer)
            else:
                out.write(text)
            out.flush()
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return
            sleep(interval)
    finally:
        if interactive:
            out.write(_CURSOR_SHOW + _ALT_SCREEN_OFF)
            out.flush()
