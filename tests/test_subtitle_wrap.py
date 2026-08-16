from backend.app.audio import _split_subtitle_text, _wrap_subtitle
from backend.app.composer import _subtitle_filter


def test_long_chinese_caption_is_balanced_into_two_lines():
    wrapped = _wrap_subtitle("今天有一句英语，她想了很久")
    lines = wrapped.splitlines()
    assert len(lines) == 2
    assert max(map(len, lines)) <= 10


def test_short_caption_stays_on_one_line():
    assert _wrap_subtitle("先问一句") == "先问一句"


def test_long_narration_is_split_before_wrapping():
    parts = _split_subtitle_text("今天有一句英语，她想了很久，还是没能说出口。")
    assert len(parts) >= 2
    assert all(len(part) <= 20 for part in parts)


def test_english_subtitle_never_splits_inside_words():
    text = "A child struggles to pronounce an unfamiliar English word"
    parts = _split_subtitle_text(text)
    wrapped = [_wrap_subtitle(part) for part in parts]

    assert " ".join(item.replace("\n", " ") for item in wrapped) == text
    assert all(len(line) <= 26 for item in wrapped for line in item.splitlines())


def test_chinese_subtitles_split_on_clause_boundaries_without_orphan_punctuation():
    parts = _split_subtitle_text("有些话，他想用英语说出来。跟着练一遍，表达就慢慢清楚了。")

    assert "" not in parts
    assert all(part not in "，。！？；,.!?;" for part in parts)
    assert "".join(parts) == "有些话，他想用英语说出来。跟着练一遍，表达就慢慢清楚了。"


def test_master_subtitles_use_regular_simhei(tmp_path):
    subtitle = tmp_path / "captions.srt"
    subtitle.write_text("", encoding="utf-8")
    style = _subtitle_filter(subtitle)

    assert "FontName=SimHei" in style
    assert "Bold=0" in style
    assert "FontSize=11" in style
    assert "Spacing=-0.4" in style
