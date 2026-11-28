import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from PIL import Image

from ui_background import create_gradient_background


class GradientBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.source = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "bg.png"
        )

    def test_light_mode_preserves_rgb_with_alpha_fade(self):
        with TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.png"
            Image.new(
                "RGB",
                (16, 16),
                (230, 245, 250),
            ).save(source_path)

            image = create_gradient_background(
                source_path,
                (16, 16),
                "light",
            )

        self.assertEqual(image.convert("RGB").getpixel((8, 8)), (230, 245, 250))
        self.assertEqual(image.getpixel((8, 0))[3], 176)
        self.assertEqual(image.getpixel((8, 15))[3], 42)

    def test_gradient_output_matches_requested_size(self):
        image = create_gradient_background(
            self.source,
            (320, 240),
            "light",
        )
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size, (320, 240))

    def test_dark_gradient_is_more_subdued(self):
        light = create_gradient_background(
            self.source,
            (320, 240),
            "light",
        )
        dark = create_gradient_background(
            self.source,
            (320, 240),
            "dark",
        )
        self.assertGreater(
            max(light.getchannel("A").getdata()),
            max(dark.getchannel("A").getdata()),
        )


if __name__ == "__main__":
    unittest.main()
