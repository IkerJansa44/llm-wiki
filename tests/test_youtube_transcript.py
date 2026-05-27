from scripts.youtube_transcript import Chapter, Cue, render_markdown, slice_chapters


def test_render_markdown_without_chapters_keeps_flat_transcript() -> None:
    markdown = render_markdown(
        title="Example",
        url="https://youtu.be/example",
        source_id="example",
        cues=[
            Cue(start=0, end=2, text="Hello."),
            Cue(start=2, end=4, text="World."),
        ],
        start=None,
        end=None,
        chapters=[],
    )

    assert "## Chapters" not in markdown
    assert "###" not in markdown
    assert "## Transcript\n\n[00:00:00] Hello." in markdown
    assert "[00:00:02] World." in markdown


def test_render_markdown_groups_transcript_by_chapters() -> None:
    markdown = render_markdown(
        title="Example",
        url="https://youtu.be/example",
        source_id="example",
        cues=[
            Cue(start=1, end=2, text="Intro line."),
            Cue(start=12, end=14, text="Main line."),
        ],
        start=None,
        end=None,
        chapters=[
            Chapter(start=0, end=10, title="Intro"),
            Chapter(start=10, end=None, title="Main Topic"),
        ],
    )

    assert "- [00:00:00] Intro" in markdown
    assert "- [00:00:10] Main Topic" in markdown
    assert "### Intro\n\n[00:00:01] Intro line." in markdown
    assert "### Main Topic\n\n[00:00:12] Main line." in markdown


def test_slice_chapters_keeps_chapter_overlapping_requested_range() -> None:
    chapters = [
        Chapter(start=0, end=100, title="Before and During"),
        Chapter(start=100, end=200, title="After"),
        Chapter(start=200, end=None, title="Later"),
    ]

    assert slice_chapters(chapters, start=50, end=150) == chapters[:2]
