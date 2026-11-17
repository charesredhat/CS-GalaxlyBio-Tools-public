#!/usr/bin/env python3
"""
Contract tests for the admin-only problem-report read API.

Run with:  python3 local-galaxy/report-problem/webhooks/test_report_admin.py
(stdlib unittest only -- no pytest, no running Galaxy.)

The point of this file
----------------------
The handler's contract is three sentences:

    1. A caller who is not a Galaxy admin gets no data, and no hint that a
       better account would help.
    2. Every report in the file is either returned or accounted for in a
       number the page can show. Nothing disappears silently.
    3. One corrupt line costs one record, not the whole file.

Sentence 2 is the one that has already been wrong in this code: `total` used to
be `len(rows)`, the count *inside* the 8 MB parse window, so an admin with 900
reports was told "500 report(s)" and the other 400 were unreachable. The
`total`/`scanned`/`returned` tests below are the regression tests for that.

The real `trans` is a Galaxy `GalaxyWebTransaction`; `FakeTrans` reimplements
only the two things the handler touches -- `user_is_admin` and
`app.config.data_dir` -- so the tests exercise the handler, not a mock of it.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    "report_admin", os.path.join(_HERE, "report_admin", "__init__.py")
)
handler = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(handler)

# Keys every successful response must carry, whatever path produced it.
OK_KEYS = {
    "ok", "total", "scanned", "returned", "offset", "limit",
    "file_truncated", "reports",
}


class FakeConfig:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.root = data_dir


class FakeTrans:
    def __init__(self, data_dir, is_admin=True):
        self.user_is_admin = is_admin
        self.app = type("App", (), {"config": FakeConfig(data_dir)})()


class ReportsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.reports_dir = os.path.join(self.tmp, "problem_reports")
        os.makedirs(self.reports_dir)
        self.path = os.path.join(self.reports_dir, "reports.jsonl")

    def write(self, lines, trailing_newline=True):
        body = "\n".join(lines)
        if trailing_newline and lines:
            body += "\n"
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(body)

    def write_reports(self, n, start=1):
        self.write([
            json.dumps({
                "id": "%012d" % i, "time": "2026-10-2%dT10:00:00-04:00" % (i % 10),
                "email": "r%d@example.org" % i, "logged_in_as": None,
                "summary": "summary %d" % i, "problem": "problem %d" % i, "page": "",
            })
            for i in range(start, start + n)
        ])

    def call(self, params=None, is_admin=True):
        return handler.main(FakeTrans(self.tmp, is_admin), None, params or {})


class TestAdminGate(ReportsTestCase):
    def test_non_admin_is_refused(self):
        self.write_reports(3)
        out = self.call(is_admin=False)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "Administrator access required.")

    def test_refusal_carries_no_data_and_no_path(self):
        """A refusal must not leak report content, counts, or the file location."""
        self.write_reports(3)
        out = self.call(is_admin=False)
        self.assertNotIn("reports", out)
        self.assertNotIn("total", out)
        self.assertNotIn("path", out)
        self.assertNotIn(self.tmp, json.dumps(out))

    def test_admin_gets_data(self):
        self.write_reports(3)
        out = self.call()
        self.assertTrue(out["ok"])
        self.assertEqual(out["returned"], 3)


class TestResponseShape(ReportsTestCase):
    def test_missing_file_has_the_same_keys_as_a_full_response(self):
        """The empty branch used to omit limit/file_truncated and add `path`."""
        missing = self.call()
        self.assertEqual(set(missing), OK_KEYS)
        self.assertEqual(missing["reports"], [])
        self.assertEqual(missing["total"], 0)

        self.write_reports(2)
        self.assertEqual(set(self.call()), OK_KEYS)

    def test_no_filesystem_path_in_any_response(self):
        self.write_reports(1)
        self.assertNotIn(self.tmp, json.dumps(self.call()))

    def test_empty_file_is_not_an_error(self):
        self.write([])
        out = self.call()
        self.assertTrue(out["ok"])
        self.assertEqual((out["total"], out["scanned"], out["returned"]), (0, 0, 0))


class TestCounts(ReportsTestCase):
    """Regression tests for `total` once meaning `len(rows)`."""

    def test_total_counts_the_file_not_the_page(self):
        self.write_reports(900)
        out = self.call({"limit": 500})
        self.assertEqual(out["total"], 900)
        self.assertEqual(out["returned"], 500)
        self.assertEqual(out["scanned"], 900)

    def test_total_survives_the_scan_window(self):
        """With the parse window smaller than the file, `total` is still the file."""
        self.write_reports(400)
        original = handler.MAX_SCAN_BYTES
        handler.MAX_SCAN_BYTES = 4096
        self.addCleanup(setattr, handler, "MAX_SCAN_BYTES", original)
        out = self.call({"limit": 500})
        self.assertTrue(out["file_truncated"])
        self.assertEqual(out["total"], 400)
        self.assertLess(out["scanned"], 400)
        self.assertEqual(out["returned"], out["scanned"])

    def test_count_without_trailing_newline(self):
        self.write_reports(3)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": "tail", "problem": "no newline"}))
        self.assertEqual(self.call()["total"], 4)

    def test_blank_lines_are_not_reports(self):
        self.write(['{"id": "a", "problem": "x"}', "", '{"id": "b", "problem": "y"}'])
        out = self.call()
        self.assertEqual(out["scanned"], 2)
        self.assertEqual(out["returned"], 2)


class TestOrderingAndPaging(ReportsTestCase):
    def test_newest_first(self):
        self.write_reports(5)
        ids = [r["id"] for r in self.call()["reports"]]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_offset_walks_backwards_without_gaps_or_repeats(self):
        self.write_reports(10)
        first = [r["id"] for r in self.call({"limit": 4, "offset": 0})["reports"]]
        second = [r["id"] for r in self.call({"limit": 4, "offset": 4})["reports"]]
        third = [r["id"] for r in self.call({"limit": 4, "offset": 8})["reports"]]
        self.assertEqual(len(first + second + third), 10)
        self.assertEqual(len(set(first + second + third)), 10)
        every = [r["id"] for r in self.call({"limit": 500})["reports"]]
        self.assertEqual(first + second + third, every)

    def test_offset_past_the_end_is_empty_not_an_error(self):
        self.write_reports(3)
        out = self.call({"offset": 99})
        self.assertTrue(out["ok"])
        self.assertEqual(out["returned"], 0)
        self.assertEqual(out["total"], 3)


class TestAsInt(unittest.TestCase):
    def test_clamps_into_range(self):
        self.assertEqual(handler._as_int("9999", 50, 1, 500), 500)
        self.assertEqual(handler._as_int("0", 50, 1, 500), 1)
        self.assertEqual(handler._as_int("-5", 50, 0, 500), 0)
        self.assertEqual(handler._as_int("37", 50, 1, 500), 37)

    def test_junk_falls_back_to_the_default(self):
        for junk in (None, "", "abc", "1; DROP TABLE", [], {}, "1.5"):
            self.assertEqual(handler._as_int(junk, 50, 1, 500), 50, junk)

    def test_limit_and_offset_are_bounded_by_the_handler(self):
        """A caller cannot ask for an unbounded response."""
        self.assertLessEqual(handler.DEFAULT_LIMIT, handler.MAX_LIMIT)
        self.assertEqual(handler._as_int("100000", handler.DEFAULT_LIMIT, 1, handler.MAX_LIMIT),
                         handler.MAX_LIMIT)


class TestCorruptLines(ReportsTestCase):
    def test_one_bad_line_costs_one_record(self):
        self.write(['{"id": "a", "problem": "first"}', "{not json at all",
                    '{"id": "c", "problem": "third"}'])
        out = self.call()
        self.assertEqual(out["scanned"], 3)
        self.assertIn("a", [r["id"] for r in out["reports"]])
        self.assertIn("c", [r["id"] for r in out["reports"]])

    def test_corrupt_rows_stay_distinguishable(self):
        """They shared the id "(unparseable)", so they looked like one record."""
        self.write(["{broken one", "{broken two", "{broken three"])
        ids = [r["id"] for r in self.call()["reports"]]
        self.assertEqual(len(set(ids)), 3, ids)

    def test_corrupt_row_has_every_field_the_page_reads(self):
        self.write(["{broken"])
        row = self.call()["reports"][0]
        for key in ("id", "time", "email", "logged_in_as", "summary", "problem", "page"):
            self.assertIn(key, row)

    def test_corrupt_row_text_is_capped(self):
        self.write(["{" + "x" * 9000])
        self.assertLessEqual(len(self.call()["reports"][0]["problem"]), 500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
