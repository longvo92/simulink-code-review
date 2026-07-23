"""PySide6 side-by-side compare viewer (Beyond-Compare style).

Optional feature: PySide6 is imported lazily so the CLI and the HTML report
keep working on a headless box with no Qt installed. Launch:

    python -m compare_tool --qt [old_dir] [new_dir]

``run_viewer`` lives in :mod:`compare_tool.qtviewer.app`; importing it pulls in
PySide6, so it is imported there, not here.
"""


def run_viewer(*args, **kwargs):
    from .app import run_viewer as _run
    return _run(*args, **kwargs)
