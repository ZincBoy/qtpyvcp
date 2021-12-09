#   Copyright (c) 2018 Kurt Jacobson
#      <kurtcjacobson@gmail.com>
#
#   This file is part of QtPyVCP.
#
#   QtPyVCP is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 2 of the License, or
#   (at your option) any later version.
#
#   QtPyVCP is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with QtPyVCP.  If not, see <http://www.gnu.org/licenses/>.

import subprocess

from PySide2.QtCore import QTimer
from PySide2.QtWidgets import QPushButton, QHBoxLayout, QPushButton, QWidget

from qtpyvcp.utilities.info import Info
from qtpyvcp.utilities import logger

Log = logger.getLogger(__name__)


class ProbeSim(QWidget):

    def __init__(self, parent=None):
        super(ProbeSim, self).__init__(parent=parent)

        self.info = Info()
        self.log = Log

        self.close_button = QPushButton("Touch")
        self.close_button.setCheckable(False)
        self.close_button.setAutoExclusive(False)
        self.pulse_button = QPushButton("Pulse")
        self.pulse_button.setCheckable(True)
        self.pulse_button.setAutoExclusive(False)     


        main_layout = QHBoxLayout()

        main_layout.addWidget(self.close_button)
        main_layout.addWidget(self.pulse_button)

        self.setLayout(main_layout)
        self.setWindowTitle("Simulate touch probe")

        self.close_button.pressed.connect(self.touch_on)
        self.close_button.released.connect(self.touch_off)

        self.timer = QTimer()
        self.timer.timeout.connect(self.pulse_off)
        self.timer.setSingleShot(True)

    def touch_on(self):

        if self.pulse_button.isChecked():
            self.timer.start(1000)
            subprocess.Popen(['halcmd', 'setp', 'motion.probe-input', '1'])
            
        else:
            subprocess.Popen(['halcmd', 'setp', 'motion.probe-input', '1'])

    def touch_off(self):

        if self.pulse_button.isChecked():
            return

        subprocess.Popen(['halcmd', 'setp', 'motion.probe-input', '0'])

    def pulse_off(self):
        subprocess.Popen(['halcmd', 'setp', 'motion.probe-input', '0'])
