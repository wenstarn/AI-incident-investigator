import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import ValidationError

from fault_injection.models import PARAMETER_MODELS, Fault, FaultCreate


class FaultConflictError(Exception):
    pass


class FaultValidationError(Exception):
    pass


class FaultRegistry:
    def __init__(
        self,
        allowed_types: set[str],
        clock: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not allowed_types <= PARAMETER_MODELS.keys():
            raise ValueError("Unknown allowed fault type")
        self._allowed_types = allowed_types.copy()
        self._clock = clock
        self._utc_now = utc_now
        self._lock = threading.RLock()
        self._faults: dict[str, tuple[Fault, float | None]] = {}

    def _expire(self, now: float) -> None:
        for fault_id, (_, deadline) in list(self._faults.items()):
            if deadline is not None and deadline <= now:
                del self._faults[fault_id]

    def set_fault(self, request: FaultCreate) -> Fault:
        model = PARAMETER_MODELS.get(request.type)
        if model is None or request.type not in self._allowed_types:
            raise FaultValidationError("Fault type is not allowed")
        try:
            parameters = model.model_validate(request.parameters).model_dump()
        except ValidationError as error:
            raise FaultValidationError("Invalid fault parameters") from error
        with self._lock:
            now = self._clock()
            self._expire(now)
            if any(fault.type == request.type for fault, _ in self._faults.values()):
                raise FaultConflictError("Fault type is already active")
            created_at = self._utc_now()
            fault = Fault(
                fault_id=uuid4().hex,
                type=request.type,
                parameters=parameters,
                created_at=created_at,
                expires_at=(
                    created_at + timedelta(seconds=request.ttl_seconds)
                    if request.ttl_seconds is not None
                    else None
                ),
            )
            self._faults[fault.fault_id] = (
                fault,
                now + request.ttl_seconds if request.ttl_seconds is not None else None,
            )
            return fault.model_copy(deep=True)

    def get_fault(self, fault_id: str) -> Fault | None:
        with self._lock:
            self._expire(self._clock())
            item = self._faults.get(fault_id)
            return item[0].model_copy(deep=True) if item else None

    def get_active_faults(self) -> list[Fault]:
        with self._lock:
            self._expire(self._clock())
            return [fault.model_copy(deep=True) for fault, _ in self._faults.values()]

    def get_fault_by_type(self, fault_type: str) -> Fault | None:
        with self._lock:
            self._expire(self._clock())
            for fault, _ in self._faults.values():
                if fault.type == fault_type:
                    return fault.model_copy(deep=True)
            return None

    def remove_fault(self, fault_id: str) -> None:
        with self._lock:
            self._faults.pop(fault_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._faults.clear()
