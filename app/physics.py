"""Free-fall physics for an animal falling belly-down through air.

Model
-----
Quadratic drag:  m dv/dt = m g - 1/2 rho_air Cd A v^2
  terminal velocity      v_t = sqrt(2 m g / (rho_air Cd A))
  speed after distance y v(y) = v_t * sqrt(1 - exp(-2 g y / v_t^2))
  speed after time t     v(t) = v_t * tanh(g t / v_t)

Body geometry is estimated from mass (and length, when known) by treating the
animal as a flattened box of water-density tissue falling belly-down.

Injury likelihood is a heuristic log-logistic curve on impact speed. The 50 %
point is anchored to human fall data (~16 m/s, roughly a 13 m fall) and scaled
with body mass^(-1/4), which reproduces Haldane's observation that small animals
shrug off falls that kill large ones (and broadly matches feline high-rise data).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

G = 9.81            # m/s^2
RHO_AIR = 1.225     # kg/m^3, sea level
MU_AIR = 1.81e-5    # Pa*s, dynamic viscosity
RHO_BODY = 1000.0   # kg/m^3, ~water
FLATNESS = 0.6      # body height / body width when spread belly-down
ASPECT = 3.0        # body length / width when length is unknown

HUMAN_MASS = 70.0
HUMAN_V50 = 16.0    # m/s impact speed with ~50 % severe injury/death (adult human)
MASS_EXPONENT = -0.25
RISK_STEEPNESS = 6.0


@dataclass
class FallResult:
    mass_kg: float
    length_m: float
    width_m: float
    area_m2: float
    cd: float
    reynolds: float
    v_t: float              # terminal velocity, m/s
    t_99: float             # time to reach 99 % of v_t, s
    d_99: float             # distance to reach 99 % of v_t, m
    vacuum_height: float    # drop height reaching v_t with no drag, m
    impact_energy_j: float
    v50: float              # impact speed for 50 % severe injury, m/s
    injury_prob: float      # at terminal velocity, 0..1
    h50: float | None       # fall height for 50 % severe injury (None = never reached)
    h10: float | None       # fall height for 10 % severe injury

    # -- curves ---------------------------------------------------------------
    def speed_at_distance(self, y: float) -> float:
        return self.v_t * math.sqrt(max(0.0, 1.0 - math.exp(-2 * G * y / self.v_t ** 2)))

    def speed_at_time(self, t: float) -> float:
        return self.v_t * math.tanh(G * t / self.v_t)

    def injury_at_speed(self, v: float) -> float:
        return injury_probability(v, self.v50)

    def injury_at_height(self, h: float) -> float:
        return injury_probability(self.speed_at_distance(h), self.v50)


def body_dimensions(mass_kg: float, length_m: float | None = None) -> tuple[float, float, float]:
    """Return (length, width, belly-down frontal area) for a body of the given mass."""
    volume = mass_kg / RHO_BODY
    cube = volume ** (1 / 3)
    # Ignore lengths that are implausible for the mass (e.g. a length that includes a long tail
    # is fine, but a value off by orders of magnitude is a parsing slip).
    width = math.sqrt(volume / (FLATNESS * length_m)) if length_m else 0.0
    # Use the length only if it's consistent with the mass: an animal can't be wider than it is
    # long. Family-level articles (e.g. "Squirrel") can pair a small species' length with a big
    # species' mass; then fall back to a typical body shape.
    if length_m and width <= length_m and length_m <= 40 * cube:
        length = length_m
    else:
        width = (volume / (FLATNESS * ASPECT)) ** (1 / 3)
        length = ASPECT * width
    return length, width, length * width


def injury_probability(v: float, v50: float) -> float:
    if v <= 0:
        return 0.0
    return 1.0 / (1.0 + (v50 / v) ** RISK_STEEPNESS)


def speed_for_probability(p: float, v50: float) -> float:
    return v50 * (p / (1 - p)) ** (1 / RISK_STEEPNESS)


def height_for_speed(v: float, v_t: float) -> float | None:
    if v >= v_t:
        return None
    return -(v_t ** 2) / (2 * G) * math.log(1 - (v / v_t) ** 2)


MIN_MASS, MAX_MASS = 1e-9, 1e6          # 1 microgram .. 1000 tonnes
MAX_LENGTH = 100.0                       # m
MAX_CD = 10.0


def _positive(value, name: str, upper: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(v) or v <= 0 or v > upper:
        raise ValueError(f"{name} must be between 0 and {upper:g}")
    return v


def compute(mass_kg: float, length_m: float | None = None, cd: float | None = None) -> FallResult:
    """Compute the fall. If cd is None, a Reynolds-dependent bluff-body Cd (1.0 + 24/Re) is used.

    Raises ValueError for non-numeric, non-finite or out-of-range inputs.
    """
    mass_kg = _positive(mass_kg, "mass", MAX_MASS)
    if mass_kg < MIN_MASS:
        raise ValueError(f"mass must be at least {MIN_MASS:g} kg")
    try:
        length_m = _positive(length_m, "length", MAX_LENGTH) if length_m else None
    except ValueError:
        length_m = None                  # a bad length just falls back to the typical body shape
    if cd is not None:
        cd = _positive(cd, "drag coefficient", MAX_CD)
    length, width, area = body_dimensions(mass_kg, length_m)

    def vt_for(c: float) -> float:
        return math.sqrt(2 * mass_kg * G / (RHO_AIR * c * area))

    if cd is None:
        c = 1.0
        for _ in range(30):  # fixed-point iteration on Re
            re = RHO_AIR * vt_for(c) * width / MU_AIR
            c_new = 1.0 + 24.0 / max(re, 1e-9)
            if abs(c_new - c) < 1e-6:
                c = c_new
                break
            c = c_new
    else:
        c = cd
    v_t = vt_for(c)
    re = RHO_AIR * v_t * width / MU_AIR

    t_99 = v_t / G * math.atanh(0.99)
    d_99 = -(v_t ** 2) / (2 * G) * math.log(1 - 0.99 ** 2)
    v50 = HUMAN_V50 * (mass_kg / HUMAN_MASS) ** MASS_EXPONENT

    return FallResult(
        mass_kg=mass_kg, length_m=length, width_m=width, area_m2=area, cd=c, reynolds=re,
        v_t=v_t, t_99=t_99, d_99=d_99, vacuum_height=v_t ** 2 / (2 * G),
        impact_energy_j=0.5 * mass_kg * v_t ** 2,
        v50=v50, injury_prob=injury_probability(v_t, v50),
        h50=height_for_speed(v50, v_t),
        h10=height_for_speed(speed_for_probability(0.10, v50), v_t),
    )


def risk_label(p: float) -> str:
    if p < 0.05:
        return "Very low"
    if p < 0.25:
        return "Low"
    if p < 0.6:
        return "Moderate"
    if p < 0.9:
        return "High"
    return "Very high"
