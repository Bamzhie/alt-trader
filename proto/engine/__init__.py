"""Frozen benchmark engine for the alt-radar quant substrate.

This package implements the staged benchmark contract (20 -> 50 -> 100+ coins)
on top of the existing research mirror and quant primitives. It NEVER reads
``data/bars/`` directly — all bar access goes through the research snapshot DB
(`proto.research.sync.load_snapshot_bars`). It NEVER writes to
``signals.db``, ``quant.db``, or any collector archive.

Modules:
  contract.py        frozen BenchmarkContract + hash
  universe_design.py  census, eligibility, strata, cohorts
  labeler.py          ROE-space triple barrier on bars
  decision.py         decision grid, outcome-independent selection
  features.py         point-in-time features at decision time
  models.py           baselines + engine scorers (stdlib)
  versions.py         engine version registry, freeze
  cycle.py            one cohort x one engine version -> per-coin outcomes
  evidence.py         pass count + baseline-matched statistics
  scheduler.py        8-hour sync + bounded dev-run queue
  __main__.py         python3 -m proto.engine <command>
"""
