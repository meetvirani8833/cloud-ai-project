"""Load the processed Azure Functions workload as dense (apps x slots) matrices."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from cloudopt import config


@dataclass
class Workload:
    """Per-app 5-minute demand series, aligned on a common slot axis.

    demand_mean / demand_peak are in busy workers (concurrent executions): the 5-minute
    mean and the busiest minute inside each 5-minute slot.
    """

    app_ids: np.ndarray
    tenant_ids: np.ndarray
    demand_mean: np.ndarray  # (apps, slots)
    demand_peak: np.ndarray  # (apps, slots)
    rps: np.ndarray  # (apps, slots)
    mem_mb: np.ndarray  # (apps, slots)

    @property
    def n_apps(self) -> int:
        return len(self.app_ids)

    @property
    def n_slots(self) -> int:
        return self.demand_mean.shape[1]

    def subset(self, mask: np.ndarray) -> "Workload":
        return Workload(
            self.app_ids[mask],
            self.tenant_ids[mask],
            self.demand_mean[mask],
            self.demand_peak[mask],
            self.rps[mask],
            self.mem_mb[mask],
        )


def _matrix(df: pd.DataFrame, col: str, app_ids: np.ndarray) -> np.ndarray:
    return df.pivot(index="app_id", columns="slot", values=col).reindex(app_ids).to_numpy(np.float64)


def load_workload(scaling_study: bool = True) -> Workload:
    """Load all selected apps, or only those with enough demand to be worth scaling."""
    apps = pd.read_parquet(config.DATA_PROCESSED / "azure_apps.parquet")
    five = pd.read_parquet(config.DATA_PROCESSED / "azure_app_5min.parquet")
    app_ids = apps["app_id"].to_numpy()
    wl = Workload(
        app_ids=app_ids,
        tenant_ids=apps["tenant_id"].to_numpy(),
        demand_mean=_matrix(five, "concurrency", app_ids),
        demand_peak=_matrix(five, "concurrency_peak", app_ids),
        rps=_matrix(five, "rps", app_ids),
        mem_mb=_matrix(five, "mem_mb", app_ids),
    )
    if scaling_study:
        median = np.median(wl.demand_mean[:, config.TRAIN], axis=1)
        wl = wl.subset(median >= config.MIN_MEDIAN_CONCURRENCY)
    return wl
