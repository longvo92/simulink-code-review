"""Change minimap: the whole file compressed to a thin column so every change
is visible at a glance, with a viewport box and click/drag to jump.

One minimap covers both panes: ``aligned_rows`` gives a single row sequence
shared by old and new, so row *i* is the same height position on both sides.
The map is driven off the old editor (its scrollbar mirrors the new one), and
jumping centres that editor -- the mirror carries the new pane along.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QTextCursor
from PySide6.QtWidgets import QWidget

_WIDTH = 16
_BG = '#1a1b1e'
# mark colour per change mode (ctx rows draw nothing)
_MARK = {'real': '#d9524f', 'minor': '#c8a030', 'moved': '#3f7fb0'}
_VIEW_FILL = QColor(255, 255, 255, 28)
_VIEW_BORDER = QColor(190, 190, 190, 130)


class Minimap(QWidget):
    def __init__(self, editor):
        super().__init__()
        self._editor = editor
        self._rows = []
        self.setFixedWidth(_WIDTH)
        self.setCursor(Qt.PointingHandCursor)
        # repaint the viewport box whenever the driven editor scrolls or grows
        editor.verticalScrollBar().valueChanged.connect(self.update)
        editor.blockCountChanged.connect(lambda _n: self.update())

    def set_rows(self, rows):
        self._rows = rows
        self.update()

    # --- painting ---

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(_BG))
        n = len(self._rows)
        if not n:
            return
        h = max(self.height(), 1)
        w = self.width()
        mark_h = max(2.0, h / n)
        for i, r in enumerate(self._rows):
            color = _MARK.get(r.mode)
            if not color:
                continue
            y = i / n * h
            painter.fillRect(1, int(y), w - 2, int(mark_h) + 1, QColor(color))
        self._paint_viewport(painter, n, h, w)

    def _paint_viewport(self, painter, n, h, w):
        first = self._editor.firstVisibleBlock().blockNumber()
        count = self._visible_rows()
        y0 = first / n * h
        y1 = min(first + count, n) / n * h
        painter.fillRect(0, int(y0), w, int(y1 - y0), _VIEW_FILL)
        painter.setPen(_VIEW_BORDER)
        painter.drawRect(0, int(y0), w - 1, max(int(y1 - y0) - 1, 1))

    def _visible_rows(self):
        block = self._editor.firstVisibleBlock()
        bh = self._editor.blockBoundingRect(block).height() or self._editor.fontMetrics().height()
        return max(1, int(self._editor.viewport().height() / bh))

    # --- interaction ---

    def mousePressEvent(self, event):
        self._jump(event.position().y())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._jump(event.position().y())

    def _jump(self, y):
        n = len(self._rows)
        if not n:
            return
        row = int(y / max(self.height(), 1) * n)
        row = max(0, min(n - 1, row))
        block = self._editor.document().findBlockByNumber(row)
        self._editor.setTextCursor(QTextCursor(block))
        self._editor.centerCursor()
