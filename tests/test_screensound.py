"""The Mac's sound capture, exercised with stand-ins for the macOS frameworks.

ScreenCaptureKit itself cannot run here, but the part most likely to be wrong is
reading a sample buffer, and that is plain arithmetic once macOS has handed the
bytes over. These tests pin down that reading against each format macOS uses.
"""

import array
import struct
import sys
import types
import unittest

FLOAT = 1 << 0
PACKED = 1 << 3
NON_INTERLEAVED = 1 << 5
SIGNED_INTEGER = 1 << 2


class Layout:
    def __init__(self, rate, channels, flags):
        self.mSampleRate = rate
        self.mChannelsPerFrame = channels
        self.mFormatFlags = flags


class Sample:
    """Stands in for a CMSampleBuffer, its block buffer and its description."""

    def __init__(self, data, frames, rate=48000, channels=2, flags=FLOAT | PACKED):
        self.data, self.frames = data, frames
        # PyObjC 12 dereferences AudioStreamBasicDescription pointers into tuples.
        self.layout = (rate, 0, flags, 0, 0, 0, channels, 0, 0)
        self.has_block = True


def fake_core_media():
    module = types.ModuleType("CoreMedia")
    module.CMSampleBufferGetNumSamples = lambda sample: sample.frames
    module.CMSampleBufferGetDataBuffer = lambda s: s if s.has_block else None
    module.CMSampleBufferGetFormatDescription = lambda sample: sample
    module.CMAudioFormatDescriptionGetStreamBasicDescription = lambda d: d.layout
    module.CMBlockBufferGetDataLength = lambda block: len(block.data)
    def copy_data(block, start, count, output):
        output[:count] = block.data[start : start + count]
        return 0, output

    module.CMBlockBufferCopyDataBytes = copy_data
    module.CMTimeMake = lambda value, scale: (value, scale)
    return module


def fake_screen_capture_kit():
    module = types.ModuleType("ScreenCaptureKit")
    module.SCStreamOutputTypeAudio = 1
    module.SCStreamOutputTypeScreen = 0
    module.SCStreamConfiguration = module.SCContentFilter = None
    module.SCStream = module.SCShareableContent = None
    return module


def load():
    """Import the capture module with stand-ins, leaving sys.modules as it was."""
    saved = {
        name: sys.modules.get(name)
        for name in (
            "CoreMedia",
            "ScreenCaptureKit",
            "Foundation",
            "libdispatch",
            "objc",
            "clickaway.screensound",
        )
    }
    foundation = types.ModuleType("Foundation")
    foundation.NSObject = type("NSObject", (object,), {})
    dispatch = types.ModuleType("libdispatch")
    dispatch.dispatch_queue_create = lambda name, attributes: object()
    objc = types.ModuleType("objc")
    objc.super = super
    sys.modules.update(
        {
            "CoreMedia": fake_core_media(),
            "ScreenCaptureKit": fake_screen_capture_kit(),
            "Foundation": foundation,
            "libdispatch": dispatch,
            "objc": objc,
        }
    )
    sys.modules.pop("clickaway.screensound", None)
    import clickaway.screensound as screensound

    return screensound, saved


def restore(saved):
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class MacSoundTests(unittest.TestCase):
    def setUp(self):
        self.screensound, self.saved = load()
        self.addCleanup(restore, self.saved)

    def samples(self, sample):
        data, rate, channels = self.screensound.samples_of(sample)
        values = array.array("h")
        values.frombytes(data)
        return list(values), rate, channels

    def test_channel_planes_are_read_the_way_macos_sends_them(self):
        # Left plane, then right plane: what ScreenCaptureKit delivers by default.
        data = struct.pack("<4f", 0.5, -0.5, 1.0, 0.0)
        sample = Sample(data, 2, flags=FLOAT | PACKED | NON_INTERLEAVED)
        self.assertEqual(self.samples(sample), ([16384, 32767, -16384, 0], 48000, 2))

    def test_interleaved_floats_are_read_too(self):
        data = struct.pack("<4f", 0.5, 1.0, -0.5, 0.0)
        sample = Sample(data, 2, flags=FLOAT | PACKED)
        self.assertEqual(self.samples(sample), ([16384, 32767, -16384, 0], 48000, 2))

    def test_whole_samples_are_passed_through(self):
        data = array.array("h", [1, -1, 32767, -32768]).tobytes()
        sample = Sample(data, 2, flags=SIGNED_INTEGER | PACKED)
        self.assertEqual(self.samples(sample), ([1, -1, 32767, -32768], 48000, 2))

    def test_attribute_style_stream_descriptions_remain_supported(self):
        data = struct.pack("<2f", 0.5, -0.5)
        sample = Sample(data, 1)
        sample.layout = Layout(44100, 2, FLOAT | PACKED)
        self.assertEqual(self.samples(sample), ([16384, -16384], 44100, 2))

    def test_a_sample_buffer_without_sound_is_ignored(self):
        empty = Sample(b"", 0)
        self.assertEqual(self.screensound.samples_of(empty), (b"", 0, 0))
        without_block = Sample(struct.pack("<2f", 0.5, 0.5), 1)
        without_block.has_block = False
        self.assertEqual(self.screensound.samples_of(without_block), (b"", 0, 0))

    def test_the_format_is_announced_once_and_sound_arrives_in_chunks(self):
        chunks, formats = [], []
        capture = self.screensound.SystemSoundCapture(
            chunks.append, lambda rate, channels: formats.append((rate, channels))
        )
        capture.running.set()
        chunk = 48000 * 2 * 2 * 20 // 1000
        capture.received(bytes(chunk + 10), 48000, 2)
        capture.received(bytes(chunk), 48000, 2)
        self.assertEqual(formats, [(48000, 2)])
        self.assertEqual([len(piece) for piece in chunks], [chunk, chunk])

    def test_a_stopped_capture_sends_nothing_more(self):
        chunks = []
        capture = self.screensound.SystemSoundCapture(chunks.append)
        capture.running.set()
        capture.stop()
        capture.received(bytes(10000), 48000, 2)
        self.assertEqual(chunks, [])

    def test_a_failure_is_reported_once_and_stops_the_capture(self):
        problems = []
        capture = self.screensound.SystemSoundCapture(
            lambda chunk: None, on_error=problems.append
        )
        capture.running.set()
        capture.failed("Screen recording is off")
        capture.failed("Screen recording is off")
        self.assertEqual(problems, ["Screen recording is off"])
        self.assertFalse(capture.running.is_set())


@unittest.skipUnless(sys.platform == "darwin", "Needs the real macOS frameworks")
class RealFrameworkTests(unittest.TestCase):
    """Checks every call the stand-ins cannot: that macOS really has these.

    A misspelled selector or a missing framework would otherwise only show up on
    someone's Mac, so this runs wherever the Mac app is built.
    """

    def setUp(self):
        # Importing it again would try to register its Objective-C class twice,
        # which the runtime refuses, so take whatever import is already there.
        import clickaway.screensound as screensound

        self.screensound = screensound

    def test_the_stream_takes_the_settings_clickaway_asks_for(self):
        import CoreMedia as CM
        import ScreenCaptureKit as SC

        configuration = SC.SCStreamConfiguration.alloc().init()
        configuration.setCapturesAudio_(True)
        configuration.setExcludesCurrentProcessAudio_(True)
        configuration.setSampleRate_(self.screensound.RATE)
        configuration.setChannelCount_(self.screensound.CHANNELS)
        configuration.setWidth_(2)
        configuration.setHeight_(2)
        configuration.setMinimumFrameInterval_(CM.CMTimeMake(1, 1))
        self.assertTrue(configuration.capturesAudio())
        self.assertTrue(configuration.excludesCurrentProcessAudio())
        self.assertEqual(configuration.sampleRate(), self.screensound.RATE)
        self.assertEqual(configuration.channelCount(), self.screensound.CHANNELS)

    def test_coremedia_stream_description_bridge_matches_the_reader(self):
        import CoreMedia as CM

        # AudioStreamBasicDescription: rate, format ID, flags, packet/frame sizes,
        # channels, bits per channel, reserved. This needs no recording permission.
        asbd = (
            48000.0,
            int.from_bytes(b"lpcm", "big"),
            FLOAT | PACKED,
            8,
            1,
            8,
            2,
            32,
            0,
        )
        status, description = CM.CMAudioFormatDescriptionCreate(
            None, asbd, 0, None, 0, None, None, None
        )
        self.assertEqual(status, 0)
        layout = CM.CMAudioFormatDescriptionGetStreamBasicDescription(description)
        self.assertEqual(
            self.screensound.layout_values(layout),
            (48000, 2, FLOAT | PACKED),
        )

    def test_coremedia_block_buffer_copy_bridge_matches_the_reader(self):
        import CoreMedia as CM

        source = b"clickaway-audio"
        status, block = CM.CMBlockBufferCreateWithMemoryBlock(
            None,
            None,
            len(source),
            None,
            None,
            0,
            len(source),
            0,
            None,
        )
        self.assertEqual(status, 0)
        self.assertEqual(
            CM.CMBlockBufferReplaceDataBytes(source, block, 0, len(source)),
            0,
        )
        self.assertEqual(self.screensound.block_bytes(block), source)

    def test_the_sound_sink_answers_the_calls_screencapturekit_makes(self):
        capture = self.screensound.SystemSoundCapture(lambda chunk: None)
        sink = self.screensound.ClickAwaySoundSink.alloc().initWithCapture_(capture)
        self.assertIsNotNone(sink)
        for selector in (
            b"stream:didOutputSampleBuffer:ofType:",
            b"stream:didStopWithError:",
        ):
            with self.subTest(selector=selector):
                self.assertTrue(sink.respondsToSelector_(selector))

    def test_a_queue_for_the_sound_can_be_made(self):
        from libdispatch import dispatch_queue_create

        self.assertIsNotNone(dispatch_queue_create(b"clickaway-sound-test", None))

    def test_asking_about_recording_permission_never_raises(self):
        from clickaway import macos

        self.assertIn(macos.screen_recording(), (True, False))


if __name__ == "__main__":
    unittest.main()
