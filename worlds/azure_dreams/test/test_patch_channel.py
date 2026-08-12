import unittest

from .. import world


class TestPatchChannel(unittest.TestCase):
    """The release-channel split: dev seeds must never look like stable seeds.

    The source world (this checkout) emits `.adpatch-dev`; only a promoted
    `.apworld` emits `.adpatch`. The zip side of the split is asserted by the
    promote script's stable smoke test, which imports the world from the
    installed apworld and reads the same constant."""

    def test_the_source_world_is_the_dev_channel(self) -> None:
        self.assertEqual(world.PATCH_EXTENSION, ".adpatch-dev")

    def test_channel_detection_keys_on_the_loader(self) -> None:
        # The split must come from HOW the module was loaded, not from an
        # editable constant - a constant is exactly what would get promoted
        # by accident. This asserts the source import reads as not-apworld.
        self.assertFalse(world._running_from_apworld())


if __name__ == "__main__":
    unittest.main()
