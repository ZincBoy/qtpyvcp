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
import sys
import re
import io

from itertools import takewhile
from datetime import datetime

from deepdiff import DeepDiff

from  linuxcnc import command

from qtpy.QtCore import QFileSystemWatcher, QTimer, Signal, Slot

import qtpyvcp

from qtpyvcp.lib.db_tool.base import Session, Base, engine
from qtpyvcp.lib.db_tool.tool_table import ToolTable, Tool

from qtpyvcp.utilities.info import Info
from qtpyvcp.utilities.logger import getLogger
from qtpyvcp.actions.machine_actions import issue_mdi
from qtpyvcp.plugins import DataPlugin, DataChannel, getPlugin

from pprint import pprint

CMD = command()
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
}

# Column formats when writing tool table
INT_COLUMN_WIDTH = 6
FLOAT_COLUMN_WIDTH = 12
FLOAT_DECIMAL_PLACES = 6


def makeLorumIpsumToolTable():
    return {i: merge(DEFAULT_TOOL,
                     {'T': i, 'P': i, 'R': 'Lorum Ipsum ' + str(i)})
            for i in range(10)}


class DBToolTable(DataPlugin):

    TOOL_TABLE = {0: NO_TOOL}
    DEFAULT_TOOL = DEFAULT_TOOL
    COLUMN_LABELS = COLUMN_LABELS

    tool_table_changed = Signal(dict)

    def __init__(self, columns='TPXYZABCUVWDIJQR', file_header_template=None,
                 remember_tool_in_spindle=True):
        super(DBToolTable, self).__init__()

        Base.metadata.create_all(engine)
        
        self.table = dict()
        # self.fs_watcher = None
        # self.orig_header_lines = []
        # self.file_header_template = file_header_template or ''
        # self.remember_tool_in_spindle = remember_tool_in_spindle
        self.columns = self.validateColumns(columns) or [c for c in 'TPXYZABCUVWDIJQR']
        #
        # self.data_manager = getPlugin('persistent_data_manager')
        #
        # self.setCurrentToolNumber(0)
        #
        # self.tool_table_file = INFO.getToolTableFile()
        # if not os.path.exists(self.tool_table_file):
        #     return
        #
        
        #
        # self.current_tool.setValue(self.TOOL_TABLE[STATUS.tool_in_spindle.getValue()])
        #
        # # update signals
        # STATUS.tool_in_spindle.notify(self.setCurrentToolNumber)
        # STATUS.tool_table.notify(lambda *args: self.loadToolTable())
        #
        # STATUS.all_axes_homed.notify(self.reload_tool)
        
        # CMD.load_tool_table()

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

        Rules channel syntax::

            tooltable:current_tool
            tooltable:current_tool?X
            tooltable:current_tool?x_offset

        :param item: the name of the tool data item to get
        :return: dict, int, float, str
        """
        
        tool_in_spindle = STAT.tool_in_spindle
                
        if item is None:
            return self.table[tool_in_spindle]
        return self.table[tool_in_spindle].get(item[0].upper())

    def initialise(self):
        self.session = Session()
        self.loadToolTable()

    def terminate(self):
        self.session.close()

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
        if not isinstance(columns, (str, list, tuple)):
            return

        return [col for col in [col.strip().upper() for col in columns]
                if col in 'TPXYZABCUVWDIJQR' and not col == '']

    def newTool(self, tnum=None):
        """Get a dict of default tool values for a new tool."""
        if tnum is None:
            tnum = len(self.TOOL_TABLE)
        new_tool = DEFAULT_TOOL.copy()
        new_tool.update({'T': tnum, 'R': 'New Tool'})
        return new_tool

    def setCurrentToolNumber(self, tool_num):
        self.current_tool.setValue(self.TOOL_TABLE[tool_num])

    def reloadToolTable(self):
        CMD.load_tool_table()
        self.loadToolTable()
        
    def loadToolTable(self): 
        print("Loads ToolTable")
        
        tool_list = self.session.query(Tool).all()
        
        for tool in tool_list:
                   
            self.table[tool.tool_no] = {'A': tool.a_offset,
                                        'B': tool.b_offset,
                                        'C': tool.b_offset,
                                        'D': tool.diameter,
                                        'I': 0.0,
                                        'J': 0.0,
                                        'P': tool.pocket,
                                        'Q': 1,
                                        'R': tool.remark,
                                        'T': tool.tool_no,
                                        'U': tool.u_offset,
                                        'V': tool.v_offset,
                                        'W': tool.w_offset,
                                        'X': tool.x_offset,
                                        'Y': tool.y_offset,
                                        'Z': tool.z_offset}

        # CMD.load_tool_table()
        
        self.tool_table_changed.emit(self.table.copy())     

    def getToolTable(self):
        return self.table.copy()
    
    def saveToolTable(self, tool_table, columns=None):
        """Write tooltable data to db.

        Args:
            tool_table (dict) : Dictionary of dictionaries containing
                the tool data to write to the file.
            columns (str | list) : A list of data columns to write.
                If `None` will use the value of ``self.columns``.
            tool_file (str) : Path to write the tooltable too.
                Defaults to ``self.tool_table_file``.
        """
        
        self.table = tool_table
        
        tool_data = self.session.query(Tool)
        
        new_tool_dict = dict()
        
        for tool in tool_data:
            tool_dict = dict()
            
            tool_dict['R'] = tool.remark
            tool_dict['T'] = tool.tool_no
            tool_dict['P'] = tool.pocket
            tool_dict['X'] = tool.x_offset
            tool_dict['Y'] = tool.y_offset
            tool_dict['Z'] = tool.z_offset
            tool_dict['A'] = tool.a_offset
            tool_dict['B'] = tool.b_offset
            tool_dict['C'] = tool.c_offset
            tool_dict['U'] = tool.u_offset
            tool_dict['V'] = tool.v_offset
            tool_dict['W'] = tool.w_offset
            tool_dict['Q'] = 1
            tool_dict['I'] = 0.0
            tool_dict['J'] = 0.0
            tool_dict['D'] = tool.diameter
            
            new_tool_dict[tool.tool_no] = tool_dict

        diff = DeepDiff(new_tool_dict, self.table, view="tree")
                #
        to_insert = diff.get("dictionary_item_added")
        to_update = diff.get("values_changed")
        to_delete = diff.get("dictionary_item_removed")
        
        if to_insert is not None:
            print("TO INSTERT")
            for a in to_insert:
                temp_tool = a.t2
                tool = Tool(remark=temp_tool['R'],
                         tool_no=temp_tool['T'],
                         in_use=False,
                         pocket=temp_tool['P'],
                         x_offset=temp_tool['X'],
                         y_offset=temp_tool['Y'],
                         z_offset=temp_tool['Z'],
                         a_offset=temp_tool['A'],
                         b_offset=temp_tool['B'],
                         c_offset=temp_tool['C'],
                         i_offset=temp_tool['I'],
                         j_offset=temp_tool['J'],
                         q_offset=temp_tool['Q'],
                         u_offset=temp_tool['U'],
                         v_offset=temp_tool['V'],
                         w_offset=temp_tool['W'],
                         diameter=temp_tool['D'],
                         tool_table_id=1
                    )
                self.session.add(tool)
                self.session.commit()
                
        if to_update is not None:
            print("TO UPDATE")
            for a in to_update:
                temp_tool = a.up.t2
                
                tool_data = self.session.query(Tool).filter(Tool.tool_no == temp_tool['T']).one()
                
                tool_data.remark = temp_tool['R']
                tool_data.tool_no = temp_tool['T']
                tool_data.in_use = False
                tool_data.pocket = temp_tool['P']
                tool_data.x_offset = temp_tool['X']
                tool_data.y_offset = temp_tool['Y']
                tool_data.z_offset = temp_tool['Z']
                tool_data.a_offset = temp_tool['A']
                tool_data.b_offset = temp_tool['B']
                tool_data.c_offset = temp_tool['C']
                tool_data.i_offset = temp_tool['I']
                tool_data.j_offset = temp_tool['J']
                tool_data.q_offset = temp_tool['Q']
                tool_data.u_offset = temp_tool['U']
                tool_data.v_offset = temp_tool['V']
                tool_data.w_offset = temp_tool['W']
                tool_data.diameter = temp_tool['D']
                tool_data.tool_table_id = 1
                
                self.session.commit()

        if to_delete is not None:
            print("TO DELETE")
            for a in to_delete:
                temp_tool = a.t1
                tool_row = tool_data.filter(Tool.tool_no == temp_tool['T']).one()
                self.session.delete(tool_row)
                self.session.commit()
                
        self.session.close()
