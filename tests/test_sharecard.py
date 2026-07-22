"""Setup share cards: codec round-trips, hostile payloads, PNG text chunks."""

import json

from app import garage


def test_png_text_chunk_roundtrip(monkeypatch):
    """Platform canary: QImage text survives a PNG encode/decode cycle.

    The whole share-card feature rests on this Qt behaviour; if a PySide6
    upgrade ever drops it, this test names the culprit directly.
    """
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QBuffer
    from PySide6.QtGui import QImage

    img = QImage(4, 4, QImage.Format.Format_ARGB32)
    img.fill(0xFF336699)
    img.setText("rccard", "payload-goes-here")
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    assert img.save(buf, "PNG")
    out = QImage.fromData(buf.data(), "PNG")
    assert out.text("rccard") == "payload-goes-here"


def test_build_encode_decode_roundtrip():
    from app import sharecard

    car = garage.new_car("Midnight YD-2")
    car.update(
        chassis="Yokomo YD-2S",
        motor="10.5T brushless",
        esc="Rêve D ELITE",
        servo="RS-ST PRO",
        tires="DS1 spec",
        notes="street setup, feels planted",
    )
    car["gearing"].update(pinion=20, spur=87)
    car["setup"]["camber_front"] = "-5"
    car["setup"]["shock_oil_front"] = "#350"
    car["log"].append({"date": "2026-07-22", "kind": "Run", "note": "private note"})

    card = sharecard.build_card(car)
    # Only the shareable fields travel — no id/log/presets/base_setup.
    assert set(card) == {
        "name", "chassis", "motor", "esc", "servo", "tires", "notes",
        "gearing", "setup",
    }
    assert "private note" not in json.dumps(card)
    assert card["gearing"]["pinion"] == 20
    assert card["setup"]["camber_front"] == "-5"

    code = sharecard.encode(card)
    assert code.startswith("RCSETUP1.")
    assert sharecard.decode(code) == card


def _valid_code(**card_fields) -> str:
    from app import sharecard

    car = garage.new_car("Test Car")
    for key, value in card_fields.items():
        car[key] = value
    return sharecard.encode(sharecard.build_card(car))


def test_decode_survives_chat_mangling():
    """Discord wraps long codes across lines and copy/paste eats '=' padding."""
    from app import sharecard

    code = _valid_code(chassis="MST RMX 2.5")
    mangled = "\n".join(
        code[i : i + 40] for i in range(0, len(code), 40)
    ).rstrip("=")
    assert sharecard.decode(mangled)["chassis"] == "MST RMX 2.5"


def test_decode_rejects_garbage():
    import pytest

    from app import sharecard

    code = _valid_code()
    for bad in (
        None,  # not text
        "",  # empty
        "hello there",  # no prefix
        code[: len(code) // 2],  # truncated mid-stream
        "RCSETUP1.",  # empty body
        "RCSETUP1.!!!not-base64???",  # junk base64
        "RCSETUP1.émoji",  # non-ascii body
        "RCSETUP1." + "A" * 70000,  # over the code-size cap
    ):
        with pytest.raises(ValueError):
            sharecard.decode(bad)


def test_decode_rejects_zlib_bomb_cheaply():
    """10 MB of zeros compresses tiny; decode must refuse without inflating it."""
    import base64
    import zlib

    import pytest

    from app import sharecard

    bomb = "RCSETUP1." + base64.urlsafe_b64encode(
        zlib.compress(b"\x00" * 10_000_000)
    ).decode("ascii")
    assert len(bomb) < 65536  # small enough to pass the code-size cap
    with pytest.raises(ValueError, match="too large"):
        sharecard.decode(bomb)


def test_decode_version_gate():
    import base64
    import json as _json
    import zlib

    import pytest

    from app import sharecard

    def envelope(obj) -> str:
        raw = _json.dumps(obj).encode()
        return "RCSETUP1." + base64.urlsafe_b64encode(zlib.compress(raw)).decode()

    with pytest.raises(ValueError, match="newer version"):
        sharecard.decode(envelope({"rccard": 2, "card": {}}))
    for bad in (
        ["not", "a", "dict"],  # envelope not a dict
        {"rccard": "1", "card": {}},  # version not an int
        {"rccard": 1, "card": "nope"},  # card not a dict
        {"rccard": 1},  # card missing
    ):
        with pytest.raises(ValueError):
            sharecard.decode(envelope(bad))


def test_hostile_card_fields_are_cleaned():
    from app import sharecard

    card = sharecard.decode(
        sharecard.encode(
            {
                "name": "x" * 5000,  # over the field cap
                "notes": "n" * 5000,  # under the notes cap
                "chassis": {"nested": "dict"},  # non-scalar -> ""
                "tires": True,  # bool -> ""
                "evil_key": "dropped",
                "gearing": {
                    "pinion": -5,  # out of range
                    "spur": 1e9,  # out of range
                    "kv": "3000",  # wrong type
                    "cells": True,  # bool masquerading as int
                    "internal_ratio": 1.9,  # valid, survives
                    "fdr": 8.44,  # computed field, never travels
                },
                "setup": {"camber_front": 42, "bogus": "dropped"},
            }
        )
    )
    assert len(card["name"]) == 200
    assert len(card["notes"]) == 2000
    assert card["chassis"] == ""
    assert card["tires"] == ""
    assert "evil_key" not in card
    assert card["gearing"] == {"internal_ratio": 1.9}
    assert card["setup"] == {"camber_front": "42"}


def test_card_to_car_fresh_identity_and_defaults():
    from app import sharecard

    original = garage.new_car("Donor")
    original["chassis"] = "Overdose GALM"
    original["gearing"]["pinion"] = 23
    original["setup"]["caster"] = "12"
    car = sharecard.card_to_car(sharecard.build_card(original))

    assert car["id"] != original["id"]
    assert car["chassis"] == "Overdose GALM"
    assert car["gearing"]["pinion"] == 23
    assert car["gearing"]["spur"] == 87  # untouched fields keep sane defaults
    assert car["setup"]["caster"] == "12"
    assert car["setup"]["camber_front"] == ""  # full 12-key shape restored
    assert car["log"] == [] and car["presets"] == []

    unnamed = sharecard.card_to_car({"name": "   "})
    assert unnamed["name"] == "Shared setup"


def test_render_card_embeds_payload_and_is_dark(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QBuffer
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    from app import sharecard
    from app.ui.share_card import render_card

    # Pin the accent: cards must render the sharer's accent wherever it's set.
    monkeypatch.setattr("app.ui.share_card._accent", lambda: "#1f6feb")
    monkeypatch.setattr("app.ui.setup_diagram._accent", lambda: "#1f6feb")

    car = garage.new_car("Card Car")
    car["chassis"] = "Yokomo YD-2S"
    car["motor"] = "10.5T"
    car["gearing"].update(pinion=20, spur=87)
    car["setup"]["camber_front"] = "-5"
    car["setup"]["rear_diff"] = "Spool"

    img = render_card(car)
    assert (img.width(), img.height()) == (1200, 675)

    # The contract that matters: the SAVED png still carries the payload.
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    assert img.save(buf, "PNG")
    reread = QImage.fromData(buf.data(), "PNG")
    decoded = sharecard.decode(reread.text(sharecard.PNG_TEXT_KEY))
    assert decoded == sharecard.build_card(car)

    # Always-dark canvas (independent of app theme) with actual drawing on it.
    probes = [
        reread.pixelColor(x, y)
        for x in range(30, 1200, 120)
        for y in range(30, 675, 90)
    ]
    dark = sum(1 for c in probes if c.lightnessF() < 0.5)
    assert dark > len(probes) * 0.7
    assert len({c.name() for c in probes}) >= 3  # canvas + body + drawn lines
    assert reread.pixelColor(2, 2).name() == "#1f6feb"  # accent frame


def test_render_card_survives_sparse_cars(monkeypatch):
    """A nearly-empty imported card must still render (no gearing, no setup)."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    from app import sharecard
    from app.ui.share_card import render_card

    bare = sharecard.card_to_car({"name": "Bare"})
    bare["gearing"]["pinion"] = 0  # would divide by zero in FDR
    img = render_card(bare)
    assert not img.isNull()


def test_share_card_dialog_buttons(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QFileDialog

    app = QApplication.instance() or QApplication([])
    from app import sharecard
    from app.ui.share_card import ShareCardDialog

    car = garage.new_car("Dialog Car")
    car["chassis"] = "MST RMX 2.5"
    dlg = ShareCardDialog(car)
    try:
        pix = dlg.preview.pixmap()
        assert pix is not None and not pix.isNull()

        dlg.copy_code_btn.click()
        assert sharecard.decode(app.clipboard().text())["chassis"] == "MST RMX 2.5"

        dlg.copy_image_btn.click()
        assert not app.clipboard().image().isNull()

        out = tmp_path / "card.png"
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            lambda *a, **k: (str(out), "PNG image (*.png)"),
        )
        dlg.save_btn.click()
        saved = QImage(str(out))
        decoded = sharecard.decode(saved.text(sharecard.PNG_TEXT_KEY))
        assert decoded["chassis"] == "MST RMX 2.5"
    finally:
        dlg.deleteLater()


def test_garage_share_button_opens_dialog(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    from app.ui.garage_tab import GarageTab

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    opened = []

    class _StubDialog:
        def __init__(self, car, parent=None):
            opened.append(car["name"])

        def exec(self):
            return 0

    monkeypatch.setattr("app.ui.garage_tab.ShareCardDialog", _StubDialog)
    tab = GarageTab()
    tab.name.setText("Sharer")
    tab.share_btn.click()
    assert opened == ["Sharer"]


def _card_png(tmp_path, name="PNG Car", chassis="Yokomo YD-2S"):
    from app.ui.share_card import render_card

    car = garage.new_car(name)
    car["chassis"] = chassis
    path = tmp_path / f"{name}.png"
    assert render_card(car).save(str(path), "PNG")
    return path


def test_import_png_card_creates_car(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    _ = QApplication.instance() or QApplication([])
    from app.ui.garage_tab import GarageTab

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    # Patched so a failure asserts instead of blocking the suite on a modal.
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warnings.append(a[2])
    )
    png = _card_png(tmp_path)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *a, **k: (str(png), "")
    )
    tab = GarageTab()
    tab._on_import()

    assert warnings == []
    cars = garage.list_cars()
    assert [car["chassis"] for car in cars] == ["Yokomo YD-2S"]
    assert tab.current_id == cars[0]["id"]


def test_import_plain_png_warns_helpfully(monkeypatch, tmp_path):
    """A screenshot/re-encoded card has no chunk; the error must say so."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    _ = QApplication.instance() or QApplication([])
    from app.ui.garage_tab import GarageTab

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    plain = tmp_path / "plain.png"
    img = QImage(40, 40, QImage.Format.Format_ARGB32)
    img.fill(0xFF000000)
    assert img.save(str(plain), "PNG")

    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warnings.append(a[2])
    )
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *a, **k: (str(plain), "")
    )
    tab = GarageTab()
    tab._on_import()

    assert garage.list_cars() == []
    assert len(warnings) == 1
    assert "No RC Central setup data" in warnings[0]


def test_paste_setup_code_imports(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

    _ = QApplication.instance() or QApplication([])
    from app import sharecard
    from app.ui.garage_tab import GarageTab

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warnings.append(a[2])
    )

    donor = garage.new_car("Coded Car")
    donor["chassis"] = "Overdose GALM"
    code = sharecard.encode(sharecard.build_card(donor))
    monkeypatch.setattr(
        QInputDialog, "getMultiLineText", lambda *a, **k: (code, True)
    )
    tab = GarageTab()
    tab.paste_btn.click()  # dedicated button, wired straight to the paste flow

    assert warnings == []
    assert [car["chassis"] for car in garage.list_cars()] == ["Overdose GALM"]


def test_paste_garbage_code_warns_and_cancel_is_silent(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

    _ = QApplication.instance() or QApplication([])
    from app.ui.garage_tab import GarageTab

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warnings.append(a[2])
    )
    tab = GarageTab()

    monkeypatch.setattr(
        QInputDialog, "getMultiLineText", lambda *a, **k: ("not a code", True)
    )
    tab._on_paste_code()
    assert len(warnings) == 1 and garage.list_cars() == []

    monkeypatch.setattr(
        QInputDialog, "getMultiLineText", lambda *a, **k: ("", False)
    )
    tab._on_paste_code()  # cancelled: no new warning, no import
    assert len(warnings) == 1 and garage.list_cars() == []


def test_drag_drop_imports_png_and_json(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    from app.ui.garage_tab import GarageTab

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    tab = GarageTab()
    assert tab.acceptDrops()

    # A real Shift-drag from Explorer: Copy and Move both possible, Shift
    # makes Move the default — the handler must force Copy anyway.
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    shift = Qt.KeyboardModifier.ShiftModifier

    def drag_enter(url_paths):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in url_paths])
        event = QDragEnterEvent(
            QPoint(5, 5), actions, mime, Qt.MouseButton.LeftButton, shift
        )
        event.ignore()  # manual events start accepted; the handler must opt in
        tab.dragEnterEvent(event)
        return event, mime

    def drop(mime):
        event = QDropEvent(
            QPointF(5, 5), actions, mime, Qt.MouseButton.LeftButton, shift
        )
        tab.dropEvent(event)
        for _ in range(3):  # the import is deferred out of the drop handshake
            app.processEvents()
        return event

    png = _card_png(tmp_path)
    event, mime = drag_enter([png])
    assert event.isAccepted()
    event = drop(mime)
    # Never Move — accepting it would make Explorer delete the source file.
    assert event.dropAction() == Qt.DropAction.CopyAction
    assert [car["chassis"] for car in garage.list_cars()] == ["Yokomo YD-2S"]

    json_car = garage.new_car("Json Car")
    json_car["chassis"] = "MST FXX"
    json_path = tmp_path / "car.json"
    json_path.write_text(json.dumps(json_car), encoding="utf-8")
    event, mime = drag_enter([json_path])
    assert event.isAccepted()
    drop(mime)
    assert sorted(car["chassis"] for car in garage.list_cars()) == [
        "MST FXX",
        "Yokomo YD-2S",
    ]

    event, _mime = drag_enter([tmp_path / "readme.txt"])
    assert not event.isAccepted()

    # A multi-file drop imports every matching file, not silently just one.
    two = [
        _card_png(tmp_path, name="Car A", chassis="Chassis A"),
        _card_png(tmp_path, name="Car B", chassis="Chassis B"),
    ]
    event, mime = drag_enter(two)
    assert event.isAccepted()
    drop(mime)
    chassis = sorted(car["chassis"] for car in garage.list_cars())
    assert "Chassis A" in chassis and "Chassis B" in chassis


def test_encode_stays_importable_with_emoji_and_cjk():
    """ensure_ascii inflation must never push a legal card past the decode cap."""
    from app import sharecard

    card = sharecard.build_card({"name": "夜のセットアップ", "notes": "🔥" * 2000})
    assert sharecard.decode(sharecard.encode(card)) == card


def test_decode_hostile_payloads_stay_valueerror():
    """Deep nesting (RecursionError) and huge int literals must surface as the
    friendly 'garbled' ValueError, per decode's exception contract."""
    import base64
    import zlib

    import pytest

    from app import sharecard

    deep = "RCSETUP1." + base64.urlsafe_b64encode(zlib.compress(b"[" * 4000)).decode()
    with pytest.raises(ValueError, match="garbled"):
        sharecard.decode(deep)

    big = b'{"rccard":1,"card":{"name":' + b"9" * 5000 + b"}}"
    code = "RCSETUP1." + base64.urlsafe_b64encode(zlib.compress(big)).decode()
    with pytest.raises(ValueError, match="garbled"):
        sharecard.decode(code)


def test_gearing_domains_mirror_gear_tab():
    """Values the Gear tab spinboxes would clamp or truncate must be dropped,
    not imported and silently mutated on the next save."""
    from app import sharecard

    card = sharecard.build_card(
        {
            "gearing": {
                "pinion": 150,  # > widget max 99
                "spur": 87.9,  # fractional int field
                "internal_ratio": 0.5,  # < widget min 1.0
                "tire_diameter_mm": 60,  # in range, survives
                "kv": 20.5,  # fractional int field
                "cells": 9,  # > widget max 8
            }
        }
    )
    assert card["gearing"] == {"tire_diameter_mm": 60}

    ok = sharecard.build_card({"gearing": {"pinion": 20.0, "spur": 87}})
    assert ok["gearing"] == {"pinion": 20, "spur": 87}  # whole floats become ints


def test_share_schema_stays_in_sync_with_garage():
    """Drift guard: a field added to garage.py must show up here, or these sets
    diverge silently (card renders fine, share crashes or drops the field)."""
    from app import sharecard
    from app.ui.setup_diagram import _ANCHORS, _CAPTIONS, _LEFT_KEYS, _RIGHT_KEYS

    setup_keys = {key for key, _ in garage._SETUP_LABELS}
    assert set(sharecard._GEARING_DOMAINS) == {
        key for key, _ in garage._GEARING_INPUT_LABELS
    }
    assert set(_LEFT_KEYS + _RIGHT_KEYS) == setup_keys
    assert set(_ANCHORS) == setup_keys
    assert set(_CAPTIONS) == setup_keys


def test_import_json_without_name_gets_default(tmp_path):
    path = tmp_path / "partial.json"
    path.write_text('{"chassis": "YD-2"}', encoding="utf-8")
    assert garage.load_car_file(path)["name"] == "Imported car"


def test_failed_import_restores_form(monkeypatch, tmp_path):
    """A rejected import must not leave the form half-overwritten: the next
    Save would clobber the previously open car with the junk."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    _ = QApplication.instance() or QApplication([])
    from app.ui.garage_tab import GarageTab

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warnings.append(a[2])
    )

    mine = garage.new_car("Mine")
    mine["chassis"] = "Real chassis"
    mine = garage.save_car(mine)
    tab = GarageTab()
    tab.current_id = mine["id"]
    tab._fill_form(mine)

    tab._import_car({"name": "Pwned", "notes": 123})  # setPlainText(int) raises

    assert len(warnings) == 1
    assert tab.current_id == mine["id"]
    assert tab.name.text() == "Mine"  # form restored, not half-'Pwned'
    tab._on_save()
    assert garage.load_car(mine["id"])["name"] == "Mine"


def test_import_save_failure_warns_not_crashes(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    _ = QApplication.instance() or QApplication([])
    from app.ui.garage_tab import GarageTab

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warnings.append(a[2])
    )
    monkeypatch.setattr(
        garage, "save_car", lambda car: (_ for _ in ()).throw(OSError("disk full"))
    )
    tab = GarageTab()
    tab._import_car(garage.new_car("Doomed"))  # must warn, not raise

    assert warnings == ["disk full"]


def test_import_unreadable_png_names_the_real_problem(monkeypatch, tmp_path):
    """A file that isn't a readable image must not be blamed on metadata
    stripping — that error sends users chasing the wrong cause."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    _ = QApplication.instance() or QApplication([])
    from app.ui.garage_tab import GarageTab

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warnings.append(a[2])
    )
    not_an_image = tmp_path / "fake.png"
    not_an_image.write_text("this is not a png", encoding="utf-8")

    tab = GarageTab()
    tab._import_path(str(not_an_image))

    assert warnings == ["Could not read this file as an image."]
    assert garage.list_cars() == []
