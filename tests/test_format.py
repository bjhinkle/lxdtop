"""Turning numbers into the short strings and styles on screen."""

import unittest

from tests.support import GiB, MiB, glyphs, lxdtop


class HumanTest(unittest.TestCase):
    def test_examples_from_the_docstring(self):
        self.assertEqual(lxdtop.human(512), "512B")
        self.assertEqual(lxdtop.human(4300), "4.2K")
        self.assertEqual(lxdtop.human(37 * MiB), "37M")
        self.assertEqual(lxdtop.human(2 * GiB), "2.0G")

    def test_units_step_up_at_1000_and_divide_by_1024(self):
        self.assertEqual(lxdtop.human(0), "0B")
        self.assertEqual(lxdtop.human(999), "999B")
        self.assertEqual(lxdtop.human(1000), "1.0K")         # 0.98 KiB, kept to four characters
        self.assertEqual(lxdtop.human(1536), "1.5K")
        self.assertEqual(lxdtop.human(10 * 1024), "10K")     # one decimal only below 10
        self.assertEqual(lxdtop.human(384 * MiB), "384M")
        self.assertEqual(lxdtop.human(3 * 1024 * GiB), "3.0T")

    def test_none_is_blank(self):
        self.assertEqual(lxdtop.human(None), "")

    def test_rates_are_floats(self):
        self.assertEqual(lxdtop.human(512.4), "512B")
        self.assertEqual(lxdtop.human(2.5 * MiB), "2.5M")


class PercentAndTimeTest(unittest.TestCase):
    def test_fmt_pct(self):
        self.assertEqual(lxdtop.fmt_pct(None), "")
        self.assertEqual(lxdtop.fmt_pct(0), "0.0%")
        self.assertEqual(lxdtop.fmt_pct(12.345), "12.3%")
        self.assertEqual(lxdtop.fmt_pct(99.94), "99.9%")
        self.assertEqual(lxdtop.fmt_pct(99.95), "100%")      # no "100.0%": the column is six wide
        self.assertEqual(lxdtop.fmt_pct(250), "250%")        # more than one core

    def test_fmt_span(self):
        self.assertEqual(lxdtop.fmt_span(0), "0s")
        self.assertEqual(lxdtop.fmt_span(119), "119s")
        self.assertEqual(lxdtop.fmt_span(120), "2 min")
        self.assertEqual(lxdtop.fmt_span(1800), "30 min")

    def test_fmt_uptime(self):
        self.assertEqual(lxdtop.fmt_uptime(59), "0m")
        self.assertEqual(lxdtop.fmt_uptime(61), "1m")
        self.assertEqual(lxdtop.fmt_uptime(3600), "1h 0m")
        self.assertEqual(lxdtop.fmt_uptime(3 * 3600 + 25 * 60 + 9), "3h 25m")
        self.assertEqual(lxdtop.fmt_uptime(2 * 86400 + 5 * 3600 + 1234), "2d 5h")

    def test_fmt_age(self):
        self.assertEqual(lxdtop.fmt_age(-5), "0s")           # clock skew between readings
        self.assertEqual(lxdtop.fmt_age(0), "0s")
        self.assertEqual(lxdtop.fmt_age(59.9), "59s")
        self.assertEqual(lxdtop.fmt_age(60), "1m")
        self.assertEqual(lxdtop.fmt_age(3599), "59m")
        self.assertEqual(lxdtop.fmt_age(3600), "1h")
        self.assertEqual(lxdtop.fmt_age(86399), "23h")
        self.assertEqual(lxdtop.fmt_age(15 * 86400 + 7), "15d")


class NetworkUnitsTest(unittest.TestCase):
    def test_mbps_is_decimal_bits(self):
        self.assertEqual(lxdtop.mbps(125_000), 1.0)          # 125,000 bytes/s = 1,000,000 bits/s
        self.assertEqual(lxdtop.mbps(0), 0.0)

    def test_fmt_mbps_examples_from_the_docstring(self):
        self.assertEqual(lxdtop.fmt_mbps(0), "0")
        self.assertEqual(lxdtop.fmt_mbps(0.004), "<0.01")
        self.assertEqual(lxdtop.fmt_mbps(0.42), "0.42")
        self.assertEqual(lxdtop.fmt_mbps(10.94), "10.9")
        self.assertEqual(lxdtop.fmt_mbps(940.4), "940")
        self.assertEqual(lxdtop.fmt_mbps(1240), "1240")
        self.assertEqual(lxdtop.fmt_mbps(-1), "0")

    def test_mbps_cell(self):
        self.assertEqual(lxdtop.mbps_cell(None, 9), ("", "dim"))           # no reading yet
        self.assertEqual(lxdtop.mbps_cell(0, 9), ("        -", "dim"))
        self.assertEqual(lxdtop.mbps_cell(1, 9), ("    <0.01", "dim"))
        self.assertEqual(lxdtop.mbps_cell(1.25e6, 9), ("     10.0", "text"))  # 10 Mb/s
        self.assertEqual(lxdtop.mbps_cell(15e6, 9), ("      120", "mid"))
        self.assertEqual(lxdtop.mbps_cell(125e6, 9), ("     1000", "hi"))

    def test_rate_cell_is_bytes(self):
        self.assertEqual(lxdtop.rate_cell(None, 7), ("", "dim"))
        self.assertEqual(lxdtop.rate_cell(0.5, 7), ("      -", "dim"))
        self.assertEqual(lxdtop.rate_cell(2048, 7), ("   2.0K", "text"))
        self.assertEqual(lxdtop.rate_cell(20 * MiB, 7), ("    20M", "mid"))
        self.assertEqual(lxdtop.rate_cell(200 * MiB, 7), ("   200M", "hi"))


class StyleTest(unittest.TestCase):
    def test_cpu_style_thresholds(self):
        self.assertEqual(lxdtop.cpu_style(None), "dim")
        self.assertEqual(lxdtop.cpu_style(0.5), "text")
        for pct, style in ((1, "lo"), (49.9, "lo"), (50, "mid"), (89.9, "mid"), (90, "hi"), (400, "hi")):
            self.assertEqual(lxdtop.cpu_style(pct), style, pct)

    def test_heat_by_position(self):
        for pos, style in ((0, "lo"), (0.49, "lo"), (0.5, "mid"), (0.79, "mid"), (0.8, "hi"), (1, "hi")):
            self.assertEqual(lxdtop.heat(pos), style, pos)


class GlyphTest(unittest.TestCase):
    def test_column_glyph_shades(self):
        with glyphs(lxdtop.GLYPHS_UNICODE):
            self.assertEqual(lxdtop.column_glyph(1.0), "█")
            self.assertEqual(lxdtop.column_glyph(0.75), "█")
            self.assertEqual(lxdtop.column_glyph(0.74), "▒")
            self.assertEqual(lxdtop.column_glyph(0.5), "▒")
            self.assertEqual(lxdtop.column_glyph(0.49), "░")
            self.assertEqual(lxdtop.column_glyph(0.25), "░")
            self.assertIsNone(lxdtop.column_glyph(0.24))
            self.assertIsNone(lxdtop.column_glyph(-3))

    def test_ascii_glyphs(self):
        with glyphs(lxdtop.GLYPHS_ASCII):
            self.assertEqual([lxdtop.column_glyph(f) for f in (1, 0.6, 0.3)], ["#", "=", "-"])

    def test_glyph_tables_match(self):
        self.assertEqual(set(lxdtop.GLYPHS_UNICODE), set(lxdtop.GLYPHS_ASCII))
        self.assertTrue(all(ch.isascii() for ch in lxdtop.GLYPHS_ASCII.values()))

    def test_console_font_characters_only(self):
        # CONTRIBUTING.md: full block, medium and light shade, box drawing, and ■ ● · … ▲ ▼ ↑ ↓ °
        allowed = set("█▒░■●·…▲▼↑↓°×─│╭╮╰╯┐┌┘└")
        self.assertLessEqual(set("".join(lxdtop.GLYPHS_UNICODE.values())), allowed)

    def test_spark(self):
        with glyphs(lxdtop.GLYPHS_UNICODE):
            self.assertEqual(lxdtop.spark(None), (" ", "text"))
            self.assertEqual(lxdtop.spark(0.4), ("·", "faint"))
            self.assertEqual(lxdtop.spark(1), ("░", "lo"))
            self.assertEqual(lxdtop.spark(5), ("▒", "lo"))
            self.assertEqual(lxdtop.spark(10), ("█", "lo"))
            self.assertEqual(lxdtop.spark(60), ("█", "mid"))
            self.assertEqual(lxdtop.spark(95), ("█", "hi"))

    def test_meter_cells(self):
        with glyphs(lxdtop.GLYPHS_UNICODE):
            half = lxdtop.meter_cells(10, 50, 100, lxdtop.heat)
            self.assertEqual(half, [("■", "lo")] * 5 + [("■", "faint")] * 5)
            full = lxdtop.meter_cells(10, 100, 100, lxdtop.heat)
            self.assertEqual([s for _, s in full], ["lo"] * 5 + ["mid"] * 3 + ["hi"] * 2)
            self.assertEqual(lxdtop.meter_cells(10, 250, 100, lxdtop.heat), full)   # capped at the width
            for value, scale in ((None, 100), (0, 100), (50, 0)):
                self.assertEqual(lxdtop.meter_cells(4, value, scale, lxdtop.heat), [("■", "faint")] * 4)
            self.assertEqual(lxdtop.meter_cells(0, 50, 100, lxdtop.heat), [])


if __name__ == "__main__":
    unittest.main()
