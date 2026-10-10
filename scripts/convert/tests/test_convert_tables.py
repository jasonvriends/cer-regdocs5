"""Table grids in scripts/convert/convert.py: the merged-row check and Table Transformer's grid.

    scripts/convert/.venv/bin/python -m unittest discover -s scripts/convert/tests
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import convert as c  # noqa: E402

H = 8.0          # text height, pt


def word(text, x, y, block=0, line=0, width=None):
    w = width or 5.0 * len(text)
    return {"text": text, "box": (x, y, x + w, y + H), "cx": x + w / 2, "cy": y + H / 2,
            "block": block, "line": line, "wno": 0, "seq": 0}


def table(rows, xs=(10, 120), pitch=12.0, y0=10.0):
    """One word per column per row: [["Gas", "12.5"], ...]; each row its own line."""
    out = []
    for i, cells in enumerate(rows):
        for j, t in enumerate(cells):
            out.append(word(t, xs[j], y0 + i * pitch, block=i, line=0))
    for k, w in enumerate(out):
        w["seq"] = k
    return out


class MergedRows(unittest.TestCase):
    rows = [["Gas", "12.5"], ["Oil", "13.0"], ["Water", "2.1"], ["Condensate", "0.4"]]

    def test_rows_run_together_are_seen(self):
        words = table(self.rows)
        one_row = [(5, 5, 110, 60), (115, 5, 200, 60)]            # one cell per column, all four rows in it
        fit = c.table_structure_score(words, one_row, 1.5)
        self.assertEqual(fit["rows_merged"], 8)
        self.assertGreaterEqual(fit["bad"], 8)

    def test_a_row_per_line_is_clean(self):
        words = table(self.rows)
        grid = [(x0, 8 + 12 * i, x1, 20 + 12 * i) for i in range(4) for x0, x1 in ((5, 110), (115, 200))]
        self.assertEqual(c.table_structure_score(words, grid, 1.5)["rows_merged"], 0)

    def test_wrapped_text_beside_a_single_value_is_not_merged(self):
        words = [word("Natural", 10, 10), word("gas", 50, 10), word("pipeline", 10, 22), word("12.5", 120, 10)]
        cells = {0: ((5, 5, 110, 35), words[:3]), 1: ((115, 5, 200, 35), words[3:])}
        self.assertEqual(c.merged_row_cells(cells), set())

    def test_two_wrapped_headers_side_by_side_are_not_merged(self):
        words = [word("Total", 10, 10), word("Volume", 10, 22), word("Unit", 120, 10), word("Price", 120, 22)]
        cells = {0: ((5, 5, 110, 35), words[:2]), 1: ((115, 5, 200, 35), words[2:])}
        self.assertEqual(c.merged_row_cells(cells), set())

    def test_wrapped_codes_are_not_values(self):
        # 4664902: one ruled row, two cells each wrapping an equipment tag onto a second line
        left = [word("MG-30-F5-1003/", 10, 10), word("MG-36-F5-1002", 10, 22)]
        right = [word("POV-040081", 120, 10), word("downstream", 175, 10), word("isolation", 120, 22),
                 word("valve", 170, 22)]
        cells = {0: ((5, 5, 110, 35), left), 1: ((115, 5, 240, 35), right)}
        self.assertEqual(c.merged_row_cells(cells), set())

    def test_values_with_units_and_dates_are_values(self):
        for text in (["12.5", "m3"], ["2024-01-01"], ["(1,250)"], ["45%"]):
            self.assertTrue(c.is_value({"words": [word(t, 0, 0) for t in text]}), text)
        for text in (["MG-30-F5-1003/"], ["Gas"], ["12.5", "billion", "cubic"]):
            self.assertFalse(c.is_value({"words": [word(t, 0, 0) for t in text]}), text)

    def test_stacked_values_need_lines_at_the_same_heights(self):
        left = [word("Gas", 10, 10), word("Oil", 10, 22)]
        right = [word("12.5", 120, 40), word("13.0", 120, 52)]       # below, not beside
        cells = {0: ((5, 5, 110, 65), left), 1: ((115, 5, 200, 65), right)}
        self.assertEqual(c.merged_row_cells(cells), set())


class TatrGrid(unittest.TestCase):
    """tatr_grid with the model replaced by fixed detections, in pixels at scale 1."""

    class Img:
        width, height = 300, 200

        def crop(self, box):
            self.box = box
            return self

        def convert(self, mode):
            return self

    def grid(self, found, words, box=(0, 0, 200, 60)):           # crop at the page origin: pixels are points
        with patch.object(c, "tatr_detect", return_value=found), \
             patch.dict(c.SETTINGS["tatr"], {"pad_pt": 0.0}):
            return c.tatr_grid((self.Img(), 1.0), box, words)

    def test_rows_and_columns_make_cells_from_the_text_layer(self):
        words = table([["Gas", "12.5"], ["Oil", "13.0"]], y0=15)
        found = [("table row", 0.9, [0, 13, 190, 25]), ("table row", 0.9, [0, 25, 190, 37]),
                 ("table row", 0.6, [0, 14, 190, 26]),                     # a near-duplicate
                 ("table column", 0.9, [0, 0, 100, 50]), ("table column", 0.9, [105, 0, 190, 50])]
        g = self.grid(found, words)
        self.assertEqual((g["num_rows"], g["num_cols"]), (2, 2))
        texts = {(cl["rows"][0], cl["cols"][0]): cl["text"] for cl in g["cells"]}
        self.assertEqual(texts, {(0, 0): "Gas", (0, 1): "12.5", (1, 0): "Oil", (1, 1): "13.0"})
        fit = c.table_structure_score(words, [cl["box"] for cl in g["cells"]], 1.5)
        self.assertEqual(fit["bad"], 0)

    def test_a_spanning_cell_joins_the_cells_under_it(self):
        words = table([["Volume", ""], ["Gas", "12.5"]], y0=15)
        words = [w for w in words if w["text"]]
        found = [("table row", 0.9, [0, 13, 190, 25]), ("table row", 0.9, [0, 25, 190, 37]),
                 ("table column", 0.9, [0, 0, 100, 50]), ("table column", 0.9, [105, 0, 190, 50]),
                 ("table spanning cell", 0.9, [0, 13, 190, 25])]
        g = self.grid(found, words)
        self.assertEqual(len(g["cells"]), 3)
        top = [cl for cl in g["cells"] if cl["rows"] == (0, 1)]
        self.assertEqual([(cl["cols"], cl["text"]) for cl in top], [((0, 2), "Volume")])

    def test_a_span_over_cells_with_their_own_text_is_ignored(self):
        # 4646070: a low-score span joining table-of-contents entries, each in its own row
        words = table([["Abbreviations", "iii"], ["Overview", "vii"]], y0=15)
        found = [("table row", 0.9, [0, 13, 190, 25]), ("table row", 0.9, [0, 25, 190, 37]),
                 ("table column", 0.9, [0, 0, 100, 50]), ("table column", 0.9, [105, 0, 190, 50]),
                 ("table spanning cell", 0.6, [0, 13, 190, 37])]
        g = self.grid(found, words)
        self.assertEqual(len(g["cells"]), 4)

    def test_crossing_a_column_does_not_justify_joining_rows(self):
        # entries that run over a narrow number column, one entry per row
        words = [word("Abbreviations", 5, 15), word("Overview", 5, 27)]
        for k, w in enumerate(words):
            w["seq"] = k
        found = [("table row", 0.9, [0, 13, 190, 25]), ("table row", 0.9, [0, 25, 190, 37]),
                 ("table column", 0.9, [0, 0, 20, 50]), ("table column", 0.9, [20, 0, 190, 50]),
                 ("table spanning cell", 0.6, [0, 13, 190, 37])]
        g = self.grid(found, words)
        self.assertEqual(sorted(cl["text"] for cl in g["cells"] if cl["text"]), ["Abbreviations", "Overview"])

    def test_no_rows_found_is_no_grid(self):
        self.assertIsNone(self.grid([("table column", 0.9, [0, 0, 100, 50])], table([["Gas"]])))


if __name__ == "__main__":
    unittest.main()
