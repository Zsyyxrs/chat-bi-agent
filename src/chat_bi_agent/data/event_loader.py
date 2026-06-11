"""Load and parse event YAML files."""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import yaml


class EventLoaderError(ValueError):
    """Raised when an event YAML fails schema validation."""


@dataclass
class RequiredPopulation:
    """种子数据契约：事件落地所需的最小客户群、必持产品、必备交易。"""

    min_customers: int
    must_hold: list[dict]
    branches: list[str] | None = None
    tiers: list[str] | None = None
    branch_levels: list[str] | None = None
    must_have_transactions: dict | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "RequiredPopulation":
        min_customers = data.get("min_customers")
        if not isinstance(min_customers, int) or min_customers < 1:
            raise EventLoaderError(
                f"required_population.min_customers must be int >= 1, got {min_customers!r}"
            )

        must_hold = data.get("must_hold")
        if not isinstance(must_hold, list) or not must_hold:
            raise EventLoaderError(
                "required_population.must_hold must be a non-empty list"
            )

        return cls(
            min_customers=min_customers,
            must_hold=must_hold,
            branches=data.get("branches") or None,
            tiers=data.get("tiers") or None,
            branch_levels=data.get("branch_levels") or None,
            must_have_transactions=data.get("must_have_transactions") or None,
        )


@dataclass
class Event:
    """事件定义：包含触发日期、受影响维度、传导规则、评估标准。"""

    id: str
    name: str
    type: str  # PRODUCT_EXPIRY, MARKETING_EVENT, MACRO_EVENT, SEASONAL_EVENT
    date: date
    description: str
    affected_dimensions: dict[str, list[str]] = field(default_factory=dict)
    propagation: list[dict] = field(default_factory=list)
    expected_rca_conclusion: str = ""
    expected_main_dimensions: list[dict] = field(default_factory=list)
    required_population: RequiredPopulation | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        """从 YAML dict 构造事件。"""
        if isinstance(data["date"], str):
            event_date = date.fromisoformat(data["date"])
        else:
            event_date = data["date"]

        rp_data = data.get("required_population")
        required_population = (
            RequiredPopulation.from_dict(rp_data) if rp_data else None
        )

        return cls(
            id=data["id"],
            name=data["name"],
            type=data.get("type", "UNKNOWN"),
            date=event_date,
            description=data.get("description", ""),
            affected_dimensions=data.get("affected_dimensions", {}),
            propagation=data.get("propagation", []),
            expected_rca_conclusion=data.get("expected_rca_conclusion", ""),
            expected_main_dimensions=data.get("expected_main_dimensions", []),
            required_population=required_population,
        )


class EventLoader:
    """加载和解析事件库 YAML 文件。"""

    def __init__(self, events_dir: Optional[Path] = None):
        if events_dir is None:
            events_dir = Path(__file__).parent / "events"
        self.events_dir = events_dir

    def load_all_events(self) -> list[Event]:
        """加载 events/ 目录下所有的 *.yaml 文件。"""
        events = []
        if not self.events_dir.exists():
            return events

        for yaml_file in sorted(self.events_dir.glob("*.yaml")):
            try:
                events.extend(self.load_events_from_file(yaml_file))
            except Exception as e:
                print(f"Warning: Failed to load {yaml_file}: {e}")

        return events

    def load_events_from_file(self, filepath: Path) -> list[Event]:
        """从单个 YAML 文件加载事件列表。"""
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or "events" not in data:
            return []

        events = []
        for event_dict in data["events"]:
            event = Event.from_dict(event_dict)  # let EventLoaderError propagate
            events.append(event)

        return events

    def get_events_by_date_range(
        self, start_date: date, end_date: date
    ) -> list[Event]:
        """获取在指定日期范围内的事件。"""
        all_events = self.load_all_events()
        return [e for e in all_events if start_date <= e.date <= end_date]

    def get_event_by_id(self, event_id: str) -> Optional[Event]:
        """按 ID 获取事件。"""
        all_events = self.load_all_events()
        for event in all_events:
            if event.id == event_id:
                return event
        return None
