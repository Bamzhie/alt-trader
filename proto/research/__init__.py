"""Isolated research substrate.

This package is the only writer of the research database under
``data/research/``. It reads ``data/bars/`` read-only and never imports or
opens the legacy live code or databases (``proto.quant.engine``,
``quant.db``, ``signals.db``). Nothing here is part of the live scanner,
watcher, or service path.
"""
