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
# Chassis deliberately absent, and why (all re-confirmed by the 2026-07-21 manual sweep):
#   Overdose XEX          - 2.00 is derived, never printed (the official gear chart's
#                           tables imply exactly 2.000 but print only tooth counts).
#   MST MRX               - variant-dependent: GT ships the 40/13 diff (6.593), S PRO
#                           the 42/11 (8.182) — both confirmed on their own sheets.
#   Onisiki Kodama        - manual labels gears "Gear A/B/C", no tooth counts printed.
#   D-Like Re-R HYBRID    - spur 85T / pinion 22T are in the official parts list but the
#                           internal ratio is not printed (and the manual builds a 24T);
#                           seeding gearing without the ratio yields a wrong FDR.
#   Overdose Vacula 2     - its diff pulley is documented as "33T or 39T" (2.75 or
#                           3.25) with no default stated, unresolvable from the manual.
#   Reve D MC-3           - conversion kit reusing the RDX gearcase; no ratio of its own
#                           (manual says to transplant the gearcase unchanged).
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
    # RMX 3.0: resolved by the KMW manual's factory sheet, "87 T ÷ 25 T × 3 = 10.44"
    # (printed p.31); 80T is the printed alternative for the other two motor
    # positions, and no 86T appears anywhere — the old 86-vs-87 ambiguity is dead.
    "MST RMX 3.0": {"internal_ratio": 3.0, "spur": 87, "pinion": 25},
    "MST RMX EX": {"internal_ratio": 3.0},  # GT is 88T/20T, S PRO 80T/22T - too different
    # MST mid-motor: ratio is the product of the gear stages, printed in full on the
    # setup sheet. These exceed the old 5.0 spinbox ceiling, hence the range widening.
    "MST FXX 2.0": {"internal_ratio": 6.873},  # (36/20) x (42/11)
    "MST FRX": {"internal_ratio": 8.182},  # (38/19) high gear; the 36/21 low set (7.013) also ships
    # Reve D: "2nd RATIO / 2.600" printed on RDX_Gear.pdf, cross-checked against its own
    # cells (65/16 x 2.6 = 10.56). The build manual (RDX_20231115.pdf p.27, BAG9) ships a
    # PG-4820S 20T pinion in the kit; the spur's tooth count is not stated there, so it
    # stays absent.
    "Rêve D RDX": {"internal_ratio": 2.6, "pinion": 20},
    # Overdose: ratios from part-numbered tooth counts in the assembly steps, plus
    # (GALM) the official gear chart, which literally prints "39/12 -> 3.250".
    "Overdose GALM ver.2": {"internal_ratio": 3.25, "pinion": 23},  # 39T diff / 12T centre
    "Overdose GALM": {"internal_ratio": 3.25, "pinion": 23},  # chart prints 3.250; kit OD2420 23T
    "Overdose Divall": {"internal_ratio": 2.0},  # 38T/19T bevel; spur+pinion not included
    # Vacula: official chart prints Rear 33T pulley / 18T centre (33/18 = 1.833, the
    # motor->rear path; the front axle runs 39/16 and the chart's printed F/R factor
    # is 1.330). Same tooth-count sourcing as Divall above.
    "Overdose Vacula": {"internal_ratio": 1.833},
}


# Factory base setup per chassis, keyed by the exact strings in CHASSIS above.
# Values are partial dicts over the garage._SETUP_LABELS keys, string-valued
# verbatim as printed (units and all), e.g.:
#   "Some Chassis": {"ride_height_front": "5.0 mm", "rear_diff": "Ball diff"},
#
# THE RULE (same as CHASSIS_GEARING): a chassis appears here only with values read
# off an official vendor setup sheet, quoted verbatim; omit any value the sheet
# does not state. A wrong "factory" camber silently misleads every base setup
# seeded from it, so a missing entry is always better than an inferred one.
#
# Values below were read off the official manuals/setup sheets in the 2026-07-21
# sweep (one research pass per vendor, spot-verified against the rendered pages).
# Two extra conventions on top of THE RULE:
#   - Numbers are quoted with the vendor's own sign/scale quirks (MST prints camber
#     unsigned and sometimes a minus on a "Toe-in" line meaning toe-out; Yokomo's
#     "#50"/"#300" and MST's "#5..#15" oils are house scales, not cSt/WT). Strings
#     stay short — they live in the setup diagram's small field boxes.
#   - Where a vendor publishes DIFFERENT factory sheets per variant of one chassis
#     name (MST S PRO/GT/Classic/RTR...), a field appears only when every documented
#     variant agrees on the value; disagreeing fields are omitted, so nothing wrong
#     is ever seeded for the variant the user actually owns.
#
# Checked and deliberately ABSENT (nothing printed to seed):
#   Rêve D MC-3        - 4-page conversion doc; dampers explicitly excluded, no
#                        setup values ("This diagram does not include dampers...").
#   D-Like Re-R HYBRID - assembly-only manuals; the one extractable field (rear
#                        diff) is variant-split: original/Kiwami spool vs Ver.ZERO
#                        ball diff, and the app name spans all versions.
CHASSIS_SETUP: dict[str, dict[str, str]] = {
    # Rêve D RDX build manual RDX_20231115.pdf: p.28 ride height ("...both front
    # and rear are 5mm with the body attached") and kit springs/oil (BAG10; the
    # included bottle has no viscosity printed); p.7 step 5 the rear is a spool
    # (D1-500SPM + 52T). Camber/toe/caster: fixed molded arms, no angles printed.
    "Rêve D RDX": {
        "ride_height_front": "5 mm (body on)",
        "ride_height_rear": "5 mm (body on)",
        "spring_front": "Kit D1-SSF1",
        "spring_rear": "Kit D1-SSR1",
        "shock_oil_front": "Kit oil",
        "shock_oil_rear": "Kit oil",
        "rear_diff": "Spool",
    },
    # Yokomo YD-2 family: shock page prints "front #300 & green spring / rear #200
    # & pink spring" (YD-2/S/E p.18; SXIII p.18; ZX p.17 — ZX also prints the part
    # codes D-177FA/D-179RS). Gear diff oil is printed per manual's bag list
    # (#10000; ZX ships #5000 per its Z/ZX parts-list split). No manual prints ride
    # height, camber, toe or caster — the setting-sheet pages are blank templates.
    "Yokomo YD-2": {
        "spring_front": "Kit green",
        "spring_rear": "Kit pink",
        "shock_oil_front": "#300",
        "shock_oil_rear": "#200",
        "rear_diff": "Gear diff (#10000)",
    },
    "Yokomo YD-2S": {
        "spring_front": "Kit green",
        "spring_rear": "Kit pink",
        "shock_oil_front": "#300",
        "shock_oil_rear": "#200",
        "rear_diff": "Gear diff (#10000)",
    },
    "Yokomo YD-2E": {
        "spring_front": "Kit green",
        "spring_rear": "Kit pink",
        "shock_oil_front": "#300",
        "shock_oil_rear": "#200",
        "rear_diff": "Gear diff (#10000)",
    },
    "Yokomo YD-2ZX": {
        "spring_front": "Kit D-177FA",
        "spring_rear": "Kit D-179RS",
        "shock_oil_front": "#300",
        "shock_oil_rear": "#200",
        "rear_diff": "Gear diff (#5000)",
    },
    "Yokomo YD-2SX III": {
        "spring_front": "Kit green",
        "spring_rear": "Kit pink",
        "shock_oil_front": "#300",
        "shock_oil_rear": "#200",
        "rear_diff": "Gear diff (#10000)",
    },
    # Yokomo SD/RD: one #50 bottle fills all four shocks (SD2.0 p.19, SD3.0 p.18,
    # RD1.0/RD2.0 p.16, with the kit spring part codes on the same page); rear
    # gear-diff silicone weight printed in the diff step (SD2.0/RD1.0 #10,000,
    # SD3.0/RD2.0 #7,500). No angles/ride height printed anywhere.
    "Yokomo SD 2.0": {
        "spring_front": "Kit D-184F",
        "spring_rear": "Kit D-182F",
        "shock_oil_front": "#50",
        "shock_oil_rear": "#50",
        "rear_diff": "Gear diff (#10000)",
    },
    "Yokomo SD 3.0": {
        "spring_front": "Kit D-184F",
        "spring_rear": "Kit D-182F",
        "shock_oil_front": "#50",
        "shock_oil_rear": "#50",
        "rear_diff": "Gear diff (#7500)",
    },
    "Yokomo RD 1.0": {
        "spring_front": "Kit D-177FA",
        "spring_rear": "Kit D-179RS",
        "shock_oil_front": "#50",
        "shock_oil_rear": "#50",
        "rear_diff": "Gear diff (#10000)",
    },
    "Yokomo RD 2.0": {
        "spring_front": "Kit YS-RDS",
        "spring_rear": "Kit YS-RDS",
        "shock_oil_front": "#50",
        "shock_oil_rear": "#50",
        "rear_diff": "Gear diff (#7500)",
    },
    # MST: every variant manual ends with a factory-filled "SETUP SHEET ORIGINAL
    # SETTING" page — values here are the per-field agreement across the variants
    # documented for each name (RMX 4: S PRO/GT/Classic/RTR p.33/30/33/10; RMX EX:
    # GT/S PRO p.30; RMX 2.5: RS/2.5S/RTR p.30/27/15; RMX 3.0: KMW p.31; RMX-M:
    # S PRO p.27; FXX 2.0: 2.0S/KMW p.25/20; FRX: RS/S PRO p.30; MRX: GT/V1.5/
    # S PRO p.30/30/26). Ride heights are "final ride height after installing
    # electronics and bodyshell" where the sheet says so (RMX 4 / RMX EX).
    "MST RMX 4": {
        "ride_height_front": "9 mm (body on)",
        "ride_height_rear": "5 mm (body on)",
        "camber_rear": "2°",
        "toe_rear": "Toe-in 1°",
        "caster": "7°",
        "shock_oil_rear": "#15",
    },
    "MST RMX EX": {
        "ride_height_front": "10 mm (body on)",
        "ride_height_rear": "5 mm (body on)",
        "camber_front": "7.5°",
        "camber_rear": "2°",
        "toe_front": "Toe-out 3.6°",
        "caster": "8°",
        "spring_front": "Green 26 mm",
        "spring_rear": "Purple-Green 28 mm",
        "shock_oil_front": "#10",
        "shock_oil_rear": "#10",
        "rear_diff": "Gear diff (#3000)",
    },
    "MST RMX 2.5": {
        "ride_height_rear": "5 mm",
        "camber_front": "6°",
    },
    "MST RMX 3.0": {
        "ride_height_front": "6 mm",
        "ride_height_rear": "5 mm",
        "camber_front": "5°",
        "camber_rear": "2°",
        "toe_front": "Toe-in -3.5°",
        "toe_rear": "Toe-in 1°",
        "caster": "8°",
        "spring_front": "Red 31 mm",
        "spring_rear": "Purple-green 31 mm",
        "shock_oil_front": "#5",
        "shock_oil_rear": "#5",
    },
    "MST RMX-M": {
        "ride_height_front": "5 mm",
        "ride_height_rear": "5 mm",
        "camber_front": "5°",
        "camber_rear": "2.5°",
        "toe_front": "Toe-out 5.5°",
        "toe_rear": "Toe-in -3°",
        "caster": "8°",
        "spring_front": "Silver 29 mm",
        "spring_rear": "Purple-green 29 mm",
        "shock_oil_front": "#15",
        "shock_oil_rear": "#15",
    },
    "MST FXX 2.0": {
        "ride_height_front": "5 mm",
        "ride_height_rear": "5 mm",
        "camber_rear": "2°",
        "spring_rear": "Purple-yellow 31 mm",
        "shock_oil_front": "#10",
        "shock_oil_rear": "#10",
        "rear_diff": "Ball diff",
    },
    "MST FRX": {
        "ride_height_front": "8 mm",
        "ride_height_rear": "5 mm",
        "toe_rear": "Toe-in 0.5°",
        "caster": "7.5°",
        "spring_rear": "Purple-Green 28 mm",
        "shock_oil_front": "#10",
        "shock_oil_rear": "#10",
        "rear_diff": "Gear diff (#3000)",
    },
    "MST MRX": {
        "caster": "8°",
        "spring_front": "Red 31 mm",
        "shock_oil_front": "#10",
        "shock_oil_rear": "#10",
    },
    # Overdose: the GALM ver.2 manual p.22 is a factory-FILLED setting sheet
    # ("GALM ve.2 Standard Setup") — camber/caster printed unsigned, ride-height
    # boxes left blank, "Standard" spring = the kit OD1560. The other manuals are
    # assembly-only: kit spring OD1560 (1.3×28 mm 9 coils) and the diff type come
    # from their build steps/parts lists; "6° (C-hub)" is the fixed hub-carrier
    # moulding angle printed as a part spec (OD1464), not a tuning-sheet value.
    "Overdose GALM ver.2": {
        "camber_front": "7.0°",
        "camber_rear": "2.0°",
        "toe_front": "0°",
        "toe_rear": "0°",
        "caster": "12.0°",
        "spring_front": "Kit OD1560",
        "spring_rear": "Kit OD1560",
        "shock_oil_front": "OD #10",
        "shock_oil_rear": "OD #10",
        "rear_diff": "Spool",
    },
    "Overdose GALM": {
        "spring_front": "Kit OD1560",
        "spring_rear": "Kit OD1560",
        "rear_diff": "Spool",
    },
    "Overdose Divall": {
        "caster": "6° (C-hub)",
        "spring_front": "Kit OD1560",
        "spring_rear": "Kit OD1560",
        "rear_diff": "Spool",
    },
    "Overdose XEX": {
        "caster": "6° (C-hub)",
        "spring_front": "Kit OD1560",
        "spring_rear": "Kit OD1560",
        "rear_diff": "Spool",
    },
    "Overdose Vacula": {
        "caster": "6° (C-hub)",
        "spring_front": "Kit OD1560",
        "spring_rear": "Kit OD1560",
        "rear_diff": "Spool",
    },
    "Overdose Vacula 2": {
        "spring_front": "Kit OD1560",
        "spring_rear": "Kit OD1560",
        "rear_diff": "Ball diff",
    },
    # Onisiki Kodama: assembly manual only. Shock steps print "500-1000 Cst Oil
    # (Not Included)" (p.6 front, p.13 rear); the rear axle is a solid axle with
    # ring gear — no differential exists in the design (p.10). Springs are
    # labeled only "Front/Rear Spring", no rate printed.
    "Onisiki Kodama": {
        "shock_oil_front": "500–1000 cSt",
        "shock_oil_rear": "500–1000 cSt",
        "rear_diff": "Solid axle",
    },
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
