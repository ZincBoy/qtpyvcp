
import os

from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QApplication

from PySide6.QtDesigner import QDesignerFormWindowInterface


from qtpyvcp.widgets.qtdesigner import _PluginExtension

UI_FILE = os.path.join(os.path.dirname(__file__), "dro_editor.ui")


class DroEditorExtension(_PluginExtension):
    def __init__(self, widget):
        super(DroEditorExtension, self).__init__(widget)
        self.widget = widget
        self.addTaskMenuAction("Edit DRO Settings ...", self.editAction)

    def editAction(self, state):
        DroEditor(self.widget, parent=None).exec_()


class DroEditor(QDialog):
    """QDialog for user-friendly editing of DRO properties in Qt Designer."""

    def __init__(self, widget, parent=None):
        super(DroEditor, self).__init__(parent)

        self.widget = widget
        self.app = QApplication.instance()

        file_path = os.path.join(os.path.dirname(__file__), UI_FILE)
        ui_file = QFile(file_path)
        ui_file.open(QFile.ReadOnly)

        loader = QUiLoader()
        self.ui = loader.load(ui_file, self)
        self.ui.show()

        self.axisCombo.setCurrentIndex(self.widget.axisNumber)
        self.refTypCombo.setCurrentIndex(self.widget.referenceType)

        self.inFmtEntry.setText(self.widget.inchFormat)
        self.mmFmtEntry.setText(self.widget.millimeterFormat)
        self.degFmtEntry.setText(self.widget.degreeFormat)

        self.latheModeCombo.setCurrentIndex(self.widget.latheMode)

        bb = self.buttonBox
        bb.button(QDialogButtonBox.Apply).setDefault(True)
        bb.button(QDialogButtonBox.Cancel).setDefault(False)
        bb.button(QDialogButtonBox.Apply).clicked.connect(self.accept)

    @Slot()
    def accept(self):
        """Commit changes"""
        # general options
        self.setCursorProperty('axisNumber', self.axisCombo.currentIndex())
        self.setCursorProperty('referenceType', self.refTypCombo.currentIndex())

        # format options
        self.setCursorProperty('inchFormat', self.inFmtEntry.text())
        self.setCursorProperty('millimeterFormat', self.mmFmtEntry.text())
        self.setCursorProperty('degreeFormat', self.degFmtEntry.text())

        # lathe options
        self.setCursorProperty('latheMode', self.latheModeCombo.currentIndex())

        self.close()

    def setCursorProperty(self, prop_name, value):
        form = QDesignerFormWindowInterface.findFormWindow(self.widget)
        if form:
            form.cursor().setProperty(prop_name, value)
