"""Tool Table data plugin.

Exposes all the info available in the tool table. Watches the
tool table file for changes and re-loads as needed.

Tool Table YAML configuration:

.. code-block:: yaml

    data_plugins:
      tooltable:
        kwargs:
          # specify the columns that should be read and writen to the
          # tooltable file. To use all columns set to: TPXYZABCUVWDIJQR
          columns: PTDZR
          # specify text to be added before the tool table data
          file_header_template: |
            LinuxCNC Tool Table
            -------------------

            QtPyVCP will preserve comments before the opening semicolon.
"""

import os
import re
from itertools import takewhile
from datetime import datetime

import linuxcnc

from qtpy.QtCore import QFileSystemWatcher, QTimer, Signal, Slot

import qtpyvcp
from qtpyvcp.utilities.info import Info
from qtpyvcp.utilities.logger import getLogger
from qtpyvcp.actions.machine_actions import issue_mdi
from qtpyvcp.plugins import DataPlugin, DataChannel, getPlugin

CMD = linuxcnc.command()
LOG = getLogger(__name__)
STATUS = getPlugin('status')
STAT = STATUS.stat
INFO = Info()

IN_DESIGNER = os.getenv('DESIGNER', False)


def merge(a, b):
    """Shallow merge two dictionaries"""
    r = a.copy()
    r.update(b)
    return r


ALL_COLUMNS = ('T', 'P',
               'X', 'Y', 'Z',
               'A', 'B', 'C',
               'U', 'V', 'W',
               'D', 'I', 'J',
               'Q', 'R',
               'STL', 'COLOR')

DEFAULT_TOOL = {
    'A': 0.0,
    'B': 0.0,
    'C': 0.0,
    'D': 0.0,
    'I': 0.0,
    'J': 0.0,
    'P': 0,
    'Q': 1,
    'T': -1,
    'U': 0.0,
    'V': 0.0,
    'W': 0.0,
    'X': 0.0,
    'Y': 0.0,
    'Z': 0.0,
    'R': '',
    'STL': '',
    'COLOR': ''
}

NO_TOOL = merge(DEFAULT_TOOL, {'T': 0, 'R': 'No Tool Loaded'})

# FILE_HEADER = """
# LinuxCNC Tool Table
# -------------------
#
# (QtPyVCP will preserve any comments before this separator.)
# ---
# Generated by: QtPyVCP ToolTable plugin ({version})
# Generated on: {datetime:%x %I:%M:%S %p}
#
# """

COLUMN_LABELS = {
    'A': 'A Offset',
    'B': 'B Offset',
    'C': 'C Offset',
    'D': 'Diameter',
    'I': 'Fnt Ang',
    'J': 'Bak Ang',
    'P': 'Pocket',
    'Q': 'Orient',
    'R': 'Remark',
    'T': 'Tool',
    'U': 'U Offset',
    'V': 'V Offset',
    'W': 'W Offset',
    'X': 'X Offset',
    'Y': 'Y Offset',
    'Z': 'Z Offset',
    'STL': 'Tool STL Model',
    'COLOR': 'Tool Path Color'
}

# Column formats when writing tool table
INT_COLUMN_WIDTH = 6
FLOAT_COLUMN_WIDTH = 12
FLOAT_DECIMAL_PLACES = 6


def makeLorumIpsumToolTable():
    return {i: merge(DEFAULT_TOOL,
                     {'T': i, 'P': i, 'R': 'Lorum Ipsum ' + str(i)})
            for i in range(10)}


class ToolTable(DataPlugin):
    TOOL_TABLE = {0: NO_TOOL}
    DEFAULT_TOOL = DEFAULT_TOOL
    COLUMN_LABELS = COLUMN_LABELS

    tool_table_changed = Signal(dict)

    def __init__(self, columns=ALL_COLUMNS, file_header_template=None,
                 remember_tool_in_spindle=True):
        super(ToolTable, self).__init__()

        self.fs_watcher = None
        self.orig_header_lines = []
        self.file_header_template = file_header_template or ''
        self.remember_tool_in_spindle = remember_tool_in_spindle
        self.columns = self.validateColumns(columns) or [c for c in ALL_COLUMNS]

        self.data_manager = getPlugin('persistent_data_manager')

        self.setCurrentToolNumber(0)

        self.tool_table_file = INFO.getToolTableFile()
        if not os.path.exists(self.tool_table_file):
            return

        self.loadToolTable()

        self.current_tool.setValue(self.TOOL_TABLE[STATUS.tool_in_spindle.getValue()])

        # update signals
        STATUS.tool_in_spindle.notify(self.setCurrentToolNumber)
        STATUS.tool_table.notify(lambda *args: self.loadToolTable())

        STATUS.all_axes_homed.notify(self.reload_tool)

    def reload_tool(self):
        if self.remember_tool_in_spindle and STATUS.all_axes_homed.value and STATUS.enabled.value:
            tnum = self.data_manager.getData('tool-in-spindle', 0)
            LOG.debug("reload_tool: tool in spindle: %i new tool: %i" % (STAT.tool_in_spindle, tnum))
            if STAT.tool_in_spindle == 0 and tnum != STAT.tool_in_spindle:
                LOG.info("Reloading tool in spindle: %i", tnum)
                cmd = "M61 Q{0} G43".format(tnum)
                # give LinuxCNC time to switch modes
                QTimer.singleShot(200, lambda: issue_mdi(cmd))

    @DataChannel
    def current_tool(self, chan, item=None):
        """Current Tool Info

        Available items:

        * T -- tool number
        * P -- pocket number
        * X -- x offset
        * Y -- y offset
        * Z -- z offset
        * A -- a offset
        * B -- b offset
        * C -- c offset
        * U -- u offset
        * V -- v offset
        * W -- w offset
        * I -- front angle
        * J -- back angle
        * Q -- orientation
        * R -- remark
        * STL -- tool stl model
        * COLOR -- tool path color

        Rules channel syntax::

            tooltable:current_tool
            tooltable:current_tool?X
            tooltable:current_tool?x_offset

        :param item: the name of the tool data item to get
        :return: dict, int, float, str
        """
        if item is None:
            return self.TOOL_TABLE[STAT.tool_in_spindle]
        return self.TOOL_TABLE[STAT.tool_in_spindle].get(item[0].upper())

    def initialise(self):
        self.fs_watcher = QFileSystemWatcher()
        self.fs_watcher.addPath(self.tool_table_file)
        self.fs_watcher.fileChanged.connect(self.onToolTableFileChanged)

    def terminate(self):
        self.data_manager.setData('tool-in-spindle', STAT.tool_in_spindle)

    @staticmethod
    def validateColumns(columns):
        """Validate display column specification.

        The user can specify columns in multiple ways, method is used to make
        sure that that data is validated and converted to a consistent format.

        Args:
            columns (str | list) : A string or list of the column IDs
                that should be shown in the tooltable.

        Returns:
            None if not valid, else a list of uppercase column IDs.
        """
        if not isinstance(columns, (basestring, list, tuple)):
            return

        return [col for col in [col.strip().upper() for col in columns]
                if col in ALL_COLUMNS and not col == '']

    def newTool(self, tnum=None):
        """Get a dict of default tool values for a new tool."""
        if tnum is None:
            tnum = len(self.TOOL_TABLE)
        new_tool = DEFAULT_TOOL.copy()
        new_tool.update({'T': tnum, 'R': 'New Tool'})
        return new_tool

    def onToolTableFileChanged(self, path):
        LOG.debug('Tool Table file changed: {}'.format(path))
        # ToolEdit deletes the file and then rewrites it, so wait
        # a bit to ensure the new data has been writen out.
        QTimer.singleShot(50, self.reloadToolTable)

    def setCurrentToolNumber(self, tool_num):
        self.current_tool.setValue(self.TOOL_TABLE[tool_num])

    def reloadToolTable(self):
        # rewatch the file if it stop being watched because it was deleted
        if self.tool_table_file not in self.fs_watcher.files():
            self.fs_watcher.addPath(self.tool_table_file)

        # reload with the new data
        tool_table = self.loadToolTable()
        self.tool_table_changed.emit(tool_table)

    def iterTools(self, tool_table=None, columns=None):
        tool_table = tool_table or self.TOOL_TABLE
        columns = self.validateColumns(columns) or self.columns
        for tool in sorted(tool_table.iterkeys()):
            tool_data = tool_table[tool]
            yield [tool_data[key] for key in columns]

    def loadToolTable(self, tool_file=None):

        if tool_file is None:
            tool_file = self.tool_table_file

        if not os.path.exists(tool_file):
            if IN_DESIGNER:
                lorum_tooltable = makeLorumIpsumToolTable()
                self.current_tool.setValue(lorum_tooltable)
                return lorum_tooltable
            LOG.critical("Tool table file does not exist: {}".format(tool_file))
            return {}

        with open(tool_file, 'r') as fh:
            lines = [line.strip() for line in fh.readlines()]

        # find opening colon, and get header data so it can be restored
        for rlnum, line in enumerate(reversed(lines)):
            if line.startswith(';'):
                lnum = len(lines) - rlnum
                raw_header = lines[:lnum]
                lines = lines[lnum:]

                self.orig_header_lines = list(takewhile(lambda l:
                                                        not l.strip() == '---' and
                                                        not l.startswith(';Tool'), raw_header))
                break

        table = {0: NO_TOOL, }
        for line in lines:

            data, sep, comment = line.partition(';')

            tool_model = re.findall(r"\[([.*?]+)]", comment.lower())
            path_color = re.findall(r"\[([#A-F0-9_]+)]", comment.upper())
            print(tool_model)
            print(path_color)

            items = re.findall(r"([A-Z]+[0-9.+-]+)", data.replace(' ', ''))

            tool = DEFAULT_TOOL.copy()
            for item in items:
                descriptor = item[0]
                if descriptor in ALL_COLUMNS:
                    value = item[1:]
                    if descriptor in ('T', 'P', 'Q'):
                        try:
                            tool[descriptor] = int(value)
                        except:
                            LOG.error('Error converting value to int: {}'.format(value))
                            break
                    else:
                        try:
                            tool[descriptor] = float(value)
                        except:
                            LOG.error('Error converting value to float: {}'.format(value))
                            break

            tool['R'] = comment.strip()

            tnum = tool['T']
            if tnum == -1:
                continue

            # add the tool to the table
            table[tnum] = tool

        # update tooltable
        self.__class__.TOOL_TABLE = table

        self.current_tool.setValue(self.TOOL_TABLE[STATUS.tool_in_spindle.getValue()])

        # import json
        # print json.dumps(table, sort_keys=True, indent=4)

        self.tool_table_changed.emit(table)
        return table.copy()

    def getToolTable(self):
        return self.TOOL_TABLE.copy()

    def saveToolTable(self, tool_table, columns=None, tool_file=None):
        """Write tooltable data to file.

        Args:
            tool_table (dict) : Dictionary of dictionaries containing
                the tool data to write to the file.
            columns (str | list) : A list of data columns to write.
                If `None` will use the value of ``self.columns``.
            tool_file (str) : Path to write the tooltable too.
                Defaults to ``self.tool_table_file``.
        """

        columns = self.validateColumns(columns) or self.columns
        print(columns)

        if tool_file is None:
            tool_file = self.tool_table_file

        lines = []
        header_lines = []

        # restore file header
        if self.file_header_template:
            try:
                header_lines = self.file_header_template.format(
                    version=qtpyvcp.__version__,
                    datetime=datetime.now()).lstrip().splitlines()
                header_lines.append('')  # extra new line before table header
            except:
                pass

        if self.orig_header_lines:
            try:
                self.orig_header_lines.extend(header_lines[header_lines.index('---'):])
                header_lines = self.orig_header_lines
            except ValueError:
                header_lines = self.orig_header_lines

        lines.extend(header_lines)

        # create the table header
        items = []
        for col in columns:
            if col == 'R':
                continue
            w = (INT_COLUMN_WIDTH if col in 'TPQ' else FLOAT_COLUMN_WIDTH) - \
                (1 if col == self.columns[0] else 0)
            items.append('{:<{w}}'.format(COLUMN_LABELS[col], w=w))

        items.append('Remark')
        lines.append(';' + ' '.join(items))

        # add the tools
        for tool_num in sorted(tool_table.iterkeys())[1:]:
            items = []
            tool_data = tool_table[tool_num]
            for col in columns:
                if col == 'R':
                    continue
                if col in 'TPQ':
                    items.append('{col}{val:<{w}}'
                                 .format(col=col,
                                         val=tool_data[col],
                                         w=INT_COLUMN_WIDTH))
                else:
                    items.append('{col}{val:<+{w}.{d}f}'
                                 .format(col=col,
                                         val=tool_data[col],
                                         w=FLOAT_COLUMN_WIDTH,
                                         d=FLOAT_DECIMAL_PLACES))

            comment = tool_data.get('R', '')
            if comment is not '':
                items.append('; ' + comment)

            lines.append(''.join(items))

        # for line in lines:
        #     print line

        # write to file
        with open(tool_file, 'w') as fh:
            fh.write('\n'.join(lines))
            fh.write('\n')  # new line at end of file
            fh.flush()
            os.fsync(fh.fileno())

        CMD.load_tool_table()
