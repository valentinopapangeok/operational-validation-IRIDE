from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Delivery:
    product: str
    delivery_id: str
    revision: str  # Content hash, version ID, or verified composite asset fingerprint.
    uri: str
    acquired_at: str


@dataclass
class Outcome:
    status: str
    reason: str
    reference_status: str = 'UNKNOWN'
    samples: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)


class Adapter(Protocol):
    """One product adapter owns reference search, download, QA and collocation.

    Return PENDING_REFERENCE, REJECTED_QA, ERROR, or COMPLETE.
    COMPLETE must contain evidence-backed sample units and metrics. Registry
    transactions start only after adapter work, so interruptions remain retryable.
    """
    def validate(self, delivery: Delivery, config: dict, workdir) -> Outcome: ...
