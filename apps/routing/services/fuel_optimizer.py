"""Cheapest refuelling plan along a fixed route (the "gas station problem").

Model: stations sit at mile markers along the route; the tank holds
`tank_range_miles` worth of fuel; each station has a price per gallon.

Minimizing money alone tends to produce silly plans (stop 11 miles later to
buy 1 gallon that is $0.006 cheaper), so every stop also costs a fixed
`stop_penalty` in the objective. The penalty only steers the choice of stops;
the reported cost is the real money spent. With `stop_penalty=0` the plan is
the exact cost minimum.

Algorithm (Khuller, Malekian & Mestre, "To Fill or not to Fill"): an optimal
plan only ever does one of two things at a stop u before driving to the next
stop v:
  * buy just enough to reach v with an empty tank (when v is not pricier), or
  * fill the tank completely (when v is pricier).
So the fuel on arrival at v is either 0, what was left from the start, or
`tank - distance(w, v)` for some earlier station w. That keeps the number of
states per station small and allows an exact dynamic program over
(station, fuel on arrival).

Everything is tracked in "miles of fuel" and converted to gallons at the end.
This module is pure Python with no Django dependencies.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

_EPSILON = 1e-7


class RoutedStation(Protocol):
    mile_marker: float

    @property
    def price(self) -> float: ...


@dataclass(frozen=True)
class FuelStop:
    station: RoutedStation
    gallons: float
    cost: float
    fuel_on_arrival_gallons: float


@dataclass(frozen=True)
class FuelPlan:
    stops: tuple[FuelStop, ...]
    total_gallons_purchased: float
    total_cost: float


class InfeasibleRouteError(Exception):
    """Raised when some stretch of the route is longer than the vehicle's range."""

    def __init__(self, stranded_at_mile: float, next_station_mile: float | None):
        self.stranded_at_mile = stranded_at_mile
        self.next_station_mile = next_station_mile
        super().__init__(f"No station reachable from mile {stranded_at_mile:.1f}.")


@dataclass(frozen=True)
class _Label:
    """A partial plan: arrived at `station` with `fuel` miles in the tank."""

    station: int  # index into the station list; -1 = origin, len(stations) = destination
    fuel: float
    score: float  # money + stop penalties (what we minimize)
    parent: "_Label | None"
    bought_at_parent: float  # miles of fuel bought at the parent station before driving here


@dataclass(frozen=True)
class _Step:
    """Extends a partial plan by buying fuel at its station and driving to the next one."""

    label: _Label
    cost_per_mile: float
    stop_penalty: float

    def to(self, target: int, bought: float, fuel_on_arrival: float) -> _Label:
        penalty = self.stop_penalty if bought > _EPSILON else 0.0
        score = self.label.score + bought * self.cost_per_mile + penalty
        return _Label(target, fuel_on_arrival, score, self.label, bought)


def plan_fuel_stops(
    stations: Sequence[RoutedStation],
    total_miles: float,
    tank_range_miles: float,
    miles_per_gallon: float,
    initial_fuel_miles: float,
    stop_penalty: float = 0.0,
) -> FuelPlan:
    """Return the best set of fuel purchases that gets the vehicle to `total_miles`.

    `stations` must be sorted by mile marker.
    """
    usable = [s for s in stations if 0 <= s.mile_marker < total_miles]
    tank = tank_range_miles
    start_fuel = min(initial_fuel_miles, tank)
    origin = _Label(-1, start_fuel, 0.0, None, 0.0)

    if start_fuel >= total_miles - _EPSILON:
        return FuelPlan((), 0.0, 0.0)

    # labels[i] maps fuel-on-arrival -> best partial plan reaching station i with that fuel.
    labels: list[dict[float, _Label]] = [{} for _ in usable]

    def relax(target: int, label: _Label) -> None:
        key = round(label.fuel, 6)
        current = labels[target].get(key)
        if current is None or label.score < current.score - _EPSILON:
            labels[target][key] = label

    for index, station in enumerate(usable):
        if station.mile_marker > start_fuel + _EPSILON:
            break
        relax(index, _Label(index, start_fuel - station.mile_marker, 0.0, origin, 0.0))

    best_finish: _Label | None = None

    for u, station in enumerate(usable):
        cost_per_mile = station.price / miles_per_gallon
        for label in list(labels[u].values()):
            fuel = label.fuel
            step = _Step(label, cost_per_mile, stop_penalty)

            remaining = total_miles - station.mile_marker
            if remaining <= tank + _EPSILON:
                finish = step.to(len(usable), max(0.0, remaining - fuel), max(0.0, fuel - remaining))
                if best_finish is None or finish.score < best_finish.score:
                    best_finish = finish

            for v in range(u + 1, len(usable)):
                distance = usable[v].mile_marker - station.mile_marker
                if distance > tank + _EPSILON:
                    break
                # Buy just enough to reach v (or nothing if we already can).
                bought = max(0.0, distance - fuel)
                relax(v, step.to(v, bought, fuel + bought - distance))
                # Fill up here because v is more expensive.
                if station.price < usable[v].price and tank - fuel > _EPSILON:
                    relax(v, step.to(v, tank - fuel, tank - distance))

    if best_finish is None:
        reachable = [i for i, found in enumerate(labels) if found]
        stranded = usable[reachable[-1]].mile_marker if reachable else 0.0
        following = (reachable[-1] + 1) if reachable else 0
        next_mile = usable[following].mile_marker if following < len(usable) else None
        raise InfeasibleRouteError(stranded, next_mile)

    return _build_plan(best_finish, usable, miles_per_gallon)


def _build_plan(finish: _Label, stations: Sequence[RoutedStation], miles_per_gallon: float) -> FuelPlan:
    stops: list[FuelStop] = []
    label = finish
    while label.parent is not None:
        parent = label.parent
        if label.bought_at_parent > _EPSILON and parent.station >= 0:
            station = stations[parent.station]
            gallons = label.bought_at_parent / miles_per_gallon
            stops.append(FuelStop(station, gallons, gallons * station.price, parent.fuel / miles_per_gallon))
        label = parent
    stops.reverse()
    return FuelPlan(
        stops=tuple(stops),
        total_gallons_purchased=sum(stop.gallons for stop in stops),
        total_cost=sum(stop.cost for stop in stops),
    )
