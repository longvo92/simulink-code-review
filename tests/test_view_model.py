"""Shared view-model tests: char_span offsets and whole-file aligned_rows.

These back the Qt two-pane viewer (Phase 2) and guard that the extracted
char-span primitive stays byte-identical to the report's old highlighter."""

import unittest

from compare_tool.diff_engine import compare_pair
from compare_tool.report import _char_diff
from compare_tool.view_model import Row, aligned_rows, char_span


class TestCharSpan(unittest.TestCase):
    """char_span returns the offsets the report used to compute inline; it
    must agree with _char_diff (which now consumes it) on every case the
    report test suite pins."""

    def _apply(self, txt, span):
        lo, hi = span
        if lo >= hi:
            return txt
        return txt[:lo] + '[' + txt[lo:hi] + ']' + txt[hi:]

    def test_equal_chars_between_diffs_swallowed_into_one_span(self):
        o, n = char_span('rtb_Sum1_abc', 'rtb_Sum2_xbc')
        self.assertEqual(self._apply('rtb_Sum1_abc', o), 'rtb_Sum[1_a]bc')
        self.assertEqual(self._apply('rtb_Sum2_xbc', n), 'rtb_Sum[2_x]bc')

    def test_pure_insertion_empty_span_on_old_side(self):
        (o_lo, o_hi), n = char_span('ab', 'axxb')
        self.assertEqual(o_lo, o_hi)                 # nothing changed on old
        self.assertEqual(self._apply('axxb', n), 'a[xx]b')

    def test_prefix_change(self):
        o, _n = char_span('Xmid', 'Ymid')
        self.assertEqual(self._apply('Xmid', o), '[X]mid')

    def test_suffix_change(self):
        o, _n = char_span('midX', 'midY')
        self.assertEqual(self._apply('midX', o), 'mid[X]')

    def test_agrees_with_report_char_diff(self):
        # the report renderer must produce spans at exactly these offsets
        cases = [('rtb_Sum1_abc', 'rtb_Sum2_xbc'), ('ab', 'axxb'),
                 ('aXbYcZd', 'aQbWcRd'), ('Xmid', 'Ymid'), ('midX', 'midY')]
        for old, new in cases:
            (o_lo, o_hi), (n_lo, n_hi) = char_span(old, new)
            exp_old = (old if o_lo >= o_hi else
                       old[:o_lo] + '<span class="chg-seg">' + old[o_lo:o_hi]
                       + '</span>' + old[o_hi:])
            html_old, _ = _char_diff(old, new)
            self.assertEqual(html_old, exp_old, (old, new))


class TestAlignedRows(unittest.TestCase):
    def _rows(self, old, new, path='f.c'):
        r = compare_pair(old, new, path)
        return r, aligned_rows(old.split('\n'), new.split('\n'), r['hunks'])

    def test_real_change_rows_carry_both_sides(self):
        r, rows = self._rows("int lim = 5;\nint keep = 0;\n",
                             "int lim = 10;\nint keep = 0;\n")
        real = [row for row in rows if row.mode == 'real']
        self.assertTrue(real)
        row = real[0]
        self.assertEqual(row.old_txt, 'int lim = 5;')
        self.assertEqual(row.new_txt, 'int lim = 10;')
        # inline highlight offsets resolve against the same row text
        (o_lo, o_hi), _ = char_span(row.old_txt, row.new_txt)
        self.assertEqual(row.old_txt[o_lo:o_hi], '5')

    def test_context_rows_advance_both_sides_in_lockstep(self):
        _r, rows = self._rows("a\nCHANGED_OLD\nb\n", "a\nCHANGED_NEW\nb\n")
        for row in rows:
            if row.mode == 'ctx':
                self.assertEqual(row.old_txt, row.new_txt)
                self.assertIsNotNone(row.old_no)
                self.assertIsNotNone(row.new_no)

    def test_insertion_pads_old_side_with_none(self):
        # new file gains a line -> the extra new row has no old counterpart
        _r, rows = self._rows("x = 1;\ny = 2;\n", "x = 1;\nz = 9;\ny = 2;\n", 'f.c')
        padded = [row for row in rows if row.old_txt is None and row.new_txt is not None]
        self.assertTrue(padded)
        self.assertTrue(all(row.old_no is None for row in padded))

    def test_every_old_and_new_line_appears_exactly_once(self):
        old = "l1\nl2\nl3\nl4\nl5\n"
        new = "l1\nX2\nl3\nl4\nX5\n"
        _r, rows = self._rows(old, new)
        got_old = [row.old_txt for row in rows if row.old_no is not None]
        got_new = [row.new_txt for row in rows if row.new_no is not None]
        self.assertEqual(got_old, old.split('\n'))
        self.assertEqual(got_new, new.split('\n'))

    def test_line_numbers_are_monotonic_and_gapless(self):
        old = "a\nb\nc\nd\n"
        new = "a\nB\nc\nD\n"
        _r, rows = self._rows(old, new)
        old_nos = [row.old_no for row in rows if row.old_no is not None]
        new_nos = [row.new_no for row in rows if row.new_no is not None]
        self.assertEqual(old_nos, list(range(1, len(old.split('\n')) + 1)))
        self.assertEqual(new_nos, list(range(1, len(new.split('\n')) + 1)))

    def test_minor_hunk_rows_tagged_minor(self):
        _r, rows = self._rows("/* gen Mon */\nint x = 1;\n",
                             "/* gen Tue */\nint x = 1;\n")
        self.assertTrue(any(row.mode == 'minor' for row in rows))

    def test_moved_block_rows_tagged_moved(self):
        old = ("void Alpha(void)\n{\n  a = 1;\n  b = 2;\n}\n"
               "void Beta(void)\n{\n  c = 3;\n}\n")
        new = ("void Beta(void)\n{\n  c = 3;\n}\n"
               "void Alpha(void)\n{\n  a = 1;\n  b = 2;\n}\n")
        _r, rows = self._rows(old, new)
        self.assertTrue(any(row.mode == 'moved' for row in rows))


if __name__ == '__main__':
    unittest.main()
