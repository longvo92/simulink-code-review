"""Right-hand side of the viewer: the two-pane diff.

Phase 1 stub -- a single label naming the selected file and its verdict. The
side-by-side old/new panes, synchronized scrolling and the minimap arrive in
Phase 2/3, replacing this body while keeping ``show_file`` / ``clear`` as the
stable seam the main window calls.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

_HINT = 'Select a file in the tree to view its diff.'


class DiffPane(QWidget):
    def __init__(self):
        super().__init__()
        self.label = QLabel(_HINT)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addWidget(self.label)

    def show_file(self, rel, result, old_root, new_root):
        self.label.setText('{}\n\nstatus: {}\n\n(side-by-side diff arrives in '
                           'Phase 2)'.format(rel, result.get('status', '?')))

    def clear(self):
        self.label.setText(_HINT)
