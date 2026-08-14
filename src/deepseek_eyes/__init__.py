"""DeepSeek Eyes — Windows-first local vision extension for coding agents.

Core chain: register a source image, obtain a ``source_ref``, observe it through
the Xiaomi MiMo Token Plan, and return tainted structured evidence. Also ships
screen capture, an OpenCode host-adapter bridge, and a PySide6 Control Center.
"""

__version__ = "0.1.0"

# Stable contract identifiers used to key the exact observation cache and to
# reject mismatched provider payloads.
CONTRACT_VERSION = "v4.3"
VISION_SCHEMA_VERSION = "1"
