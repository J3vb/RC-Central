"""Suggested part names for the Garage spec fields.

These are *hints* for an editable combo box, never a closed set: the Garage stores
plain strings and any value the user types is kept verbatim. suggestions() unions this
curated seed with whatever is already in the user's garage, so the list improves itself
and never has to be exhaustive to stay useful.

The curated entries are deliberately conservative - chassis, ESC and servo models come
from vendor documents verified while building the catalog, while motors and tires stay
at brand/series level because those product lines turn over fast and a wrong model name
is worse than a missing one. Add to these only from an official vendor source.
"""

from collections.abc import Iterable

CHASSIS = (
    "Yokomo YD-2",
    "Yokomo YD-2S",
    "Yokomo YD-2E",
    "Yokomo YD-2ZX",
    "Yokomo YD-2SX III",
    "Yokomo SD 2.0",
    "Yokomo SD 3.0",
    "Yokomo RD 1.0",
    "Yokomo RD 2.0",
    "MST RMX 2.5",
    "MST RMX 3.0",
    "MST RMX EX",
    "MST RMX 4",
    "MST RMX-M",
    "MST FXX 2.0",
    "MST FRX",
    "MST MRX",
    "Overdose GALM",
    "Overdose GALM ver.2",
    "Overdose Divall",
    "Overdose XEX",
    "Overdose Vacula",
    "Overdose Vacula 2",
    "Rêve D RDX",
    "Rêve D MC-3",
    "Onisiki Kodama",
    "D-Like Re-R HYBRID",
)

MOTORS = (
    "Acuvance Luxon",
    "Acuvance Xarvis",
    "Acuvance Xarvis XX",
    "Acuvance Agile",
    "Hobbywing XeRun V10",
    "Hobbywing XeRun Justock",
    "Muchmore FLETA ZX",
    "Yokomo Racing Performer",
    "Onisiki ONI6407",
    "Onisiki ONI6409",
    "Onisiki Hell Blaze",
    "G-Force",
    "Trinity",
    "Speed Passion",
)

ESCS = (
    "Hobbywing XeRun XR10 Justock",
    "Hobbywing XeRun XR10 Pro",
    "Acuvance Xarvis XX",
    "Acuvance Luxon",
    "Yokomo BL-RPX3",
    "Yokomo BL-RPX3 V2",
    "Yokomo BL-RPX4",
    "Yokomo BL-RPXS",
    "Rêve D ELITE",
    "KO Propo VFS-FR3",
    "G-Force TS120A R2",
    "G-Force TS150A",
    "SkyRC Toro TS120A",
    "SkyRC Toro TS150A",
    "Onisiki ONI4604",
    "Onisiki ONI4605",
    "Onisiki Hell Blaze",
    "Muchmore FLETA ZX",
    "Muchmore FLETA PRO V3",
    "Tekin RSX",
    "LRP Flow X",
)

SERVOS = (
    "Rêve D RS-ST",
    "Rêve D RS-ST PRO",
    "Sanwa PGS-CLE",
    "Sanwa PGS-HR",
    "Sanwa PGS-Servo 2",
    "KO Propo RSx3",
    "KO Propo RSx4S",
    "KO Propo BSx4S",
    "Yokomo SP-02D V2",
    "Yokomo SP-03D V2",
    "SRT D1S",
    "AGFRC A50BHL",
    "AGFRC SA30BHM",
    "Power HD",
    "Savox",
    "Futaba",
)

TIRES = (
    "Rêve D RT-01",
    "Rêve D RT-02",
    "Yokomo Zero-One R2",
    "Yokomo Super Drift",
    "DS Racing Finix",
    "MST",
    "Overdose",
    "HotRace",
)

CURATED = {
    "chassis": CHASSIS,
    "motor": MOTORS,
    "esc": ESCS,
    "servo": SERVOS,
    "tires": TIRES,
}


# Kit drivetrain gearing per chassis, keyed by the exact strings in CHASSIS above.
#
# THE RULE: a chassis appears here only if its internal ratio was read off an official
# vendor document (2026-07-20 sweep). That is the number a user cannot guess and the one
# that silently corrupts every FDR, rollout and top-speed figure downstream, so a missing
# entry is always better than an inferred one. Omit individual keys the same way: an
# absent "spur"/"pinion" means the kit's value was not stated, not that it is unknown-ish.
#
# All three vendors print the same formula the app implements in
# gearing.final_drive_ratio: MST "Spur / Pinion x Reduction Gear", Yokomo
# "2.6 x spur / pinion", Reve D "(spur / pinion) x 2.600" - so "internal ratio" maps
# onto our field 1:1.
#
# Chassis deliberately absent, and why:
#   Overdose XEX          - 2.00 is derived (assumes the 31T is a ratio-neutral idler),
#                           not printed. Weaker than the Yokomo values below, which solve
#                           exactly against Yokomo's own published table.
#   MST MRX               - variant-dependent: GT 6.593 vs S PRO 8.182.
#   Onisiki Kodama        - manual labels gears "Gear A/B/C", no tooth counts printed.
#   D-Like Re-R HYBRID    - spur 85T / pinion 22T are stated but the internal ratio is
#                           not; seeding gearing without it yields a wrong FDR.
#   Overdose GALM (v1),   - only ver.2 was verified. Vacula 2's diff pulley is documented
#     Vacula, Vacula 2      as "33T or 39T" (2.75 or 3.25), unresolvable from the manual.
#   Reve D MC-3           - conversion kit reusing the RDX gearcase; no ratio of its own.
CHASSIS_GEARING = {
    # Yokomo: "2nd ration 2.6" printed on the gear-ratio chart of the YD-2/S/E/SX III
    # manuals; the rest solve to 2.6 exactly against Yokomo's own ratio tables.
    # (Their 84T table has a typo - the 30T cell prints 6.28 where the maths gives 7.28 -
    # so take the ratio, never individual cells.)
    "Yokomo YD-2": {"internal_ratio": 2.6, "spur": 84, "pinion": 20},
    "Yokomo YD-2S": {"internal_ratio": 2.6, "spur": 84, "pinion": 20},
    "Yokomo YD-2E": {"internal_ratio": 2.6, "spur": 84, "pinion": 20},
    "Yokomo YD-2SX III": {"internal_ratio": 2.6, "spur": 84, "pinion": 20},
    "Yokomo YD-2ZX": {"internal_ratio": 2.6, "spur": 84, "pinion": 24},
    "Yokomo SD 2.0": {"internal_ratio": 2.6, "spur": 80},  # kit ships no pinion (24T ref)
    "Yokomo SD 3.0": {"internal_ratio": 2.6, "spur": 80},  # kit ships no pinion (24T ref)
    "Yokomo RD 1.0": {"internal_ratio": 2.6, "spur": 84, "pinion": 24},
    "Yokomo RD 2.0": {"internal_ratio": 2.6, "spur": 83, "pinion": 24},
    # MST: reduction printed as "60T/20T <3.00>" (RMX 2.5) or in the setup-sheet line
    # "Spur / Pinion x Reduction Gear" (RMX 4, EX, RMX-M). Note MST's printed TOTALS are
    # unreliable - the MRX S PRO and FRX sheets print 14.38 for inputs computing to 17.84
    # - but the tooth counts they list are sound.
    "MST RMX 2.5": {"internal_ratio": 3.0, "spur": 80, "pinion": 22},
    "MST RMX 4": {"internal_ratio": 3.0, "spur": 80, "pinion": 22},
    "MST RMX-M": {"internal_ratio": 3.0, "spur": 80, "pinion": 22},
    "MST RMX 3.0": {"internal_ratio": 3.0},  # spur ambiguous: 86T exploded view vs 87T table
    "MST RMX EX": {"internal_ratio": 3.0},  # GT is 88T/20T, S PRO 80T/22T - too different
    # MST mid-motor: ratio is the product of the gear stages, printed in full on the
    # setup sheet. These exceed the old 5.0 spinbox ceiling, hence the range widening.
    "MST FXX 2.0": {"internal_ratio": 6.873},  # (36/20) x (42/11)
    "MST FRX": {"internal_ratio": 8.182},  # (38/19) high gear; the 36/21 low set (7.013) also ships
    # Reve D: "2nd RATIO / 2.600" printed on RDX_Gear.pdf, cross-checked against its own
    # cells (65/16 x 2.6 = 10.56). Spur/pinion appear only as spare/option parts.
    "Rêve D RDX": {"internal_ratio": 2.6},
    # Overdose: ratios from part-numbered tooth counts in the assembly steps.
    "Overdose GALM ver.2": {"internal_ratio": 3.25, "pinion": 23},  # 39T diff / 12T centre
    "Overdose Divall": {"internal_ratio": 2.0},  # 38T/19T bevel; spur+pinion not included
}


def suggestions(field: str, cars: Iterable[dict] = ()) -> list[str]:
    """Curated names for a spec field, plus every distinct value already in the garage.

    An unknown field yields only the garage's own values, so adding a spec field to
    garage.new_car() gives a useful (if uncurated) list without touching this module.
    """
    from_garage = set()
    for car in cars:
        value = str(car.get(field) or "").strip()
        if value:
            from_garage.add(value)
    return sorted(set(CURATED.get(field, ())) | from_garage, key=str.casefold)
