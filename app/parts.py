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
