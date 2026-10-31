"""Built-in checks. Importing this package registers them, in display order."""

from linuxpilot.checks import system  # noqa: F401
from linuxpilot.checks import cpu  # noqa: F401
from linuxpilot.checks import memory  # noqa: F401
from linuxpilot.checks import disk  # noqa: F401
from linuxpilot.checks import services  # noqa: F401
from linuxpilot.checks import network  # noqa: F401
from linuxpilot.checks import pi  # noqa: F401
