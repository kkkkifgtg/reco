"""RankRow 表格模型（工单 12 切片 2；A6/B4）。

GUI 侧零排序：模型只按 rank() 的输出顺序逐行渲染（§10）；
批更新走 beginResetModel/endResetModel（§11.2 坑 6）；
理由列只渲染 axis/contribution/source（B4，无正文）。
"""
from __future__ import annotations

from reco_ui.qt import (
    QAbstractTableModel,
    QModelIndex,
    Qt,
)

EXPLORATION_MARK = "🧭"  # 探索位视觉标记（§10：is_exploration 行加标记）

_HEADERS = ("#", "书名", "分数", "探索", "理由")


def render_reasons(reasons) -> str:
    """B4：理由行 = axis + 权重贡献 + 来源机制；不含任何原文。"""
    parts = [f"{r.axis} {float(r.contribution):+.3f} ({r.source})" for r in reasons]
    return "；".join(parts) if parts else "—"


class RankTableModel(QAbstractTableModel):
    """rank() 输出的只读表格模型；零重排、零补算（A6）。"""

    def __init__(self, rows=(), names=None, parent=None) -> None:
        super().__init__(parent)
        self._rows = list(rows)
        self._names: dict = dict(names or {})
        self._reset_calls = 0

    # —— Qt model 契约 ——
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(_HEADERS):
                return _HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self._rows[index.row()]
        col = index.column()
        if col == 0:
            return str(row.rank)
        if col == 1:
            return self._names.get(str(row.book_id), str(row.book_id))
        if col == 2:
            return f"{float(row.score):.3f}"
        if col == 3:
            return EXPLORATION_MARK if row.is_exploration else ""
        if col == 4:
            return render_reasons(row.reasons)
        return None

    # —— 批更新与选取辅助 ——
    def set_rows(self, rows, names=None) -> None:
        """整批替换（§11.2 坑 6：model/view 批更新，非逐行 insert）。"""
        self.beginResetModel()
        self._rows = list(rows)
        if names is not None:
            self._names = dict(names)
        self._reset_calls += 1
        self.endResetModel()

    def book_id_at(self, row: int):
        if 0 <= row < len(self._rows):
            return str(self._rows[row].book_id)
        return None
