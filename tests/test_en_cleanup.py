"""Unit tests for en_cleanup.py: every rule, plus the no-op cases that keep it safe."""
import pytest

import en_cleanup as ec

DOTS = {"quotes": "straight", "ellipsis": "dots"}
CHAR_CURLY = {"quotes": "curly", "ellipsis": "char"}


def clean(text, style=DOTS, **kw):
    return ec.clean_text(text, style, **kw)


def only(rule, text, style=DOTS):
    return ec.clean_text(text, style, rules=[rule])[0]


@pytest.mark.parametrize("rule,before,after", [
    ("cjk_punctuation", "Hello，world。", "Hello,world."),
    ("cjk_punctuation", "Ｗait！Ｎo？１２", "Wait!No?12"),
    ("cjk_punctuation", "He said 「go」", 'He said "go"'),
    ("ellipsis", "Wait... what", "Wait... what"),
    ("extra_space", "Hello   there  friend ", "Hello there friend"),
    ("extra_space", "One \n  two", "One\ntwo"),
    ("repeated_punctuation", "Well,, yes;; ok::", "Well, yes; ok:"),
    ("repeated_punctuation", "What?! Stop!!", "What?! Stop!!"),
    ("space_before_punctuation", "Hello , world !", "Hello, world!"),
    ("space_before_punctuation", "It is 10 :30", "It is 10 :30"),
    ("space_inside_brackets", "( aside ) and [ note ]", "(aside) and [note]"),
    ("space_inside_brackets", 'He said " go " now', 'He said "go" now'),
    ("space_after_punctuation", "Yes,no;ok!Fine?Sure", "Yes, no; ok! Fine? Sure"),
    ("space_after_punctuation", "end.Next", "end. Next"),
    ("doubled_word", "Go to the the store", "Go to the store"),
    ("doubled_word", "The the end", "The end"),
    ("pronoun_i", "i think i'm right and i’ll go", "I think I'm right and I’ll go"),
    ("capitalize_sentence", "It ended. then he left! ok", "It ended. Then he left! Ok"),
])
def test_rule_fixes(rule, before, after):
    assert only(rule, before) == after


@pytest.mark.parametrize("rule,text", [
    ("cjk_punctuation", "Plain text."),
    ("ellipsis", "No dots here"),
    ("extra_space", "Already fine"),
    ("repeated_punctuation", "Wait... what?!"),
    ("space_before_punctuation", "Prices: 3.5 and 10:30"),
    ("space_inside_brackets", 'He said "go" (now)'),
    ("space_inside_brackets", 'Odd " quote count'),
    ("space_after_punctuation", "U.S.A, e.g. 3,000 and Mr.Smith"),
    ("quotes", "No quotes here"),
    ("doubled_word", "He had had enough. That that is odd. Bye bye."),
    ("pronoun_i", "Plan (i) and i) and i.e. the list, chapter I"),
    ("capitalize_sentence", "Dr. smith and e.g. the thing, J. smith, etc. and so on"),
    ("capitalize_sentence", "Wait... then he left"),
    ("capitalize_sentence", "It ended. iPhone sales"),
])
def test_rule_noops(rule, text):
    assert only(rule, text) == text


def test_ellipsis_follows_the_title_style():
    assert only("ellipsis", "Wait... what… no……", DOTS) == "Wait... what... no..."
    assert only("ellipsis", "Wait... what… no……", CHAR_CURLY) == "Wait… what… no…"


def test_quotes_follow_the_title_style():
    assert only("quotes", 'He said "it\'s fine"', CHAR_CURLY) == "He said “it’s fine”"
    assert only("quotes", "He said “it’s fine”", DOTS) == 'He said "it\'s fine"'


def test_straight_quotes_stay_when_pairing_is_unsafe():
    assert only("quotes", 'one"two"', CHAR_CURLY) == 'one"two"'
    assert only("quotes", 'say "hi', CHAR_CURLY) == 'say "hi'


def test_detect_style_takes_the_majority():
    assert ec.detect_style(["“a”", "“b”", '"c"'])["quotes"] == "curly"
    assert ec.detect_style(['"a"', "“b”", '"c"'])["quotes"] == "straight"
    assert ec.detect_style(["a… b", "c…", "d..."])["ellipsis"] == "char"
    assert ec.detect_style([])  == {"quotes": "straight", "ellipsis": "dots"}


def test_leading_lowercase_of_the_line_is_never_capitalised():
    assert clean("and then he left")[0] == "and then he left"


def test_non_english_lines_are_untouched():
    for text in ("你好 ,world", "こんにちは  i  ", "안녕  the the"):
        assert clean(text) == (text, [])


def test_speaker_labels_markup_urls_and_glossary_terms_are_protected():
    assert clean("JOHN : i think so")[0] == "JOHN : I think so"
    assert clean("- Mary (O.S.):  i go")[0] == "- Mary (O.S.): I go"
    assert clean("<i>hello</i>  i  go")[0] == "<i>hello</i> I go"
    assert clean("{\\an8}the  end")[0] == "{\\an8}the end"
    assert clean("see www.Foo.com,and go")[0] == "see www.Foo.com,and go"
    assert clean("the the Void Blade,x", protected_terms=["Void  Blade"])[0] == "the Void Blade, x"
    assert clean("i met  the ,wolf the wolf", protected_terms=["the wolf"])[0] == "I met the, wolf the wolf"
    assert clean("Ann: the the", speaker="Ann")[0] == "Ann: the"


def test_protected_term_text_is_not_edited():
    text, rules = clean("the Wolf ,king", protected_terms=["Wolf ,king"])
    assert text == "the Wolf ,king" and rules == []


def test_private_use_characters_skip_the_line():
    assert clean("odd  text  here") == ("odd  text  here", [])


def test_clean_text_reports_each_rule_once():
    text, rules = clean("hello  , i think the the…")
    assert text == "hello, I think the..."
    assert rules == ["ellipsis", "extra_space", "space_before_punctuation", "doubled_word",
                     "pronoun_i"]


def test_rules_argument_limits_what_runs():
    assert ec.clean_text("hi  , i", DOTS, rules=["pronoun_i"])[0] == "hi  , I"


def test_clean_is_idempotent():
    once = clean("Hello ,, i think  the the。。。 ok.then 「go」")[0]
    assert clean(once)[0] == once


def test_plan_hash_changes_with_any_change():
    a = ec.plan_hash([(1, "x"), (2, "y")])
    assert a == ec.plan_hash([(1, "x"), (2, "y")])
    assert a != ec.plan_hash([(1, "x"), (2, "z")])
    assert a != ec.plan_hash([(1, "xy")])


@pytest.mark.parametrize("text, expected", [
    ("<i>Go to www.example.com</i>  now", "<i>Go to www.example.com</i> now"),
    ("<i>www.x.com</i>  i", "<i>www.x.com</i> I"),
    ("see www.x.com<i>  go</i>", "see www.x.com<i> go</i>"),
    ("<i>go</i>www.x.com  now", "<i>go</i>www.x.com now"),
    ("<i>a@b.com</i>  i go", "<i>a@b.com</i> I go"),
    ("mail a@b.com<b>  now</b>", "mail a@b.com<b> now</b>"),
])
def test_urls_next_to_tags_keep_every_tag(text, expected):
    assert clean(text)[0] == expected


def test_leaked_private_use_character_discards_the_change(monkeypatch):
    monkeypatch.setitem(ec._RULE_FUNCS, "extra_space", lambda t, s: t + chr(0xE000))
    assert clean("hello  there") == ("hello  there", [])


def _fast(fn, bound=1.0):
    import time
    t = time.perf_counter()
    fn()
    assert time.perf_counter() - t < bound


def test_adversarial_long_lines_stay_fast():
    _fast(lambda: clean("a. " * 660 + "b"))
    _fast(lambda: clean("a." * 1000 + "@"))
    _fast(lambda: clean('x "' * 660, style={"quotes": "curly", "ellipsis": "dots"}))


def test_over_long_lines_are_skipped():
    text = "hello  there " * 400
    assert len(text) > ec.MAX_LINE_CHARS
    assert clean(text) == (text, [])
    assert ec.too_long(text) and not ec.too_long("short")


def test_many_glossary_terms_match_like_one_at_a_time():
    terms = [f"Term{i} Name" for i in range(600)]
    text = "the Term599 Name ,and term0 name ,x"
    assert clean(text, protected_terms=terms)[0] == "the Term599 Name, and term0 name, x"
    pat = ec.compile_terms(terms)
    assert clean(text, protected_terms=pat) == clean(text, protected_terms=terms)
    assert clean("a ,b", protected_terms=pat)[0] == "a, b"
