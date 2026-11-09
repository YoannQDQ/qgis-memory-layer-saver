"""Regression tests, to run with the Python of a QGIS installation (no display needed):

python-qgis tests/test_regressions.py <repository folder> <empty work folder>
"""

import os
import shutil
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

PLUGIN_PARENT, WORK = sys.argv[1], Path(sys.argv[2])
WORK.mkdir(parents=True, exist_ok=True)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QGIS_CUSTOM_CONFIG_PATH"] = str(WORK / "profile")

from qgis.PyQt.QtCore import (  # noqa: E402
    QCoreApplication,
    QDataStream,
    QDate,
    QDateTime,
    QEvent,
    QFile,
    QIODevice,
    QMetaType,
    QSettings,
    QTime,
    QTimeZone,
)
from qgis.PyQt.QtWidgets import QMainWindow, QMenu, QMessageBox  # noqa: E402

QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(WORK / "settings"))
QCoreApplication.setOrganizationName("MLSTests")
QCoreApplication.setApplicationName("MLSTests")

from qgis.core import (  # noqa: E402
    Qgis,
    QgsApplication,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsSettings,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.gui import QgsMessageBar  # noqa: E402

app = QgsApplication([], True)
app.initQgis()
assert "MLSTests" in QgsSettings().fileName(), QgsSettings().fileName()


class RecordingBar(QgsMessageBar):
    def __init__(self, parent):
        super().__init__(parent)
        self.messages = []

    def pushMessage(self, *args):
        self.messages.append(str(args))


class Iface:
    def __init__(self):
        self.win = QMainWindow()
        self.menu = QMenu(self.win)
        self.bar = RecordingBar(self.win)

    def mainWindow(self):
        return self.win

    def pluginMenu(self):
        return self.menu

    def messageBar(self):
        return self.bar


import qgis.utils  # noqa: E402

iface = qgis.utils.iface = Iface()
sys.path.insert(0, PLUGIN_PARENT)

dialogs = []
answer = {"value": QMessageBox.StandardButton.No}


def fake_box(kind):
    def box(*args, **kwargs):
        dialogs.append((kind, str(args[1:])))
        return answer["value"]

    return staticmethod(box)


for kind in ("information", "warning", "critical", "about", "question"):
    setattr(QMessageBox, kind, fake_box(kind))
slot_errors = []
sys.excepthook = lambda t, v, tb: slot_errors.append(f"{t.__name__}: {v}")

from MemoryLayerSaver.memory_layer_saver import MemoryLayerSaver  # noqa: E402
from MemoryLayerSaver.reader import Reader  # noqa: E402
from MemoryLayerSaver.settings import Settings  # noqa: E402
from MemoryLayerSaver.settings_dialog import SettingsDialog  # noqa: E402
from MemoryLayerSaver.writer import Writer  # noqa: E402

proj = QgsProject.instance()
plugin = MemoryLayerSaver()
plugin.initGui()


def layer(name, n, fields="a:integer&field=s:string(20)"):
    lyr = QgsVectorLayer(f"Point?crs=EPSG:4326&field={fields}", name, "memory")
    feats = []
    for i in range(n):
        f = QgsFeature(lyr.fields())
        f.setAttribute(0, i)
        f.setGeometry(QgsGeometry.fromWkt(f"POINT({i} {i})"))
        feats.append(f)
    lyr.dataProvider().addFeatures(feats)
    lyr.updateExtents()
    proj.addMapLayer(lyr)
    return lyr


def new_project(name, ext="qgz"):
    folder = Path(tempfile.mkdtemp(dir=WORK))
    proj.clear()
    return str(folder / f"{name}.{ext}")


def get(name):
    return proj.mapLayersByName(name)[0]


def counts():
    return {lyr.name(): lyr.dataProvider().featureCount() for lyr in proj.mapLayers().values()}


def reopen(path):
    proj.clear()
    assert proj.read(path), proj.error()


def mldata_entries(path):
    with zipfile.ZipFile(path) as z:
        return {i.filename: i.file_size for i in z.infolist() if "mldata" in i.filename}


def mldata_bytes(path):
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith("layers.mldata"):
                return z.read(name)
    return None


def replace_mldata(path, data):
    tmp = path + ".tmp"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for i in src.infolist():
            dst.writestr(i, data if i.filename.endswith("layers.mldata") else src.read(i.filename))
    shutil.move(tmp, path)


def layers_in_file(data):
    """The {layer name: feature count} of a v2 file, read independently of the plugin"""
    p = Path(tempfile.mkdtemp(dir=WORK)) / "scan.mldata"
    p.write_bytes(data)
    qfile = QFile(str(p))
    assert qfile.open(QIODevice.OpenModeFlag.ReadOnly)
    ds = QDataStream(qfile)
    ds.setVersion(QDataStream.Version.Qt_4_5)
    ds.readRawData(20)
    assert ds.readInt32() == 2
    result = {}
    while not ds.atEnd():
        name = ds.readQString().split("_")[0]
        ds.readQString()
        nattr = ds.readInt16()
        for _i in range(nattr):
            ds.readQString(), ds.readInt16(), ds.readQString(), ds.readInt16(), ds.readInt16(), ds.readQString()
        count = 0
        while ds.readBool():
            for _i in range(nattr):
                ds.readQVariant()
            size = ds.readUInt32()
            if size:
                ds.readRawData(size)
            count += 1
        assert ds.status() == QDataStream.Status.Ok, "damaged file"
        result[name] = count
    qfile.close()
    return result


def edit_add_feature(lyr):
    lyr.startEditing()
    lyr.addFeature(QgsFeature(lyr.fields()))
    assert lyr.commitChanges(), lyr.commitErrors()


def make_permanent(lyr):
    """What the Make Permanent action of QGIS does: write a file, then change the data source"""
    target = str(Path(tempfile.mkdtemp(dir=WORK)) / "moved.gpkg")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    assert QgsVectorFileWriter.writeAsVectorFormatV3(lyr, target, proj.transformContext(), options)[0] == 0
    lyr.setDataSource(target, lyr.name(), "ogr")
    assert lyr.dataProvider().name() == "ogr"


tests = []


def test(fn):
    tests.append(fn)
    return fn


@test
def failed_write_keeps_the_previous_file():
    new_project("t")
    a, b = layer("A", 5), layer("B", 5)
    f = str(WORK / "direct.mldata")
    with Writer(f) as w:
        w.write_layers([a, b])
    good = Path(f).read_bytes()

    class Boom:
        def id(self):
            return "boom"

        def dataProvider(self):
            raise OSError("simulated")

    try:
        with Writer(f) as w:
            w.write_layers([a, Boom(), b])
        raise AssertionError("should have raised")
    except OSError:
        pass
    assert Path(f).read_bytes() == good, "the file changed"


@test
def failed_save_is_reported_and_stays_pending():
    path = new_project("t")
    layer("A", 5)
    proj.write(path)
    good = mldata_bytes(path)
    original = Writer.write_layer

    def fail(self, lyr):
        raise OSError("disk full")

    Writer.write_layer = fail
    try:
        edit_add_feature(get("A"))
        proj.write(path)
    finally:
        Writer.write_layer = original
    assert mldata_bytes(path) == good, "the file changed"
    assert plugin.has_modified_layers
    assert any("disk full" in m for m in iface.bar.messages), iface.bar.messages


@test
def field_length_overflow_does_not_break_the_save():
    """#13"""
    path = new_project("t")
    layer("A", 5)
    layer("C", 5, "t:string(40000)")
    proj.write(path)
    assert not slot_errors, slot_errors
    reopen(path)
    assert counts() == {"A": 5, "C": 5}, counts()
    assert get("C").fields()[0].length() == 32767


@test
def user_type_field_does_not_break_the_save():
    """Qt6 only: user type ids start at 65536. Values of such fields are not kept by this file format."""
    path = new_project("t")
    layer("A", 2)
    g = QgsVectorLayer("Point?crs=EPSG:4326", "G", "memory")
    g.dataProvider().addAttributes([QgsField("g", QMetaType.Type.User, "geometry")])
    g.updateFields()
    g.dataProvider().addFeatures([QgsFeature(g.fields())])
    proj.addMapLayer(g)
    proj.write(path)
    assert not slot_errors, slot_errors
    reopen(path)
    assert counts() == {"A": 2, "G": 1}, counts()
    assert get("G").fields()[0].typeName() == "geometry"
    assert get("G").fields()[0].type() == QMetaType.Type.User


@test
def truncated_file_is_reported():
    path = new_project("t")
    layer("A", 50), layer("B", 50), layer("C", 50)
    proj.write(path)
    good = mldata_bytes(path)
    replace_mldata(path, good[: len(good) // 2])
    reopen(path)
    assert any(k == "warning" for k, _ in dialogs), f"no error shown: {counts()}"


@test
def failed_load_does_not_overwrite_the_file():
    path = new_project("t")
    layer("A", 50), layer("B", 50), layer("C", 50)
    proj.write(path)
    good = mldata_bytes(path)
    half = good[: len(good) // 2]
    replace_mldata(path, half)
    reopen(path)
    edit_add_feature(next(lyr for lyr in proj.mapLayers().values() if lyr.featureCount()))

    # Answering No keeps the file
    proj.write(path)
    assert mldata_bytes(path) == half, f"overwritten without asking: {layers_in_file(mldata_bytes(path))}"
    assert plugin.has_modified_layers

    # Answering Yes replaces it
    answer["value"] = QMessageBox.StandardButton.Yes
    proj.write(path)
    assert mldata_bytes(path) != half


@test
def empty_file_of_older_versions_is_not_an_error():
    path = new_project("t")
    layer("A", 5)
    proj.write(path)
    replace_mldata(path, b"")
    dialogs.clear()
    reopen(path)
    assert not dialogs, dialogs


@test
def already_loaded_layer_is_skipped():
    new_project("t")
    a, b = layer("A", 5), layer("B", 5)
    f = str(WORK / "loaded.mldata")
    with Writer(f) as w:
        w.write_layers([a, b])
    empty = QgsVectorLayer("Point?crs=EPSG:4326", "B2", "memory")

    class Target:
        """Reads into the empty layer as if it was B"""

        def __getattr__(self, name):
            return getattr(empty, name)

        def id(self):
            return b.id()

    with Reader(f) as r:
        r.read_layers([a, Target()])
    assert a.featureCount() == 5 and empty.featureCount() == 5, (a.featureCount(), empty.featureCount())


@test
def filter_change_is_saved():
    path = new_project("t")
    layer("A", 10)
    proj.write(path)
    reopen(path)
    assert not proj.isDirty()
    get("A").setSubsetString('"a" < 3')
    proj.write(path)
    reopen(path)
    assert get("A").subsetString() == '"a" < 3', repr(get("A").subsetString())
    assert get("A").featureCount() == 3


@test
def provider_edits_are_saved():
    path = new_project("t")
    layer("A", 3)
    proj.write(path)
    reopen(path)
    a = get("A")
    f = QgsFeature(a.fields())
    f.setGeometry(QgsGeometry.fromWkt("POINT(9 9)"))
    a.dataProvider().addFeatures([f])
    a.dataProvider().addAttributes([QgsField("z", QMetaType.Type.Int)])
    proj.write(path)
    reopen(path)
    assert counts() == {"A": 4}, counts()
    assert [x.name() for x in get("A").fields()] == ["a", "s", "z"]


@test
def committed_field_rename_is_saved():
    path = new_project("t")
    layer("A", 3)
    proj.write(path)
    reopen(path)
    a = get("A")
    a.startEditing()
    a.renameAttribute(0, "renamed")
    assert a.commitChanges()
    proj.write(path)
    reopen(path)
    assert [x.name() for x in get("A").fields()] == ["renamed", "s"], [x.name() for x in get("A").fields()]


@test
def storage_switch_does_not_keep_a_stale_file():
    path = new_project("t")
    Settings.set_mldata_embedded(False)
    layer("A", 3)
    proj.write(path)
    Settings.set_mldata_embedded(True)
    edit_add_feature(get("A"))
    proj.write(path)
    Settings.set_mldata_embedded(False)
    proj.write(path)
    reopen(path)
    assert counts() == {"A": 4}, counts()


@test
def no_change_means_no_rewrite():
    path = new_project("t")
    a = layer("A", 10)
    a.setSubsetString('"a" < 3')
    proj.write(path)
    reopen(path)
    target = plugin.memory_layer_file()
    stamp = Path(target).stat().st_mtime_ns
    proj.write(path)
    assert Path(plugin.memory_layer_file()).stat().st_mtime_ns == stamp


@test
def filtered_layer_in_edit_mode_keeps_all_features():
    path = new_project("t")
    a = layer("A", 10)
    a.setSubsetString('"a" < 3')
    a.startEditing()
    proj.write(path)
    assert a.subsetString() == '"a" < 3' and a.featureCount() == 3
    a.rollBack()
    assert layers_in_file(mldata_bytes(path)) == {"A": 10}, layers_in_file(mldata_bytes(path))


@test
def saving_does_not_touch_filtered_layers():
    path = new_project("t")
    a = layer("A", 10)
    a.setSubsetString('"a" < 3')
    seen = []
    a.subsetStringChanged.connect(lambda: seen.append("subsetStringChanged"))
    a.repaintRequested.connect(lambda *x: seen.append("repaintRequested"))
    a.dataChanged.connect(lambda: seen.append("dataChanged"))
    proj.write(path)
    assert not seen, seen


@test
def uncommitted_edits_are_not_saved():
    path = new_project("t")
    layer("A", 5), layer("B", 5)
    proj.write(path)
    reopen(path)
    a = get("A")
    a.startEditing()
    a.deleteFeatures([f.id() for f in a.getFeatures()][:3])
    edit_add_feature(get("B"))
    proj.write(path)
    a.rollBack()
    reopen(path)
    assert counts() == {"A": 5, "B": 6}, counts()


@test
def uncommitted_field_deletion_keeps_the_values():
    path = new_project("t")
    a = layer("A", 3)
    a.dataProvider().changeAttributeValues({f.id(): {1: "keep"} for f in a.getFeatures()})
    a.startEditing()
    a.deleteAttribute(1)
    proj.write(path)
    a.rollBack()
    reopen(path)
    assert [f["s"] for f in get("A").getFeatures()] == ["keep"] * 3, [f["s"] for f in get("A").getFeatures()]


@test
def no_attachment_without_memory_layers():
    path = new_project("t")
    proj.write(path)
    assert not mldata_entries(path), mldata_entries(path)


@test
def last_layer_made_permanent_leaves_no_data():
    path = new_project("t")
    a, b = layer("A", 5000), layer("B", 5000)
    proj.write(path)
    size = os.path.getsize(path)
    make_permanent(a)
    make_permanent(b)
    proj.write(path)
    assert not mldata_entries(path), mldata_entries(path)
    assert os.path.getsize(path) < size / 2, (os.path.getsize(path), size)
    reopen(path)
    assert get("A").featureCount() == 5000 and not dialogs, dialogs


@test
def last_layer_deleted_leaves_no_data():
    path = new_project("t")
    a = layer("A", 5000)
    proj.write(path)
    size = os.path.getsize(path)
    proj.removeMapLayer(a.id())
    proj.write(path)
    assert not mldata_entries(path), mldata_entries(path)
    assert os.path.getsize(path) < size / 2, (os.path.getsize(path), size)


@test
def last_layer_gone_with_a_separate_file_leaves_no_data():
    Settings.set_mldata_embedded(False)
    path = new_project("t")
    a = layer("A", 5)
    proj.write(path)
    assert Path(path + ".mldata").exists()
    make_permanent(a)
    proj.write(path)
    assert not Path(path + ".mldata").exists()


def save_without_plugin(path):
    proj.writeProject.disconnect(plugin.save_data)
    try:
        assert proj.write(path)
    finally:
        proj.writeProject.connect(plugin.save_data)


@test
def stale_data_of_older_versions_is_cleaned_up():
    # What older versions left in a project whose last memory layer is gone
    path = new_project("t")
    a = layer("A", 20)
    proj.write(path)
    make_permanent(a)
    save_without_plugin(path)
    assert layers_in_file(mldata_bytes(path)) == {"A": 20}
    reopen(path)
    proj.write(path)
    assert not mldata_entries(path), mldata_entries(path)

    # With another layer remaining
    path = new_project("t2")
    a, _b = layer("A", 20), layer("B", 3)
    proj.write(path)
    make_permanent(a)
    save_without_plugin(path)
    reopen(path)
    assert get("B").featureCount() == 3
    proj.write(path)
    assert layers_in_file(mldata_bytes(path)) == {"B": 3}, layers_in_file(mldata_bytes(path))


@test
def failed_load_still_asks_when_no_layer_is_left():
    path = new_project("t")
    layer("A", 50)
    proj.write(path)
    good = mldata_bytes(path)
    half = good[: len(good) // 2]
    replace_mldata(path, half)
    reopen(path)
    make_permanent(get("A"))
    proj.write(path)
    assert mldata_bytes(path) == half, "removed without asking"
    answer["value"] = QMessageBox.StandardButton.Yes
    proj.write(path)
    assert not mldata_entries(path), mldata_entries(path)


@test
def about_link():
    plugin.show_about()
    text = next(t for k, t in dialogs if k == "about")
    assert "/issues/issues" not in text, text


@test
def unload_disconnects_and_deletes_the_menu():
    other = MemoryLayerSaver()
    other.initGui()
    count = len(iface.menu.findChildren(QMenu, "memory_layer_saver_menu"))
    other.unload()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(iface.menu.findChildren(QMenu, "memory_layer_saver_menu")) == count - 1, "menu still there"
    other.has_modified_layers = "sentinel"
    proj.clear()
    assert other.has_modified_layers == "sentinel", "still connected"


@test
def settings_dialog_has_a_parent():
    import MemoryLayerSaver.memory_layer_saver as module

    received = []
    exec_original = SettingsDialog.exec
    init_original = SettingsDialog.__init__

    def spy(self, parent=None):
        received.append(parent)
        init_original(self, parent)

    SettingsDialog.__init__ = spy
    SettingsDialog.exec = lambda self: 0
    try:
        module.SettingsDialog = SettingsDialog
        plugin.show_settings()
    finally:
        SettingsDialog.__init__ = init_original
        SettingsDialog.exec = exec_original
    assert received == [iface.win], received
    dialog = SettingsDialog()
    assert dialog.checkbox.text() == "Embed mldata file", dialog.checkbox.text()


@test
def list_field_subtype_survives():
    path = new_project("t")
    a = QgsVectorLayer("Point?crs=EPSG:4326", "A", "memory")
    a.dataProvider().addAttributes(
        [
            QgsField("li", QMetaType.Type.QVariantList, "integerlist", 0, 0, "", QMetaType.Type.Int),
            QgsField("ls", QMetaType.Type.QStringList, "stringlist", 0, 0, "", QMetaType.Type.QString),
        ]
    )
    a.updateFields()
    f = QgsFeature(a.fields())
    f.setAttributes([[1, 2, 3], ["x", "y"]])
    a.dataProvider().addFeatures([f])
    proj.addMapLayer(a)
    proj.write(path)
    reopen(path)
    a = get("A")
    assert a.fields()[0].subType() == QMetaType.Type.Int, a.fields()[0].subType()
    assert a.fields()[1].subType() == QMetaType.Type.QString, a.fields()[1].subType()
    assert next(a.getFeatures()).attributes() == [[1, 2, 3], ["x", "y"]]


@test
def datetime_keeps_its_instant():
    path = new_project("t")
    a = layer("A", 0, "d:datetime")
    values = [
        QDateTime(QDate(2026, 6, 1), QTime(12, 0), QTimeZone(18000)),
        QDateTime(QDate(2026, 6, 1), QTime(12, 0), QTimeZone.utc()),
        QDateTime(QDate(2026, 6, 1), QTime(12, 0)),
    ]
    feats = []
    for value in values:
        f = QgsFeature(a.fields())
        f.setAttributes([value])
        feats.append(f)
    feats.append(QgsFeature(a.fields()))
    a.dataProvider().addFeatures(feats)
    proj.write(path)
    reopen(path)
    after = [f["d"] for f in get("A").getFeatures()]
    expected = [v.toMSecsSinceEpoch() for v in values]
    assert [x.toMSecsSinceEpoch() for x in after[:3]] == expected, after
    assert not after[3] or after[3].isNull()


@test
def values_of_all_types_survive():
    path = new_project("t")
    a = layer("A", 0, "i:integer&field=l:int8&field=d:double&field=s:string&field=b:bool&field=dd:date&field=t:time")
    f = QgsFeature(a.fields())
    expected = [7, 2**40 + 1, 0.1, "é中", True, QDate(2001, 2, 3), QTime(1, 2, 3, 4)]
    f.setAttributes(expected)
    f.setGeometry(QgsGeometry.fromWkt("POINT(1 2)"))
    a.dataProvider().addFeatures([f])
    proj.write(path)
    reopen(path)
    got = next(get("A").getFeatures())
    assert got.attributes() == expected, got.attributes()
    assert got.geometry().asWkt() == "Point (1 2)"


@test
def v1_file_is_still_readable():
    new_project("t")
    a = layer("A", 0)
    f = str(WORK / "v1.mldata")
    qfile = QFile(f)
    qfile.open(QIODevice.OpenModeFlag.WriteOnly)
    ds = QDataStream(qfile)
    ds.setVersion(QDataStream.Version.Qt_4_5)
    ds.writeRawData(b"QGis.MemoryLayerData")
    ds.writeUInt32(1)
    ds.writeQString(a.id())
    ds.writeInt16(1)
    ds.writeQString("n")
    ds.writeInt16(int(QMetaType.Type.Int))
    ds.writeQString("integer")
    ds.writeInt16(0)
    ds.writeInt16(0)
    ds.writeQString("")
    for value in (11, 22):
        ds.writeBool(True)
        ds.writeQVariant(value)
        ds.writeUInt32(0)
    ds.writeBool(False)
    qfile.close()
    with Reader(f) as r:
        r.read_layers([a])
    assert [x["n"] for x in a.getFeatures()] == [11, 22]


def write_user_type(ds, name):
    """What Qt writes for a value of a type it has no stream operator for: its type, without content"""
    ds.writeUInt32(127)
    ds.writeInt8(0)
    ds.writeBytes(name + b"\0")


def write_v2(f, layers):
    """A v2 file, with the layers given as (layer, rows): a row is the value of an integer field 'a'
    and a function writing the value of a string field 's'"""
    qfile = QFile(f)
    qfile.open(QIODevice.OpenModeFlag.WriteOnly)
    ds = QDataStream(qfile)
    ds.setVersion(QDataStream.Version.Qt_4_5)
    ds.writeRawData(b"QGis.MemoryLayerData")
    ds.writeUInt32(2)
    wkb = QgsGeometry.fromWkt("POINT(3 4)").asWkb()
    for lyr, rows in layers:
        ds.writeQString(lyr.id())
        ds.writeQString("")
        ds.writeInt16(2)
        for name, qtype, typename in (("a", QMetaType.Type.Int, "integer"), ("s", QMetaType.Type.QString, "string")):
            ds.writeQString(name)
            ds.writeInt16(int(qtype))
            ds.writeQString(typename)
            ds.writeInt16(0)
            ds.writeInt16(0)
            ds.writeQString("")
        for a, write_s in rows:
            ds.writeBool(True)
            ds.writeQVariant(a)
            write_s(ds)
            ds.writeUInt32(len(wkb))
            ds.writeRawData(wkb)
        ds.writeBool(False)
    qfile.close()


def text(value):
    return lambda ds: ds.writeQVariant(value)


def unset(ds):
    write_user_type(ds, b"QgsUnsetAttributeValue")


def interval(ds):
    write_user_type(ds, b"QgsInterval")


def list_with_unset(ds):
    ds.writeUInt32(9)
    ds.writeInt8(0)
    ds.writeUInt32(2)
    ds.writeQVariant("in list")
    unset(ds)


@test
def values_qt_could_not_store_are_read_as_null():
    """Qt writes values it has no stream operator for as their type only: reading stopped there,
    and every layer after it in the file stayed empty"""
    Settings.set_mldata_embedded(False)
    path = new_project("t", "qgs")
    a, b = layer("A", 0), layer("B", 0)
    proj.write(path)
    rows_a = [(1, text("x")), (2, unset), (3, text("z")), (4, interval)]
    write_v2(path + ".mldata", [(a, rows_a), (b, [(6, text("w"))])])
    reopen(path)
    assert not plugin.load_failed, dialogs
    got = {f["a"]: f["s"] for f in get("A").getFeatures()}
    assert got[1] == "x" and got[3] == "z", got
    assert not got[2] and not got[4], got
    assert [(f["a"], f["s"]) for f in get("B").getFeatures()] == [(6, "w")]


@test
def values_inside_lists_are_read_as_null():
    new_project("t")
    a, b = layer("A", 0), layer("B", 0)
    f = str(WORK / "list.mldata")
    write_v2(f, [(a, [(1, list_with_unset), (2, text("after"))]), (b, [(3, text("w"))])])
    values = []
    with Reader(f) as r:
        r._dstream.readQString()
        r._dstream.readQString()
        r._dstream.readInt16()
        for _i in range(2):
            for read in (r._dstream.readQString, r._dstream.readInt16, r._dstream.readQString):
                read()
            for read in (r._dstream.readInt16, r._dstream.readInt16, r._dstream.readQString):
                read()
        while r._dstream.readBool():
            values.append([r.read_value(), r.read_value()])
            r.read_wkb()
    assert values == [[1, ["in list", None]], [2, "after"]], values


@test
def refused_features_are_loaded_with_null_values():
    """The memory provider refuses a text in an integer field: the feature was silently lost"""
    Settings.set_mldata_embedded(False)
    path = new_project("t", "qgs")
    a, b = layer("A", 0), layer("B", 0)
    proj.write(path)
    rows_a = [(1, text("x")), ("not a number", text("y")), (3, text("z"))]
    write_v2(path + ".mldata", [(a, rows_a), (b, [(4, text("w"))])])
    before = Path(path + ".mldata").read_bytes()
    reopen(path)
    got = sorted((f["s"], f["a"] if f["a"] else None) for f in get("A").getFeatures())
    assert got == [("x", 1), ("y", None), ("z", 3)], got
    assert counts()["B"] == 1
    assert all(f.hasGeometry() for f in get("A").getFeatures())
    # A value is missing: the file must not be replaced without asking
    assert plugin.load_failed
    assert any("needed changes" in t for _k, t in dialogs), dialogs
    proj.write(path)
    assert Path(path + ".mldata").read_bytes() == before


@test
def unknown_unreadable_type_is_reported_and_protected():
    Settings.set_mldata_embedded(False)
    path = new_project("t", "qgs")
    a, b = layer("A", 0), layer("B", 0)
    proj.write(path)
    write_v2(
        path + ".mldata", [(a, [(1, text("x")), (2, lambda ds: write_user_type(ds, b"QgsSomethingElse"))]), (b, [])]
    )
    before = Path(path + ".mldata").read_bytes()
    reopen(path)
    assert plugin.load_failed
    assert any("QgsSomethingElse" in t for _k, t in dialogs), dialogs
    proj.write(path)
    assert Path(path + ".mldata").read_bytes() == before


@test
def unset_values_are_saved_as_null():
    from qgis.core import QgsUnsetAttributeValue

    path = new_project("t")
    a = layer("A", 0)
    f = QgsFeature(a.fields())
    f.setAttributes([5, QgsUnsetAttributeValue("'x'")])
    if not a.dataProvider().addFeatures([f])[0]:
        # QGIS 3.40 refuses them, so they cannot be in a layer there
        assert Qgis.QGIS_VERSION_INT < 34400
        return
    layer("B", 3)
    proj.write(path)
    reopen(path)
    assert not plugin.load_failed, dialogs
    assert counts() == {"A": 1, "B": 3}, counts()
    got = next(get("A").getFeatures())
    assert got["a"] == 5 and not isinstance(got["s"], QgsUnsetAttributeValue) and not got["s"], got.attributes()


@test
def values_qt_cannot_store_are_never_written():
    from qgis.core import QgsInterval, QgsUnsetAttributeValue

    new_project("t")
    a = layer("A", 0)
    field = a.fields()[1]
    w = Writer(str(WORK / "unstreamable.mldata"))
    w.open()
    try:
        assert w.streamable(QgsUnsetAttributeValue(), a, field) is None
        assert w.streamable([1, QgsUnsetAttributeValue()], a, field) == [1, None]
        assert w.streamable({"k": QgsUnsetAttributeValue()}, a, field) == {"k": None}
        for value in (QgsInterval(60), [QgsInterval(60)]):
            try:
                w.streamable(value, a, field)
                raise AssertionError("should have raised")
            except OSError as e:
                assert "QgsInterval" in str(e) and "Layer A, field s" in str(e), e
    finally:
        w.close()


def reset():
    proj.clear()
    dialogs.clear()
    slot_errors.clear()
    iface.bar.messages.clear()
    answer["value"] = QMessageBox.StandardButton.No
    Settings.set_mldata_embedded(True)


print("QGIS", Qgis.QGIS_VERSION)
failed = 0
for fn in tests:
    reset()
    try:
        fn()
        print("PASS", fn.__name__)
    except Exception as e:
        failed += 1
        print("FAIL", fn.__name__, "-", f"{type(e).__name__}: {e}"[:200])
        if "-v" in sys.argv:
            print(traceback.format_exc(limit=5))
    sys.stdout.flush()
print(f"{len(tests) - failed}/{len(tests)} passed")
sys.stdout.flush()
os._exit(1 if failed else 0)  # QGIS teardown is irrelevant to the results
