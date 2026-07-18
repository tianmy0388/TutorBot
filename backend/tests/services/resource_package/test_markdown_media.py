"""Regression coverage for Markdown image ownership."""

def test_unowned_relative_image_becomes_visible_placeholder():
    from tutor.services.resource_package.markdown_media import (
        replace_unowned_markdown_images,
    )

    assert "图片未提供" in replace_unowned_markdown_images(
        "![Dyna](dyna_diagram.png)",
        set(),
    )


def test_owned_relative_image_is_preserved_by_basename():
    from tutor.services.resource_package.markdown_media import (
        replace_unowned_markdown_images,
    )

    markdown = "![Dyna](images/dyna_diagram.png)"
    assert replace_unowned_markdown_images(markdown, {"dyna_diagram.png"}) == markdown
