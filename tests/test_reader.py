

def test_novel_paragraphs_are_joined_into_one_row_each():
    from types import SimpleNamespace as L
    import reader
    lines = [L(idx=0, start=0, end=1, zh="他", en="He"), L(idx=1, start=1, end=2, zh="走了。", en="left."),
             L(idx=2, start=2, end=3, zh="她哭了。", en="She cried.")]
    page = reader.build_reader_html(lines, "zh", {}, paragraph_ends={1, 2}, readings=False)
    assert page.count('class="line-row"') == 2
    assert "He left." in page
    assert "She cried." in page


def test_korean_paragraph_keeps_the_space_after_a_sentence_but_not_inside_a_cut_word():
    from types import SimpleNamespace as L
    import reader
    import novel_text
    paragraph = "그는 그녀에게 거짓말을 할 생각이 없었다. 하지만 말이 먼저 나와 버렸고, 이제 그녀는 그를 낯선 사람처럼 바라보고 있었다."
    chunks = novel_text.chunk_novel_text(paragraph, 40)
    lines = [L(idx=i, start=0, end=1, zh=c, en="") for i, c in enumerate(chunks)]
    joined = reader._join_paragraphs(lines, {len(lines) - 1}, "ko")
    assert joined[0].zh == paragraph
    assert reader._join_paragraphs(lines, {len(lines) - 1}, "ja")[0].zh == "".join(chunks)


def test_editable_page_gives_each_line_its_own_edit_button():
    from types import SimpleNamespace as L
    import reader
    lines = [L(idx=i, start=i, end=i + 1, zh="你好", en=f"Hi {i}", id=100 + i) for i in range(2)]
    html = reader.build_reader_html(lines, "zh", {}, editable=True)
    assert 'data-line-id="100"' in html and 'onclick="editLine(101)"' in html
    assert 'class="edit-btn"' not in reader.build_reader_html(lines, "zh", {})
