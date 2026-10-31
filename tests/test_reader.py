

def test_novel_paragraphs_are_joined_into_one_row_each():
    from types import SimpleNamespace as L
    import reader
    lines = [L(idx=0, start=0, end=1, zh="他", en="He"), L(idx=1, start=1, end=2, zh="走了。", en="left."),
             L(idx=2, start=2, end=3, zh="她哭了。", en="She cried.")]
    page = reader.build_reader_html(lines, "zh", {}, paragraph_ends={1, 2}, readings=False)
    assert page.count('class="line-row"') == 2
    assert "He left." in page
    assert "She cried." in page
