"""Provider-agnostic pricing models for container replicas.

A replica costs vcpu * vcpu_hour + memory_gb * gb_hour per hour. Presets are approximate
public on-demand list prices for Linux containers in a US region; they exist so the
dashboard can switch providers. Verify current prices before quoting them in the report.
Any custom or reseller table can be added as another PricingModel.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PricingModel:
    key: str
    label: str
    vcpu_hour: float  # USD per vCPU-hour
    gb_hour: float  # USD per GB-hour

    def replica_hour(self, vcpu: float, memory_gb: float) -> float:
        return vcpu * self.vcpu_hour + memory_gb * self.gb_hour

    def to_dict(self) -> dict:
        return asdict(self)


PRESETS: dict[str, PricingModel] = {
    p.key: p
    for p in [
        PricingModel("aws_fargate", "AWS Fargate (approx.)", 0.04048, 0.004445),
        PricingModel("azure_aci", "Azure Container Instances (approx.)", 0.0486, 0.0054),
        PricingModel("gcp_cloud_run", "GCP Cloud Run, instance billing (approx.)", 0.0648, 0.0072),
    ]
}
DEFAULT_PRICING = PRESETS["aws_fargate"]
