"""集中式 QSS 深色主题（§11.6 夜间裁定）。

全部控件只走这一份 QSS：theme.py 之外禁止散落 setStyleSheet（§11.2 坑 4）。
色板/栅格/圆角常量集中定义，测试与将来主题微调引用同一来源。
"""
from __future__ import annotations

# —— 设计色板：深色底 → 层次面 → 悬浮面 → 前景 → 次要前景 → 强调/危险/成功 ——
BG = "#1e1f22"
SURFACE = "#2b2d31"
OVERLAY = "#313338"
TEXT = "#e6e6e6"
TEXT_DIM = "#9aa0a6"
ACCENT = "#4f8cff"
DANGER = "#e5566d"
OK = "#3fb27f"

# —— 间距栅格与圆角规范（§11.6）——
GRID = (4, 8, 12, 16)
RADIUS_SM = 6
RADIUS_MD = 8

DARK_QSS = f"""
QMainWindow, QDialog {{ background: {BG}; color: {TEXT}; }}
QWidget {{ background: transparent; color: {TEXT}; font-size: 13px; }}
QLabel {{ background: transparent; color: {TEXT}; }}
QLabel#dim {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#page-title {{ font-size: 16px; font-weight: 600; padding: 4px 0 12px 0; }}
QPushButton {{
    background: {SURFACE}; color: {TEXT};
    border: 1px solid {OVERLAY}; border-radius: {RADIUS_SM}px;
    padding: 8px 12px;
}}
QPushButton:hover {{ background: {OVERLAY}; }}
QPushButton:pressed {{ background: {BG}; }}
QPushButton#primary {{ background: {ACCENT}; color: #ffffff; border: none; }}
QPushButton#primary:hover {{ background: #3f78e0; }}
QPushButton#danger {{ background: transparent; color: {DANGER}; border: 1px solid {DANGER}; }}
QPushButton:pressed {{ background: #3a3d42; padding-top: 9px; }}
QPushButton#primary:pressed {{ background: #2f66c0; padding-top: 9px; }}
QLineEdit, QSpinBox {{
    background: {SURFACE}; color: {TEXT};
    border: 1px solid {OVERLAY}; border-radius: {RADIUS_SM}px;
    padding: 4px 8px; selection-background-color: {ACCENT};
}}
QListWidget, QTableView, QScrollArea {{
    background: {BG}; color: {TEXT};
    border: 1px solid {OVERLAY}; border-radius: {RADIUS_MD}px;
    padding: 4px;
}}
QListWidget#nav {{ border: none; background: transparent; }}
QListWidget::item {{ padding: 8px; border-radius: {RADIUS_SM}px; }}
QListWidget::item:selected {{ background: {SURFACE}; color: {TEXT}; }}
QListWidget::item:selected:!active {{ color: {TEXT_DIM}; }}
QListWidget::item:selected:active {{ background: {ACCENT}; color: #ffffff; }}
QTableView:!active {{ selection-background-color: {OVERLAY}; }}
QTableView {{ gridline-color: {OVERLAY}; selection-background-color: {SURFACE}; selection-color: {TEXT}; }}
QPlainTextEdit, QTextBrowser {{
    background: {BG}; color: {TEXT};
    border: 1px solid {OVERLAY}; border-radius: {RADIUS_MD}px;
    padding: 12px; selection-background-color: {ACCENT};
}}
QHeaderView::section {{
    background: {SURFACE}; color: {TEXT_DIM};
    border: none; border-bottom: 1px solid {OVERLAY};
    padding: 8px 12px;
}}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM};
    padding: 8px 16px; border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QProgressBar {{
    background: {SURFACE}; border: none; border-radius: {RADIUS_SM}px;
    text-align: center; color: {TEXT}; min-height: 8px; max-height: 8px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: {RADIUS_SM}px; }}
QMenu {{
    background: {SURFACE}; color: {TEXT};
    border: 1px solid {OVERLAY}; border-radius: {RADIUS_MD}px; padding: 4px;
}}
QMenu::item {{ padding: 8px 16px; border-radius: {RADIUS_SM}px; }}
QMenu::item:selected {{ background: {OVERLAY}; }}
QMenu::separator {{ height: 1px; background: {OVERLAY}; margin: 4px 8px; }}
QStatusBar {{ background: {BG}; color: {TEXT_DIM}; border-top: 1px solid {OVERLAY}; }}
QToolTip {{ background: {OVERLAY}; color: {TEXT}; border: 1px solid {OVERLAY}; padding: 4px 8px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {OVERLAY}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; }}
QScrollBar::handle:horizontal {{ background: {OVERLAY}; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""


def apply_dark_theme(app) -> None:
    """把集中 QSS 挂到应用（全仓库唯一一处 setStyleSheet，美观不阻塞骨架）。"""
    app.setStyleSheet(DARK_QSS)
