import unittest

from stitch_parser import (
    ParseError,
    parse_row,
    parse_row_details,
    parse_row_repeat_sites,
    parse_row_sizes,
    parse_row_stitch_counts,
)


class TestParseRow(unittest.TestCase):
    def test_basic_stitches(self):
        self.assertEqual(parse_row('k'), (1, 1))
        self.assertEqual(parse_row('p'), (1, 1))
        self.assertEqual(parse_row('yo'), (0, 1))
        self.assertEqual(parse_row('k2tog'), (2, 1))

    def test_sequence_with_separators(self):
        self.assertEqual(parse_row('k k yo k2tog'), (4, 4))
        self.assertEqual(parse_row('k, k, yo, k2tog'), (4, 4))
        self.assertEqual(parse_row('kkyo k2tog'), (4, 4))

    def test_group_repeat(self):
        self.assertEqual(parse_row('(k2tog yo)*6'), (12, 12))
        self.assertEqual(parse_row('(k yo)*2 p'), (3, 5))

    def test_nested_groups(self):
        # (k yo)*2 -> (2,4)；+ k2tog -> (4,5)；整体 *3
        self.assertEqual(parse_row('((k yo)*2 k2tog)*3'), (12, 15))

    def test_atom_repeat(self):
        self.assertEqual(parse_row('k*3'), (3, 3))
        self.assertEqual(parse_row('k2tog*4'), (8, 4))

    def test_huge_multiplier_is_not_expanded(self):
        self.assertEqual(parse_row('(k yo)*1000000000'), (1000000000, 2000000000))

    def test_case_insensitive(self):
        self.assertEqual(parse_row('K2TOG, YO, K, P'), (4, 4))

    def test_invalid_tokens(self):
        for bad in ['ssk', 'k3', 'k 2', 'm1', 'k/p', 'k+yo']:
            with self.assertRaises(ParseError, msg=bad):
                parse_row(bad)

    def test_bad_parentheses(self):
        for bad in ['(k yo', 'k)', '()', '( )', '(k))*2', '(k (yo)']:
            with self.assertRaises(ParseError, msg=bad):
                parse_row(bad)

    def test_bad_multiplier(self):
        for bad in ['k*0', '(k)*0', 'k*-1', 'k*', 'k*yo', '*3 k', '(k)*2*3']:
            with self.assertRaises(ParseError, msg=bad):
                parse_row(bad)

    def test_empty_row(self):
        with self.assertRaises(ParseError):
            parse_row('   ')


class TestParseRowSizes(unittest.TestCase):
    def test_plain_repeat_applies_to_all_sizes(self):
        c, p = parse_row_sizes('k*3', 3)
        self.assertEqual(c, [3, 3, 3])
        self.assertEqual(p, [3, 3, 3])

    def test_size_repeat_on_atom(self):
        c, p = parse_row_sizes('k*{6|8|10}', 3)
        self.assertEqual(c, [6, 8, 10])
        self.assertEqual(p, [6, 8, 10])

    def test_size_repeat_on_group(self):
        c, p = parse_row_sizes('(k2tog yo)*{6|8|10}', 3)
        self.assertEqual(c, [12, 16, 20])
        self.assertEqual(p, [12, 16, 20])

    def test_size_repeat_with_spaces(self):
        c, p = parse_row_sizes('k*{6 | 8 | 10}', 3)
        self.assertEqual(c, [6, 8, 10])
        self.assertEqual(p, [6, 8, 10])

    def test_nested_mixed_repeats(self):
        # k*{2|3} -> c [2,3] p [2,3]；+ yo -> c [2,3] p [3,4]；整体 *2
        c, p = parse_row_sizes('(k*{2|3} yo)*2', 2)
        self.assertEqual(c, [4, 6])
        self.assertEqual(p, [6, 8])

    def test_nested_group_size_repeat_outside(self):
        # (k yo)*2 -> c 2 p 4；整体 *{2|3}
        c, p = parse_row_sizes('((k yo)*2)*{2|3}', 2)
        self.assertEqual(c, [4, 6])
        self.assertEqual(p, [8, 12])

    def test_single_size_brace(self):
        c, p = parse_row_sizes('k*{5}', 1)
        self.assertEqual((c, p), ([5], [5]))
        self.assertEqual(parse_row('k*{5}'), (5, 5))

    def test_count_mismatch_reports_position(self):
        with self.assertRaises(ParseError) as ctx:
            parse_row_sizes('kp*{6|8}', 3)
        self.assertIn('项数不符', str(ctx.exception))
        self.assertEqual(ctx.exception.pos, 2)  # '*' 的下标

    def test_bad_size_repeat(self):
        for bad in ['k*{0|2}', 'k*{2|}', 'k*{|2}', 'k*{}', 'k*{2 3}',
                    'k*{2|3', 'k*{2|3}*2', 'k*{a|b}', 'k*2|3']:
            with self.assertRaises(ParseError, msg=bad):
                parse_row_sizes(bad, 2)

    def test_huge_size_multiplier_is_not_expanded(self):
        c, p = parse_row_sizes('(k yo)*{1000000000|1}', 2)
        self.assertEqual(c, [1000000000, 1])
        self.assertEqual(p, [2000000000, 2])


class TestParseRowStitchCounts(unittest.TestCase):
    def test_basic_stitch_counts(self):
        tallies = parse_row_stitch_counts('k p yo k2tog', 1)
        self.assertEqual(tallies['k'], [1])
        self.assertEqual(tallies['p'], [1])
        self.assertEqual(tallies['yo'], [1])
        self.assertEqual(tallies['k2tog'], [1])

    def test_repeat_multiplies_counts(self):
        tallies = parse_row_stitch_counts('(k2tog yo)*6', 1)
        self.assertEqual(tallies['k2tog'], [6])
        self.assertEqual(tallies['yo'], [6])
        self.assertEqual(tallies['k'], [0])

    def test_nested_groups(self):
        # ((k yo)*2 k2tog)*3：k=6, yo=6, k2tog=3
        tallies = parse_row_stitch_counts('((k yo)*2 k2tog)*3', 1)
        self.assertEqual(tallies['k'], [6])
        self.assertEqual(tallies['yo'], [6])
        self.assertEqual(tallies['k2tog'], [3])

    def test_size_repeats_counted_per_size(self):
        tallies = parse_row_stitch_counts('(k2tog yo)*{6|8|10}', 3)
        self.assertEqual(tallies['k2tog'], [6, 8, 10])
        self.assertEqual(tallies['yo'], [6, 8, 10])

    def test_nested_mixed_repeats(self):
        # (k*{2|3} yo)*2：k=[4,6]，yo=[2,2]
        tallies = parse_row_stitch_counts('(k*{2|3} yo)*2', 2)
        self.assertEqual(tallies['k'], [4, 6])
        self.assertEqual(tallies['yo'], [2, 2])
        self.assertEqual(tallies['p'], [0, 0])
        self.assertEqual(tallies['k2tog'], [0, 0])

    def test_atom_size_repeat(self):
        tallies = parse_row_stitch_counts('k*{6|8|10}', 3)
        self.assertEqual(tallies['k'], [6, 8, 10])

    def test_huge_multiplier_count_not_expanded(self):
        tallies = parse_row_stitch_counts('(k yo)*1000000000', 1)
        self.assertEqual(tallies['k'], [1000000000])
        self.assertEqual(tallies['yo'], [1000000000])

    def test_details_matches_counts(self):
        c, p, tallies = parse_row_details('((k yo)*2 k2tog)*3', 1)
        self.assertEqual(c, [12])
        self.assertEqual(p, [15])
        self.assertEqual(tallies['k2tog'], [3])

    def test_invalid_row_raises(self):
        with self.assertRaises(ParseError):
            parse_row_stitch_counts('ssk', 1)


class TestParseRowRepeatSites(unittest.TestCase):
    def test_atom_repeat_site(self):
        consumes, produces, sites = parse_row_repeat_sites('k*3', 2)
        self.assertEqual(consumes, [3, 3])
        self.assertEqual(produces, [3, 3])
        self.assertEqual(len(sites), 1)
        site = sites[0]
        self.assertEqual((site.start, site.end), (1, 3))
        self.assertEqual('k*3'[site.start:site.end], '*3')
        self.assertFalse(site.sized)
        self.assertEqual(site.mults, [3, 3])
        self.assertEqual(site.unit_consume, [1, 1])
        self.assertEqual(site.unit_produce, [1, 1])
        self.assertEqual(site.outer, [1, 1])

    def test_size_repeat_site(self):
        text = '(k2tog yo)*{6|8|10}'
        consumes, produces, sites = parse_row_repeat_sites(text, 3)
        self.assertEqual(consumes, [12, 16, 20])
        self.assertEqual(len(sites), 1)
        site = sites[0]
        self.assertTrue(site.sized)
        self.assertEqual(site.mults, [6, 8, 10])
        self.assertEqual(site.unit_consume, [2, 2, 2])
        self.assertEqual(site.unit_produce, [2, 2, 2])
        self.assertEqual(site.outer, [1, 1, 1])
        self.assertEqual(text[site.start:site.end], '*{6|8|10}')

    def test_size_repeat_with_spaces_span(self):
        text = 'k*{6 | 8}'
        _c, _p, sites = parse_row_repeat_sites(text, 2)
        self.assertEqual(len(sites), 1)
        self.assertEqual(text[sites[0].start:sites[0].end], '*{6 | 8}')
        self.assertEqual(sites[0].mults, [6, 8])

    def test_nested_repeat_outer_product(self):
        # ((k2tog yo)*3)*2：内层 *3 的外层乘积应回填为 2
        consumes, _p, sites = parse_row_repeat_sites('((k2tog yo)*3)*2', 1)
        self.assertEqual(consumes, [12])
        self.assertEqual(len(sites), 2)
        inner, outer = sites  # 内层先解析完成
        self.assertEqual(inner.mults, [3])
        self.assertEqual(inner.outer, [2])
        self.assertEqual(inner.unit_consume, [2])
        self.assertEqual(outer.mults, [2])
        self.assertEqual(outer.outer, [1])
        self.assertEqual(outer.unit_consume, [6])

    def test_sibling_repeats_do_not_share_outer(self):
        # (k)*2 (yo)*3：并列的倍数互不影响外层乘积
        consumes, _p, sites = parse_row_repeat_sites('(k)*2 (yo)*3', 1)
        self.assertEqual(consumes, [2])
        self.assertEqual(len(sites), 2)
        self.assertEqual([s.outer for s in sites], [[1], [1]])
        self.assertEqual([s.mults for s in sites], [[2], [3]])

    def test_huge_multiplier_site_is_arithmetic(self):
        consumes, _p, sites = parse_row_repeat_sites('(k yo)*1000000000', 1)
        self.assertEqual(consumes, [1000000000])
        self.assertEqual(sites[0].mults, [1000000000])
        self.assertEqual(sites[0].unit_consume, [1])

    def test_details_do_not_collect_sites(self):
        # 既有解析入口行为不变，不附带倍数位置
        consumes, produces, tallies = parse_row_details('(k)*2', 1)
        self.assertEqual(consumes, [2])
        self.assertEqual(produces, [2])
        self.assertEqual(tallies['k'], [2])

    def test_invalid_row_raises(self):
        with self.assertRaises(ParseError):
            parse_row_repeat_sites('k*{2|3}', 3)


if __name__ == '__main__':
    unittest.main()
