"""Theme-aware translucent gradient backgrounds generated with Pillow."""

from PIL import Image, ImageEnhance


def _cover_resize(image, size):
    width, height = size
    source_width, source_height = image.size
    scale = max(
        width / float(source_width),
        height / float(source_height),
    )
    resized = image.resize(
        (
            max(1, round(source_width * scale)),
            max(1, round(source_height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _vertical_alpha(size, top_alpha, bottom_alpha):
    width, height = size
    if height <= 1:
        return Image.new("L", size, top_alpha)

    column = Image.new("L", (1, height))
    values = []
    for y in range(height):
        progress = y / float(height - 1)
        eased = progress * progress * (3.0 - 2.0 * progress)
        values.append(
            round(top_alpha + (bottom_alpha - top_alpha) * eased)
        )
    column.putdata(values)
    return column.resize(size, Image.Resampling.BILINEAR)


def create_gradient_background(source_path, size, mode):
    """Return a theme-tinted RGBA gradient alpha image."""
    width, height = size
    if width < 1 or height < 1:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    source = Image.open(source_path).convert("RGBA")
    source = _cover_resize(source, size)

    if mode == "light":
        result = source.convert("RGBA")
        result.putalpha(_vertical_alpha(size, 176, 42))
        return result

    base = Image.new("RGB", size, (28, 28, 28))
    image = Image.blend(source.convert("RGB"), base, 0.62)
    image = ImageEnhance.Color(image).enhance(0.72)

    result = image.convert("RGBA")
    result.putalpha(_vertical_alpha(size, 92, 20))
    return result
