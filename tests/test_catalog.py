from __future__ import annotations

import unittest

from school_survey.catalog import _parse_form_files


class CatalogTests(unittest.TestCase):
    def test_parses_labels_and_normalizes_hyphenated_form_codes(self):
        html = """\
        <html><body><table>
          <tr><td rowspan="2">卒業後の状況</td><td>(30-1) 状況別卒業者数</td>
              <td class="download"><a href="xls/2012_30go_2-1.xls">Excel</a></td></tr>
          <tr><td>(30-2) 職業別就職者数</td>
              <td class="download"><a href="xls/2012_30go_2-2.xls">Excel</a></td></tr>
        </table></body></html>""".encode("utf-8-sig")

        forms = _parse_form_files(html, "https://portal.niad.ac.jp/ptrt/h24.html")

        self.assertEqual([form.form_code for form in forms], ["30go_2_1", "30go_2_2"])
        self.assertEqual(forms[0].form_label, "(30-1) 状況別卒業者数")
        self.assertEqual(forms[1].filename, "2012_30go_2-2.xls")


if __name__ == "__main__":
    unittest.main()
