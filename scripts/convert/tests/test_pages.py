"""Page kinds in scripts/convert/pages.py: region shares, the kind rules, page runs.

    scripts/convert/.venv/bin/python -m unittest discover -s scripts/convert/tests
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pages as pg  # noqa: E402


def region(label, box):
    return {"label": label, "box": list(box), "conf": 0.9}


def page(regions=(), words=200, line_numbers=0, dot_lines=0.0, widgets=0):
    regs = list(regions)
    return {"regions": regs, "shares": pg.shares(regs), "words": words, "line_numbers": line_numbers,
            "dot_lines": dot_lines, "widgets": widgets}


class Shares(unittest.TestCase):
    def test_headers_and_footers_are_left_out(self):
        s = pg.shares([region("page_header", (0, 0, 600, 40)), region("text", (50, 100, 550, 700))])
        self.assertEqual(s, {"text": 1.0})

    def test_a_region_inside_another_counts_once(self):
        s = pg.shares([region("picture", (50, 100, 550, 700)), region("text", (100, 200, 200, 220))])
        self.assertEqual(s, {"picture": 1.0})


class Kinds(unittest.TestCase):
    def test_a_letters_address_block_is_not_a_form(self):
        p = page([region("key_value_region", (50, 50, 300, 150)), region("text", (50, 200, 550, 700))])
        self.assertEqual(pg.page_kind(p, False), "text")

    def test_checkboxes_make_a_form(self):
        p = page([region("checkbox_unselected", (50, 50, 60, 60)), region("form", (40, 40, 550, 600))])
        self.assertEqual(pg.page_kind(p, False), "form")

    def test_acroform_fields_make_a_form(self):
        self.assertEqual(pg.page_kind(page([region("text", (50, 50, 550, 700))], widgets=3), False), "form")

    def test_line_numbers_count_only_in_a_transcript(self):
        p = page([region("table", (50, 50, 550, 700))], line_numbers=25)
        self.assertEqual(pg.page_kind(p, True), "transcript")
        self.assertEqual(pg.page_kind(p, False), "table")

    def test_dot_leaders_make_contents(self):
        self.assertEqual(pg.page_kind(page([region("table", (50, 50, 550, 700))], dot_lines=0.6), False), "contents")

    def test_text_beside_a_table_is_mixed(self):
        p = page([region("text", (50, 50, 550, 350)), region("table", (50, 400, 550, 700))])
        self.assertEqual(pg.page_kind(p, False), "mixed:table+text")

    def test_no_regions_and_no_words_is_empty(self):
        self.assertEqual(pg.page_kind(page(words=0), False), "empty")

    def test_a_page_not_read_says_so(self):
        p = page(words=0)
        p["not_read"] = "30,642 paths"
        self.assertEqual(pg.page_kind(p, False), "not_read")


class Runs(unittest.TestCase):
    def test_runs_break_at_gaps_and_at_the_size(self):
        self.assertEqual(list(pg.runs_of_pages([1, 2, 3, 5, 6, 7, 8], 3)), [[1, 2, 3], [5, 6, 7], [8]])


class SecondOpinion(unittest.TestCase):
    """The vision model is asked only where the layout model is weak, and decides only some of those."""

    def run_with(self, pages, answer):
        pdf = MagicMock()
        with patch.object(pg, "ask_vlm", return_value=answer):
            return pg.second_opinion(pdf, pages)

    def p(self, kind, source="text layer", shares=None):
        return {"page": 1, "kind": kind, "kind_by": "layout", "source": source,
                "shares": {"text": 1.0} if shares is None else shares}

    def test_a_form_takes_its_kind(self):
        x = self.p("table", source="ocr", shares={"table": 1.0})
        self.run_with([x], {"kind": "form", "picture_type": "none", "rotated": False})
        self.assertEqual((x["kind"], x["kind_by"], x["vlm"]["asked_because"]), ("form", "vlm", "scanned"))

    def test_other_disagreements_keep_the_layout_kind_and_are_marked(self):
        # 4462406's scanned mitigation tables, which it calls text
        x = self.p("table", source="ocr", shares={"table": 1.0})
        self.run_with([x], {"kind": "text", "picture_type": "none", "rotated": False})
        self.assertEqual((x["kind"], x["kind_by"], x.get("kind_disputed")), ("table", "layout", True))

    def test_a_picture_gets_a_type_but_keeps_its_kind(self):
        x = self.p("picture", shares={"picture": 1.0})
        self.run_with([x], {"kind": "mixed", "picture_type": "map", "rotated": False})
        self.assertEqual((x["kind"], x["vlm"]["picture_type"]), ("picture", "map"))

    def test_an_ordinary_text_page_is_not_asked(self):
        x = self.p("text")
        self.assertEqual(self.run_with([x], {"kind": "form"}), 0)
        self.assertNotIn("vlm", x)

    def test_ollama_down_leaves_the_layout_reading(self):
        x = self.p("table", source="ocr", shares={"table": 1.0})
        with patch.object(pg, "ask_vlm", side_effect=OSError("connection refused")):
            pg.second_opinion(MagicMock(), [x])
        self.assertEqual(x["kind"], "table")
        self.assertIn("connection refused", x["vlm"]["error"])


if __name__ == "__main__":
    unittest.main()
