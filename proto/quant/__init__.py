"""Quant trading module (read-only decision support; never places orders).

Pipeline:  candidates (event-time, broad) -> labeled in shadow (triple barrier,
net of costs) -> optional low-capacity scorer -> budgeted selector -> event
lifecycle + measurement.  Nothing is emitted as actionable unless a registered
rule has passed the promotion gate (proto.quant.gates) on untouched data.

Stdlib only, like the rest of the app.  Python 3.10+.
"""
