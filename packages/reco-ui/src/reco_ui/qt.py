"""PyQt6 许可隔离薄适配层（§11.5）。

全仓库唯一允许 import PyQt6 的模块：其余代码只从这里再导出，
将来切换 PySide6（LGPLv3）时只改本文件。
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

__all__ = [
    "QAbstractTableModel",
    "QAbstractItemView",
    "QAction",
    "QApplication",
    "QColor",
    "QFrame",
    "QGridLayout",
    "QHBoxLayout",
    "QIcon",
    "QModelIndex",
    "QLabel",
    "QLineEdit",
    "QListWidget",
    "QListWidgetItem",
    "QMainWindow",
    "QMenu",
    "QObject",
    "QPainter",
    "QPalette",
    "QPixmap",
    "QProgressBar",
    "QPushButton",
    "QRunnable",
    "QScrollArea",
    "QSizePolicy",
    "QSpinBox",
    "QStackedWidget",
    "QTableView",
    "QThreadPool",
    "QTimer",
    "Qt",
    "QVBoxLayout",
    "QWidget",
    "pyqtSignal",
]
