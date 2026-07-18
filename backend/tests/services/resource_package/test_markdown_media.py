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


def test_absolute_and_non_http_image_sources_cannot_borrow_an_owned_basename():
    from tutor.services.resource_package.markdown_media import (
        replace_unowned_markdown_images,
    )

    sources = [
        "file:///tmp/owned.png",
        "C:\\temp\\owned.png",
        "\\\\server\\share\\owned.png",
        "/tmp/owned.png",
        "ftp://example.test/owned.png",
    ]
    markdown = "\n".join(f"![unsafe]({source})" for source in sources)

    replaced = replace_unowned_markdown_images(markdown, {"owned.png"})

    assert replaced.count("图片未提供") == len(sources)
    assert "owned.png)" not in replaced


def test_commonmark_image_destinations_preserve_only_owned_relative_targets():
    from tutor.services.resource_package.markdown_media import (
        replace_unowned_markdown_images,
    )

    markdown = (
        "![owned](<images/owned image.png> \"caption\")\n"
        "![unowned](<unowned image.png> 'caption')\n"
        "![escaped](unowned\\ image.png \"caption\")"
    )

    replaced = replace_unowned_markdown_images(markdown, {"owned image.png"})

    assert "![owned](<images/owned image.png> \"caption\")" in replaced
    assert replaced.count("图片未提供") == 2
