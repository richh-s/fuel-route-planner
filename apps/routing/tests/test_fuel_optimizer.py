import random
from dataclasses import dataclass

from django.test import SimpleTestCase

from apps.routing.services.fuel_optimizer import InfeasibleRouteError, plan_fuel_stops


@dataclass(frozen=True)
class Stop:
    mile_marker: float
    price: float


def plan(stations, total, initial, tank=500, mpg=10):
    return plan_fuel_stops(stations, total, tank_range_miles=tank, miles_per_gallon=mpg, initial_fuel_miles=initial)


class FuelOptimizerTests(SimpleTestCase):
    def test_no_stop_when_starting_fuel_covers_trip(self):
        result = plan([Stop(100, 3.0)], total=400, initial=500)
        self.assertEqual(result.stops, ())
        self.assertEqual(result.total_cost, 0)

    def test_buys_only_what_is_needed_to_finish(self):
        # Full tank covers 500 of 700 miles; the only station is at mile 300.
        result = plan([Stop(300, 3.0)], total=700, initial=500)
        self.assertEqual(len(result.stops), 1)
        # Arrive at mile 300 with 200 miles left in the tank, need 400 more to finish -> buy 200 miles = 20 gal.
        self.assertAlmostEqual(result.stops[0].gallons, 20)
        self.assertAlmostEqual(result.total_cost, 60)

    def test_prefers_cheaper_station_further_ahead(self):
        stations = [Stop(100, 4.0), Stop(450, 3.0)]
        result = plan(stations, total=900, initial=500)
        # Skip the expensive station and buy everything at the cheap one.
        self.assertEqual([s.station.mile_marker for s in result.stops], [450])
        self.assertAlmostEqual(result.stops[0].gallons, 40)  # arrive with 50 mi, need 450 more

    def test_fills_up_at_cheap_station_before_expensive_stretch(self):
        stations = [Stop(0, 3.0), Stop(400, 5.0), Stop(800, 5.0)]
        result = plan(stations, total=1000, initial=0)
        first = result.stops[0]
        self.assertEqual(first.station.mile_marker, 0)
        self.assertAlmostEqual(first.gallons, 50)  # full tank at the cheapest price
        # Then only the minimum is bought at the expensive stations.
        self.assertAlmostEqual(result.total_gallons_purchased, 100)
        self.assertAlmostEqual(result.total_cost, 50 * 3 + 50 * 5)

    def test_matches_hand_computed_optimum(self):
        stations = [Stop(0, 3.5), Stop(200, 3.0), Stop(450, 4.0), Stop(600, 2.5)]
        result = plan(stations, total=1000, initial=0)
        # Optimal: 20 gal @3.5 to reach mile 200, 40 gal @3.0 to reach the cheaper station at 600
        # (skipping the $4 one), then 40 gal @2.5 to finish.
        self.assertEqual([s.station.mile_marker for s in result.stops], [0, 200, 600])
        self.assertAlmostEqual(result.total_cost, 20 * 3.5 + 40 * 3.0 + 40 * 2.5)
        self.assertAlmostEqual(result.total_gallons_purchased, 100)

    def test_raises_when_gap_exceeds_range(self):
        with self.assertRaises(InfeasibleRouteError) as ctx:
            plan([Stop(100, 3.0), Stop(700, 3.0)], total=1000, initial=500)
        self.assertEqual(ctx.exception.stranded_at_mile, 100)

    def test_raises_when_no_station_reachable_from_origin(self):
        with self.assertRaises(InfeasibleRouteError):
            plan([Stop(300, 3.0)], total=400, initial=100)

    def test_ignores_stations_beyond_destination(self):
        result = plan([Stop(300, 3.0), Stop(900, 1.0)], total=700, initial=500)
        self.assertEqual([s.station.mile_marker for s in result.stops], [300])


def brute_force_cost(stations, total, tank, initial):
    """Exhaustive DP over integer fuel levels (exact when all distances are integers)."""
    inf = float("inf")
    positions = [0, *[s.mile_marker for s in stations], total]
    prices = [inf, *[s.price for s in stations]]
    best = [[inf] * (tank + 1) for _ in positions]
    best[0][initial] = 0.0
    for i in range(len(positions) - 1):
        for fuel in range(tank + 1):
            if best[i][fuel] == inf:
                continue
            for bought in range(0, tank - fuel + 1):
                if bought and prices[i] == inf:
                    break
                cost = best[i][fuel] + bought * (prices[i] if bought else 0)
                left = fuel + bought - (positions[i + 1] - positions[i])
                if left >= 0 and cost < best[i + 1][left]:
                    best[i + 1][left] = cost
    return min(best[-1])


class OptimalityTests(SimpleTestCase):
    def test_matches_brute_force_on_random_routes(self):
        rng = random.Random(42)
        checked = 0
        for _ in range(300):
            total = rng.randint(12, 45)
            miles = sorted(rng.sample(range(total), rng.randint(2, min(12, total))))
            stations = [Stop(m, rng.choice([2.5, 2.9, 3.0, 3.1, 3.4, 3.9])) for m in miles]
            initial = rng.randint(0, 10)
            expected = brute_force_cost(stations, total, tank=10, initial=initial)
            if expected == float("inf"):
                with self.assertRaises(InfeasibleRouteError):
                    plan(stations, total, initial, tank=10, mpg=1)
                continue
            result = plan(stations, total, initial, tank=10, mpg=1)
            self.assertAlmostEqual(result.total_cost, expected, places=6)
            checked += 1
        self.assertGreater(checked, 100)

    def test_stop_penalty_avoids_stopping_to_save_pennies(self):
        # We can only just reach mile 390. Station 2 is 10 miles on and $0.01 cheaper, so pure cost
        # says: buy half a gallon at 390, then fill up at 400. That saves ~$0.48 for an extra stop.
        stations = [Stop(390, 3.00), Stop(400, 2.99)]
        cheapest = plan(stations, total=880, initial=395)
        pragmatic = plan_fuel_stops(stations, 880, 500, 10, 395, stop_penalty=15)

        self.assertEqual(len(cheapest.stops), 2)
        self.assertEqual(len(pragmatic.stops), 1)
        self.assertLess(pragmatic.total_cost - cheapest.total_cost, 15)
