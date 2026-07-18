"""Tkinter front panel over the same compare core the CLI uses.

Stdlib-only (tkinter ships with CPython on Windows) -- no server, no
localhost UI. All modes of the CLI are exposed: full compare,
ARXML/A2L-only, exclude patterns, custom report path. The scan runs in a
worker thread so the window stays responsive; the worker talks to the UI
through a queue only (tkinter is single-threaded), and any worker
exception surfaces as a loud red failure -- a died run must never look
like "no changes".

Launch:
    python -m compare_tool --gui [old_dir] [new_dir]
    python -m compare_tool.gui
"""

import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .main import default_report_name, run_compare, summary_lines

POLL_MS = 100


class CompareGUI:
    def __init__(self, root, old_dir=None, new_dir=None):
        self.root = root
        root.title('AUTOSAR CodeGen Compare')
        root.minsize(640, 480)

        frm = ttk.Frame(root, padding=10)
        frm.pack(fill='both', expand=True)
        frm.columnconfigure(1, weight=1)

        self.old_var = tk.StringVar(value=old_dir or '')
        self.new_var = tk.StringVar(value=new_dir or '')
        self.report_var = tk.StringVar(value='')
        self.exclude_var = tk.StringVar(value='')
        self.arxml_var = tk.BooleanVar(value=False)

        def folder_row(row, label, var):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky='w', pady=2)
            ttk.Entry(frm, textvariable=var).grid(row=row, column=1, sticky='ew',
                                                  padx=6, pady=2)
            ttk.Button(frm, text='Browse…', width=9,
                       command=lambda: self._pick_dir(var)
                       ).grid(row=row, column=2, pady=2)

        folder_row(0, 'OLD folder', self.old_var)
        folder_row(1, 'NEW folder', self.new_var)

        ttk.Label(frm, text='Report file').grid(row=2, column=0, sticky='w', pady=2)
        ttk.Entry(frm, textvariable=self.report_var).grid(row=2, column=1,
                                                          sticky='ew', padx=6, pady=2)
        ttk.Button(frm, text='Browse…', width=9, command=self._pick_report
                   ).grid(row=2, column=2, pady=2)
        ttk.Label(frm, text='(empty = default name next to the NEW folder)',
                  foreground='#777').grid(row=3, column=1, sticky='w', padx=6)

        ttk.Checkbutton(
            frm, variable=self.arxml_var,
            text='ARXML/A2L only — compact "what changed in the model / '
                 'calibration" report'
        ).grid(row=4, column=0, columnspan=3, sticky='w', pady=(6, 2))

        ttk.Label(frm, text='Exclude').grid(row=5, column=0, sticky='w', pady=2)
        ttk.Entry(frm, textvariable=self.exclude_var).grid(row=5, column=1,
                                                           sticky='ew', padx=6, pady=2)
        ttk.Label(frm, text='(glob patterns, space-separated, e.g. *.html Rte_*.c)',
                  foreground='#777').grid(row=6, column=1, sticky='w', padx=6)

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=3, sticky='ew', pady=(8, 4))
        self.run_btn = ttk.Button(btns, text='Run compare', command=self._run_clicked)
        self.run_btn.pack(side='left')
        self.open_btn = ttk.Button(btns, text='Open report', state='disabled',
                                   command=self._open_report)
        self.open_btn.pack(side='left', padx=6)
        self.progress = ttk.Progressbar(btns, mode='determinate')
        self.progress.pack(side='left', fill='x', expand=True, padx=6)

        self.status_var = tk.StringVar(value='Pick two folders and run.')
        self.status = tk.Label(frm, textvariable=self.status_var, anchor='w')
        self.status.grid(row=8, column=0, columnspan=3, sticky='ew', pady=(0, 4))

        self.log = tk.Text(frm, height=14, state='disabled', wrap='none',
                           font=('Consolas', 9))
        self.log.grid(row=9, column=0, columnspan=3, sticky='nsew')
        frm.rowconfigure(9, weight=1)
        sb = ttk.Scrollbar(frm, orient='vertical', command=self.log.yview)
        sb.grid(row=9, column=3, sticky='ns')
        self.log['yscrollcommand'] = sb.set
        self.log.tag_configure('err', foreground='#b00020')
        self.log.tag_configure('warn', foreground='#a05a00')
        self.log.tag_configure('ok', foreground='#00691c')

        self.q = None
        self.report_path = None

    # --- widget helpers ---

    def _pick_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or None)
        if d:
            var.set(d)

    def _pick_report(self):
        f = filedialog.asksaveasfilename(
            defaultextension='.html', filetypes=[('HTML report', '*.html')],
            initialfile=default_report_name(self.arxml_var.get()))
        if f:
            self.report_var.set(f)

    def _log_line(self, text, tag=None):
        self.log['state'] = 'normal'
        self.log.insert('end', text + '\n', tag or ())
        self.log.see('end')
        self.log['state'] = 'disabled'

    def _set_status(self, text, color='#000'):
        self.status_var.set(text)
        self.status.configure(foreground=color)

    # --- run flow ---

    def _run_clicked(self):
        old = self.old_var.get().strip()
        new = self.new_var.get().strip()
        for label, p in (('OLD', old), ('NEW', new)):
            if not p or not Path(p).is_dir():
                messagebox.showerror('Invalid folder',
                                     '{} folder is not a directory:\n{}'
                                     .format(label, p or '(empty)'))
                return
        report = self.report_var.get().strip()
        if not report:
            # default OUTSIDE the compared trees so the report never scans
            # itself on the next run
            report = str(Path(new).parent / default_report_name(self.arxml_var.get()))
            self.report_var.set(report)
        exclude = tuple(self.exclude_var.get().split())

        self.run_btn['state'] = 'disabled'
        self.open_btn['state'] = 'disabled'
        self.report_path = None
        self.progress['value'] = 0
        self.log['state'] = 'normal'
        self.log.delete('1.0', 'end')
        self.log['state'] = 'disabled'
        self._set_status('Scanning…')

        self.q = queue.Queue()
        threading.Thread(
            target=self._worker,
            args=(old, new, report, self.arxml_var.get(), exclude),
            daemon=True).start()
        self.root.after(POLL_MS, self._poll)

    def _worker(self, old, new, report, arxml_only, exclude):
        q = self.q

        def progress(done, total, rel):
            q.put(('prog', done, total, rel))

        try:
            results, counts = run_compare(old, new, report, arxml_only,
                                          exclude=exclude, progress=progress)
            q.put(('done', results, counts, report))
        except Exception as e:
            # a died run must be loud, never a silent half-result
            q.put(('fail', '{}: {}'.format(type(e).__name__, e)))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == 'prog':
                    _tag, done, total, rel = msg
                    self.progress['maximum'] = max(total, 1)
                    self.progress['value'] = done
                    self._set_status('Scanning {}/{}: {}'.format(done, total, rel))
                elif msg[0] == 'done':
                    self._finish(*msg[1:])
                    return
                else:  # fail
                    self._log_line('!! RUN FAILED: {}'.format(msg[1]), 'err')
                    self._set_status('RUN FAILED — no report written', '#b00020')
                    messagebox.showerror('Compare failed', msg[1])
                    self.run_btn['state'] = 'normal'
                    return
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def _finish(self, results, counts, report):
        for line in summary_lines(results, counts):
            if 'COMPARE INCOMPLETE' in line or line.startswith('  !!'):
                tag = 'err'
            elif line.startswith('  MODIFIED'):
                tag = 'warn'
            else:
                tag = None
            self._log_line(line, tag)
        self._log_line('Report written: {}'.format(Path(report).resolve()))
        self.progress['value'] = self.progress['maximum']

        if counts['error']:
            self._set_status(
                'COMPARE INCOMPLETE — {} path(s) NOT compared '
                '(treat as potentially changed)'.format(counts['error']),
                '#b00020')
        elif counts['real-change'] or counts['added'] or counts['deleted']:
            self._set_status(
                'Real changes: {} modified, {} added, {} deleted'.format(
                    counts['real-change'], counts['added'], counts['deleted']),
                '#a05a00')
        else:
            self._set_status('No real changes (noise-only or identical).',
                             '#00691c')
        self.report_path = report
        self.open_btn['state'] = 'normal'
        self.run_btn['state'] = 'normal'

    def _open_report(self):
        if self.report_path and Path(self.report_path).exists():
            webbrowser.open(Path(self.report_path).resolve().as_uri())


def run_gui(old_dir=None, new_dir=None):
    root = tk.Tk()
    CompareGUI(root, old_dir, new_dir)
    root.mainloop()
    return 0


if __name__ == '__main__':
    run_gui()
