"""编织指令解析器：按括号与倍数计算每行的消耗/产出针数。

支持的记号（大小写不敏感，可用空格或逗号分隔）：
  k       下针：消耗 1 针，产出 1 针
  p       上针：消耗 1 针，产出 1 针
  yo      挂线：不消耗，产出 1 针
  k2tog   两针并一针：消耗 2 针，产出 1 针
  (序列)*n          括号序列重复 n 次，可嵌套；单个记号后也可跟 *n
  (序列)*{a|b|c}    按尺码分别取倍数：竖线分隔的正整数按尺码顺序一一对应，
                    项数必须与尺码个数一致；单个记号后同样可跟 *{a|b|c}

普通 *n 对所有尺码生效，*{a|b|c} 与 *n 两种重复可互相嵌套。
解析只做算术累计，绝不展开重复项，因此 (k)*1000000000 也能瞬间算完。

除消耗/产出针数外，解析器还可返回每种记号（k/p/yo/k2tog）在每行中每个
尺码的实际出现次数（parse_row_details / parse_row_stitch_counts），供线材
用量按“记号次数 × 单记号克重”累计。

parse_row_repeat_sites 额外返回行内每个 *n / *{...} 倍数的位置与算术信息
（RepeatSite），供“只改一个倍数”的修正建议做纯算术推导：嵌套重复只累计
外层倍数之积，不展开重复项。
"""

import re

# 记号顺序（稳定的遍历顺序）
STITCH_NAMES = ('k', 'p', 'yo', 'k2tog')

# 记号 -> (消耗针数, 产出针数)
STITCH_COUNTS = {
    'k': (1, 1),
    'p': (1, 1),
    'yo': (0, 1),
    'k2tog': (2, 1),
}

# 注意 k2tog 必须排在 k 之前，保证最长匹配
TOKEN_RE = re.compile(r"""
    (?P<STITCH>k2tog|yo|k|p)
  | (?P<LPAREN>\()
  | (?P<RPAREN>\))
  | (?P<STAR>\*)
  | (?P<LBRACE>\{)
  | (?P<RBRACE>\})
  | (?P<PIPE>\|)
  | (?P<NUM>\d+)
  | (?P<SEP>[\s,]+)
""", re.IGNORECASE | re.VERBOSE)


class ParseError(ValueError):
    """指令解析错误，message 中已包含定位信息。

    pos 为出错字符在原文中的下标（从 0 计），供接口返回结构化的字符位置；
    无法定位到具体字符时为 None。
    """

    def __init__(self, message, pos=None):
        super().__init__(message)
        self.pos = pos


class RepeatSite(object):
    """行内一个 *n 或 *{a|b|c} 倍数的位置与算术信息（供修正建议推导）。

    start/end 为倍数文本（含 '*'）在原文中的下标（0 起，end 不含）；
    sized 表示是否为 *{a|b|c} 形式；mults 为各尺码当前倍数（长度 num_sizes）。
    unit_consume/unit_produce 为被重复单元（单个记号或括号组）自身各尺码的
    消耗/产出（含其内部重复，不含本倍数）；outer 为外层各尺码倍数之积，
    由外层重复在解析完成时回填。本行各尺码针数满足
        行合计 = 与本倍数无关的部分 + mults × unit × outer
    因此只改本倍数时可用纯算术反解新倍数，无需展开任何重复项。
    """

    def __init__(self, start, end, sized, mults, unit_consume, unit_produce):
        self.start = start
        self.end = end
        self.sized = sized
        self.mults = mults
        self.unit_consume = unit_consume
        self.unit_produce = unit_produce
        self.outer = [1] * len(mults)


def _tokenize(text):
    tokens = []
    pos = 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise ParseError(
                "无法识别的记号 {!r}（第 {} 个字符）".format(text[pos], pos + 1), pos)
        if m.lastgroup != 'SEP':
            tokens.append((m.lastgroup, m.group(), m.start()))
        pos = m.end()
    return tokens


class _Parser(object):
    def __init__(self, tokens, num_sizes, collect_sites=False):
        self.tokens = tokens
        self.i = 0
        self.num_sizes = num_sizes
        # collect_sites 为 True 时收集行内所有倍数位置（RepeatSite）
        self.sites = [] if collect_sites else None

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def advance(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse_row(self):
        """row := sequence EOF

        返回 (consumes, produces, tallies)：consumes/produces 为长度 num_sizes
        的针数列表；tallies 为 {记号名: 长度 num_sizes 的次数列表}。
        """
        c, p, tallies = self.parse_sequence()
        tok = self.peek()
        if tok is not None:
            kind, val, pos = tok
            if kind == 'RPAREN':
                raise ParseError("多余的右括号 ')'（第 {} 个字符）".format(pos + 1), pos)
            if kind == 'NUM':
                raise ParseError(
                    "意外的数字 {!r}（第 {} 个字符）：倍数需写成 (序列)*次数 的形式".format(val, pos + 1), pos)
            if kind == 'STAR':
                raise ParseError(
                    "意外的 '*'（第 {} 个字符）：'*' 必须跟在记号或 ')' 之后".format(pos + 1), pos)
            raise ParseError("意外的记号 {!r}（第 {} 个字符）".format(val, pos + 1), pos)
        return c, p, tallies

    def parse_sequence(self):
        """sequence := item+，消耗/产出/记号次数逐项累加"""
        total_c = [0] * self.num_sizes
        total_p = [0] * self.num_sizes
        total_t = {name: [0] * self.num_sizes for name in STITCH_NAMES}
        while True:
            tok = self.peek()
            if tok is None or tok[0] not in ('STITCH', 'LPAREN'):
                break
            c, p, tallies = self.parse_item()
            for j in range(self.num_sizes):
                total_c[j] += c[j]
                total_p[j] += p[j]
                for name in STITCH_NAMES:
                    total_t[name][j] += tallies[name][j]
        return total_c, total_p, total_t

    def parse_item(self):
        """item := (STITCH | '(' sequence ')') ('*' repeat)?"""
        # 记录进入本项前已收集的倍数数，用于把本项倍数回填为子树内倍数的外层
        mark = len(self.sites) if self.sites is not None else 0
        kind, val, pos = self.advance()
        if kind == 'STITCH':
            c0, p0 = STITCH_COUNTS[val.lower()]
            c = [c0] * self.num_sizes
            p = [p0] * self.num_sizes
            tallies = {name: [0] * self.num_sizes for name in STITCH_NAMES}
            tallies[val.lower()] = [1] * self.num_sizes
        else:  # LPAREN
            c, p, tallies = self.parse_sequence()
            if not any(c) and not any(p):
                raise ParseError("括号内不能为空（第 {} 个字符）".format(pos + 1), pos)
            tok = self.peek()
            if tok is None:
                raise ParseError("缺少与 '(' 对应的 ')'（第 {} 个字符）".format(pos + 1), pos)
            if tok[0] != 'RPAREN':
                raise ParseError(
                    "括号内出现意外的记号 {!r}（第 {} 个字符）".format(tok[1], tok[2] + 1), tok[2])
            self.advance()
        tok = self.peek()
        if tok is not None and tok[0] == 'STAR':
            self.advance()
            mults, end, sized = self.parse_repeat(tok)
            if self.sites is not None:
                # 子树内（被重复单元中）的倍数都嵌套在本倍数之内，外层乘积乘上它
                for site in self.sites[mark:]:
                    site.outer = [o * m for o, m in zip(site.outer, mults)]
                self.sites.append(RepeatSite(
                    tok[2], end, sized, list(mults), list(c), list(p)))
            c = [cv * m for cv, m in zip(c, mults)]
            p = [pv * m for pv, m in zip(p, mults)]
            tallies = {
                name: [cnt * m for cnt, m in zip(counts, mults)]
                for name, counts in tallies.items()
            }
        return c, p, tallies

    def parse_repeat(self, star_tok):
        """repeat := NUM | '{' NUM ('|' NUM)* '}'。

        返回 (mults, end, sized)：mults 为长度 num_sizes 的倍数列表；end 为
        倍数文本在原文中的结束下标（0 起，不含）；sized 表示是否为 *{a|b|c}。
        普通 *n 对所有尺码生效；*{a|b|c} 按尺码顺序分别取值，项数必须等于尺码数。
        """
        star_pos = star_tok[2]
        tok = self.peek()
        if tok is None:
            raise ParseError(
                "'*' 后必须跟正整数倍数（第 {} 个字符）".format(star_pos + 1), star_pos)
        if tok[0] == 'NUM':
            self.advance()
            end = tok[2] + len(tok[1])
            return [self._positive_num(tok)] * self.num_sizes, end, False
        if tok[0] == 'LBRACE':
            self.advance()
            values = []
            end = None
            while True:
                ntok = self.peek()
                if ntok is None:
                    raise ParseError(
                        "尺码倍数 '{{...}}' 未闭合，缺少正整数（第 {} 个字符）".format(tok[2] + 1), tok[2])
                if ntok[0] != 'NUM':
                    raise ParseError(
                        "尺码倍数 '{{...}}' 中必须是正整数，收到 {!r}（第 {} 个字符）".format(
                            ntok[1], ntok[2] + 1), ntok[2])
                self.advance()
                values.append(self._positive_num(ntok))
                sep = self.peek()
                if sep is not None and sep[0] == 'PIPE':
                    self.advance()
                    continue
                if sep is not None and sep[0] == 'RBRACE':
                    self.advance()
                    end = sep[2] + 1
                    break
                if sep is None:
                    raise ParseError(
                        "尺码倍数 '{{...}}' 缺少右括号 '}}'（第 {} 个字符）".format(tok[2] + 1), tok[2])
                raise ParseError(
                    "尺码倍数 '{{...}}' 的数字需用 '|' 分隔，收到 {!r}（第 {} 个字符）".format(
                        sep[1], sep[2] + 1), sep[2])
            if len(values) != self.num_sizes:
                raise ParseError(
                    "尺码倍数项数不符：共 {} 个尺码，但倍数给出了 {} 个值（第 {} 个字符）".format(
                        self.num_sizes, len(values), star_pos + 1), star_pos)
            return values, end, True
        raise ParseError(
            "'*' 后必须跟正整数倍数或按尺码的 '{{a|b|...}}'（第 {} 个字符）".format(tok[2] + 1), tok[2])

    @staticmethod
    def _positive_num(ntok):
        n = int(ntok[1])
        if n <= 0:
            raise ParseError(
                "倍数必须为正整数，收到 {}（第 {} 个字符）".format(n, ntok[2] + 1), ntok[2])
        return n


def parse_row_details(text, num_sizes):
    """解析一行指令，返回 (consumes, produces, tallies)。

    consumes/produces 为各尺码的消耗/产出针数列表（长度 num_sizes）；
    tallies 为 {记号名: 各尺码该记号次数列表}，嵌套重复与按尺码重复均按
    实际次数算术累计（不展开重复项）。非法输入抛出 ParseError。
    """
    if num_sizes < 1:
        raise ValueError("num_sizes 必须为正整数")
    tokens = _tokenize(text)
    if not tokens:
        raise ParseError("该行没有有效指令")
    return _Parser(tokens, num_sizes).parse_row()


def parse_row_sizes(text, num_sizes):
    """解析一行指令，返回 (consumes, produces)：各尺码的消耗/产出针数列表。

    两个列表长度均等于 num_sizes，按尺码顺序排列；非法输入抛出 ParseError。
    """
    consumes, produces, _tallies = parse_row_details(text, num_sizes)
    return consumes, produces


def parse_row_stitch_counts(text, num_sizes):
    """解析一行指令，返回各尺码的记号次数 {记号名: 长度 num_sizes 的次数列表}。"""
    _c, _p, tallies = parse_row_details(text, num_sizes)
    return tallies


def parse_row_repeat_sites(text, num_sizes):
    """解析一行指令，返回 (consumes, produces, sites)。

    consumes/produces 与 parse_row_sizes 相同；sites 为行内全部 *n 与
    *{a|b|c} 倍数的 RepeatSite 列表（按解析完成顺序，嵌套内层在前），
    供“只改一个倍数”的修正建议做算术推导。全程只做算术累计，不展开重复项。
    非法输入抛出 ParseError。
    """
    if num_sizes < 1:
        raise ValueError("num_sizes 必须为正整数")
    tokens = _tokenize(text)
    if not tokens:
        raise ParseError("该行没有有效指令")
    parser = _Parser(tokens, num_sizes, collect_sites=True)
    consumes, produces, _tallies = parser.parse_row()
    return consumes, produces, parser.sites


def parse_row(text):
    """单尺码解析：返回 (消耗针数, 产出针数)。非法输入抛出 ParseError。"""
    consumes, produces, _tallies = parse_row_details(text, 1)
    return consumes[0], produces[0]
