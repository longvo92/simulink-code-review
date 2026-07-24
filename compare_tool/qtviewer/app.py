"""Viewer main window: folder tree on the left, diff pane on the right.

The scan runs on a :class:`ScanWorker` thread; the window only reacts to its
signals. Fail-safe stays first-class: a worker crash or any uncompared path
raises a loud red banner -- an incomplete compare must never look clean.
"""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QBrush, QColor, QPalette
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QProgressBar, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..diff_engine import RULES
from ..scanner import summarize
from .diffpane import DiffPane
from .tree import STATUS, build_nodes, filter_nodes
from .worker import ScanWorker

REL_ROLE = Qt.UserRole  # QTreeWidgetItem data slot holding a file's rel path


def _arxml_include():
    """Same include globs run_compare uses for --arxml-only."""
    return tuple('*' + ext for ext, rs in RULES.items() if rs in ('arxml', 'a2l'))


class MainWindow(QMainWindow):
    def __init__(self, old=None, new=None, exclude=(), arxml_only=False):
        super().__init__()
        self.old = old
        self.new = new
        self.exclude = tuple(exclude)
        self.include = _arxml_include() if arxml_only else ()
        self.results = {}
        self.worker = None
        self._pending_rel = None     # file to re-open after a rule-toggle rescan
        self._rescan_queued = False  # a toggle flipped while a scan was running

        self.setWindowTitle('AUTOSAR CodeGen Compare — viewer')
        self.resize(1200, 800)

        self.banner = QLabel()
        self.banner.setVisible(False)
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet('background:#4a1d1d; color:#ffd6d6; padding:6px 10px;'
                                  'font-weight:bold; border-bottom:1px solid #b04a4a;')

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['File', 'Status'])
        self.tree.setColumnWidth(0, 380)
        self.tree.setUniformRowHeights(True)
        self.tree.itemSelectionChanged.connect(self._on_select)

        # path search only. Verdicts never remove a row: the folder structure
        # must stay stable so the reviewer's bearings do not shift when change
        # categories are folded away.
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText('Filter by path…')
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._refresh_tree)

        # compare-rule toggles: unticking a category means "do not report it
        # separately" -- the tree is rescanned and each such file comes back as
        # Identical (nothing left) or Modified (real changes underneath).
        self.cb_comment = QCheckBox('Comment')
        self.cb_comment.setChecked(True)
        self.cb_comment.setToolTip(
            'Untick to rescan ignoring comment-only differences: each such '
            'file is then reported as Identical or Modified.')
        self.cb_comment.toggled.connect(self._start_scan)
        self.cb_unimportant = QCheckBox('Unimportant')
        self.cb_unimportant.setChecked(True)
        self.cb_unimportant.setToolTip(
            'Untick to rescan ignoring the other unimportant differences '
            '(UUIDs, timestamps, renames, whitespace): each such file is then '
            'reported as Identical or Modified.')
        self.cb_unimportant.toggled.connect(self._start_scan)
        rules = QHBoxLayout()
        rules.setContentsMargins(0, 0, 0, 0)
        rules.addWidget(QLabel('Report:'))
        rules.addWidget(self.cb_comment)
        rules.addWidget(self.cb_unimportant)
        rules.addStretch(1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(6, 6, 6, 0)
        lv.setSpacing(4)
        lv.addWidget(self.filter_edit)
        lv.addLayout(rules)
        lv.addWidget(self.tree, 1)

        self.diff = DiffPane()

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.diff)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([400, 800])

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.banner)
        v.addWidget(split)
        self.setCentralWidget(central)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(240)
        self.progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress)

        tb = self.addToolBar('main')
        tb.setMovable(False)
        act_open = QAction('Open folders…', self)
        act_open.triggered.connect(self._pick_folders)
        act_rescan = QAction('Rescan', self)
        act_rescan.triggered.connect(self._start_scan)
        tb.addAction(act_open)
        tb.addAction(act_rescan)
        tb.addSeparator()
        act_prev = QAction('◀ Prev change', self)
        act_prev.setShortcut('F7')
        act_prev.triggered.connect(lambda: self.diff.prev_change())
        act_next = QAction('Next change ▶', self)
        act_next.setShortcut('F8')
        act_next.triggered.connect(lambda: self.diff.next_change())
        tb.addAction(act_prev)
        tb.addAction(act_next)

        if self.old and self.new:
            self._start_scan()
        else:
            self._pick_folders()

    # --- folder selection ---

    def _pick_folders(self):
        o = QFileDialog.getExistingDirectory(self, 'Select OLD folder', self.old or '')
        if not o:
            return
        n = QFileDialog.getExistingDirectory(self, 'Select NEW folder', self.new or o)
        if not n:
            return
        self.old, self.new = o, n
        self._start_scan()

    # --- scan lifecycle ---

    def _fold(self):
        """Change categories the current rules do NOT report separately; those
        files come back Identical (or Modified when real changes remain)."""
        fold = []
        if not self.cb_comment.isChecked():
            fold.append('comment-only')
        if not self.cb_unimportant.isChecked():
            fold.append('ignorable-only')
        return tuple(fold)

    def _start_scan(self):
        if not (self.old and self.new):
            return
        if self.worker and self.worker.isRunning():
            # never drop the request: a toggle flipped while a scan is running
            # would leave the tree disagreeing with the checkboxes. Queue it and
            # run once the current scan lands.
            self._rescan_queued = True
            return
        # a rule toggle rescans; remember the open file so the reviewer keeps
        # their place instead of being thrown back to an empty pane
        self._pending_rel = self._selected_rel()
        self.banner.setVisible(False)
        self.tree.clear()
        self.diff.clear()
        self.results = {}
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # busy/indeterminate until first tick
        self.setWindowTitle('AUTOSAR CodeGen Compare — {}  →  {}'.format(self.old, self.new))
        self.statusBar().showMessage('Scanning…')
        self.worker = ScanWorker(self.old, self.new, self.exclude, self.include,
                                 self._fold())
        self.worker.progressed.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)
        self.worker.start()

    def _on_progress(self, done, total, rel):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.statusBar().showMessage('Scanning {}/{}: {}'.format(done, total, rel))

    def _on_done(self, results):
        self.results = results
        self._refresh_tree()
        self._reselect(getattr(self, '_pending_rel', None))
        counts = summarize(results)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setVisible(False)
        if counts['error']:
            errs = sorted(rel for rel, r in results.items() if r['status'] == 'error')
            shown = ', '.join(errs[:20]) + (' …' if len(errs) > 20 else '')
            self.banner.setText('⚠ COMPARE INCOMPLETE — {} path(s) NOT compared '
                                '(treat as potentially changed): {}'.format(len(errs), shown))
            self.banner.setVisible(True)
        self.statusBar().showMessage(
            '{real-change} modified · {comment-only} comment-only · '
            '{ignorable-only} unimportant · {added} added · {deleted} deleted · '
            '{identical} identical · {error} error(s)'.format(**counts))
        self._run_queued_scan()

    def _on_fail(self, msg):
        self.progress.setVisible(False)
        self.banner.setText('‼ SCAN FAILED — no results (treat everything as '
                            'potentially changed): {}'.format(msg))
        self.banner.setVisible(True)
        self.statusBar().showMessage('SCAN FAILED')
        self._run_queued_scan()

    def _run_queued_scan(self):
        """Rerun once for a rule toggle that arrived mid-scan, so the tree
        always ends up matching the checkboxes."""
        if self._rescan_queued:
            self._rescan_queued = False
            self._start_scan()

    # --- tree fill + selection ---

    def _refresh_tree(self):
        """Rebuild the tree from results under the current path filter. Cheap
        enough to run on every keystroke; selection is not preserved."""
        self.tree.clear()
        if not self.results:
            return
        self._fill_tree(filter_nodes(build_nodes(self.results),
                                     text=self.filter_edit.text()))

    def _fill_tree(self, nodes):
        def add(parent, node):
            marker, label, color = STATUS[node.status]
            item = QTreeWidgetItem(['{}  {}'.format(marker, node.name), label])
            brush = QBrush(QColor(color))
            item.setForeground(0, brush)
            item.setForeground(1, brush)
            if not node.is_dir:
                item.setData(0, REL_ROLE, node.rel)
            for ch in node.children:
                add(item, ch)
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            if node.is_dir:
                item.setExpanded(True)

        for n in nodes:
            add(None, n)

    def _selected_rel(self):
        items = self.tree.selectedItems()
        return items[0].data(0, REL_ROLE) if items else None

    def _reselect(self, rel):
        """Re-open the file that was showing before a rescan."""
        if not rel:
            return
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item.data(0, REL_ROLE) == rel:
                self.tree.setCurrentItem(item)
                return
            stack.extend(item.child(i) for i in range(item.childCount()))

    def _on_select(self):
        rel = self._selected_rel()
        if rel and rel in self.results:
            self.diff.show_file(rel, self.results[rel], self.old, self.new)


def _apply_dark(app):
    """Fusion dark palette so the viewer matches the report's dark identity."""
    app.setStyle('Fusion')
    p = QPalette()
    bg, base, text = QColor('#1e1f22'), QColor('#232427'), QColor('#d4d4d4')
    p.setColor(QPalette.Window, bg)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, bg)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Button, base)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.Highlight, QColor('#3a5a7a'))
    p.setColor(QPalette.HighlightedText, QColor('#ffffff'))
    app.setPalette(p)


def run_viewer(old=None, new=None, exclude=(), arxml_only=False):
    app = QApplication.instance()
    owns = app is None
    if owns:
        app = QApplication(sys.argv[:1])
    _apply_dark(app)
    win = MainWindow(old, new, exclude, arxml_only)
    win.show()
    return app.exec() if owns else 0


if __name__ == '__main__':
    sys.exit(run_viewer())
