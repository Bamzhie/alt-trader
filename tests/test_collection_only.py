"""Collection-only loop: no scanner/store imports, no signals.db writes."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import collection  # noqa: E402


class Counts(dict):
    def __init__(self, values, errors=None, stopped=False):
        super().__init__(values)
        self.errors = errors or {}
        self.stopped = stopped


def main():
    calls = []

    def fake_bars(**kw):
        calls.append(("bars", kw["data_dir"]))
        return Counts({"BTC": 2, "ETH": 1})

    def fake_funding(**kw):
        calls.append(("funding", kw["data_dir"]))
        return {"BTC": 1}

    def fake_settlements(**kw):
        calls.append(("settlements", kw["data_dir"]))
        return {"BTC": {"new": 3}}

    def fake_prices(**kw):
        calls.append(("prices", kw["data_dir"]))
        return {"mark": {"BTC": {"new": 4}}}

    collection.colmod.collect_full_universe = fake_bars
    collection.fmod.collect_funding = fake_funding
    collection.smod.maybe_collect = fake_settlements
    collection.pmod.maybe_collect = fake_prices

    forbidden = {"proto.scan", "proto.store", "proto.app", "proto.daemon",
                 "proto.measurement", "proto.outcomes", "proto.service"}
    leaked = sorted(forbidden & set(sys.modules))
    assert not leaked, f"collection imported legacy runtime: {leaked}"

    with tempfile.TemporaryDirectory() as td:
        data = os.path.join(td, "bars")
        state = collection.CollectionState(
            data, os.path.join(td, "funding"),
            os.path.join(td, "funding_settlements"),
            os.path.join(td, "prices"), every_s=1)
        out = collection.run_collection_loop(
            state, iterations=1, data_dir=data,
            funding_dir=state.funding_dir,
            settlements_dir=state.settlements_dir,
            prices_dir=state.prices_dir)

        assert out["status"] == "healthy", out
        assert out["bars"]["coins"] == 2, out
        assert out["bars"]["new_bars"] == 3, out
        assert out["funding"]["result"]["new_rows"] == 1, out
        assert out["settlements"]["result"]["new_rows"] == 3, out
        assert out["prices"]["result"]["new_rows"] == 4, out
        assert [c[0] for c in calls] == ["bars", "funding", "settlements",
                                        "prices"], calls
        assert not os.path.exists(os.path.join(td, "signals.db"))
        assert not os.path.exists(os.path.join(td, "quant.db"))

    print("ALL PASS collection-only loop")


if __name__ == "__main__":
    main()
