from dataclasses import dataclass, fields

@dataclass(frozen=True)
class EvaluationResult:
    """
    Outputs a % score for each event category.

    Attention: Immutable post init, all values must be in [0, 1].
    """
    space_weather: float
    jamming: float
    spoofing: float
    equipment_fault: float
    multi_path: float

    def __post_init__(self) -> None:
        # Frozen: values are checked once here and cannot change afterwards (NaN is rejected too).
        for field in fields(self):
            value = getattr(self, field.name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field.name} must be in [0, 1], got {value!r}")