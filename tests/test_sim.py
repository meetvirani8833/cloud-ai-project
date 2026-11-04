import numpy as np
import pytest

from cloudopt.data import Workload
from cloudopt.forecast.baselines import moving_max, persistence, seasonal_naive
from cloudopt.sim.policies import Oracle, Predictive, Reactive, StaticPeak
from cloudopt.sim.simulator import SimConfig, choose_replica_vcpu, simulate, summarize


def make_wl(series: np.ndarray) -> Workload:
    series = np.atleast_2d(series).astype(float)
    n = series.shape[0]
    return Workload(np.array([f"app_{i}" for i in range(n)]), np.array(["t"] * n),
                    series, series.copy(), series.copy(), np.full_like(series, 100.0))


def test_oracle_never_violates_and_is_cheapest():
    rng = np.random.default_rng(0)
    y = rng.gamma(2.0, 2.0, size=(5, 4032))
    wl, cfg = make_wl(y), SimConfig()
    oracle = simulate(Oracle(), wl, cfg, 3456, 4032)
    static = simulate(StaticPeak(), wl, cfg, 3456, 4032)
    assert not oracle.violation.any()
    assert oracle.cost.sum() < static.cost.sum()


def test_static_cost_is_constant_per_slot():
    y = np.tile(np.linspace(1, 10, 288), 14)
    res = simulate(StaticPeak(), make_wl(y), SimConfig(), 3456, 4032)
    assert np.all(res.replicas == res.replicas[:, :1])


def test_reactive_lags_a_step_increase():
    y = np.full(4032, 2.0)
    y[3600:] = 20.0
    res = simulate(Reactive(), make_wl(y), SimConfig(), 3456, 4032, replica_vcpu=np.array([1.0]))
    t = 3600 - 3456
    assert res.violation[0, t]  # first slot of the surge: capacity sized for old demand
    assert not res.violation[0, t + 1]  # one slot later it has caught up


class _ProvisionForCurrentSlot:
    """Requests capacity for the current slot's demand with no look-ahead."""

    name = "current"

    def decide(self, ctx):
        return ctx.replicas_for(ctx.wl.demand_peak[:, ctx.t])


def test_provision_delay_postpones_scale_up_but_not_scale_down():
    y = np.full(4032, 2.0)
    y[3600:3700] = 20.0
    cfg = SimConfig(provision_delay=2)
    res = simulate(_ProvisionForCurrentSlot(), make_wl(y), cfg, 3456, 4032, replica_vcpu=np.array([1.0]))
    t = 3600 - 3456
    assert res.replicas[0, t] == res.replicas[0, t - 1]  # requested, not yet running
    assert res.replicas[0, t + 2] > res.replicas[0, t - 1]  # arrived after the delay
    assert res.replicas[0, 3700 - 3456] < res.replicas[0, 3699 - 3456]  # immediate scale-down


def test_forecasters_use_only_past_data():
    y = np.arange(20, dtype=float)[None, :]
    assert persistence(y, horizon=2)[0, 10] == 8
    assert seasonal_naive(y, season=5)[0, 10] == 5
    assert moving_max(y, horizon=1, window=3)[0, 10] == 9


def test_predictive_with_perfect_forecast_matches_oracle():
    rng = np.random.default_rng(1)
    y = rng.gamma(2.0, 2.0, size=(3, 4032))
    wl, cfg = make_wl(y), SimConfig()
    a = simulate(Predictive(y.copy()), wl, cfg, 3456, 4032)
    b = simulate(Oracle(), wl, cfg, 3456, 4032)
    assert a.cost.sum() == pytest.approx(b.cost.sum())


def test_replica_sizing_leaves_room_to_scale():
    cfg = SimConfig()
    sizes = choose_replica_vcpu(np.array([0.3, 3.0, 100.0]), cfg)
    assert list(sizes) == [0.25, 1.0, 4.0]
    assert summarize(simulate(Oracle(), make_wl(np.full(4032, 5.0)), cfg, 3456, 4032))["violation_rate"] == 0


def test_oracle_covers_provision_delay():
    rng = np.random.default_rng(2)
    y = rng.gamma(2.0, 2.0, size=(4, 4032))
    res = simulate(Oracle(), make_wl(y), SimConfig(provision_delay=2), 3456, 4032)
    assert not res.violation.any()


def test_static_policies_do_not_churn_after_warmup():
    y = np.tile(np.linspace(1, 10, 288), 14)
    res = simulate(StaticPeak(), make_wl(y), SimConfig(provision_delay=1), 3456, 4032)
    assert (np.diff(res.replicas, axis=1) == 0).all()
