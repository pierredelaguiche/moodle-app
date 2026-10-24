"""Everything that needs the host operating system sits behind HostBridge, so a container or another OS can swap it."""
from .bridge import HostBridge, get_bridge  # noqa: F401
