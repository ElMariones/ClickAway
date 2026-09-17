"""Mac system sound capture with ScreenCaptureKit.

macOS has no equivalent of Windows' loopback recording: an app cannot simply ask
for a copy of what the speakers are playing. ScreenCaptureKit can, because it was
built to record the screen, so this asks it for a two-pixel picture it throws away
and keeps the sound that comes with it. That is why the Mac asks for Screen
Recording permission before it can send any sound.

Nothing here runs on the Qt thread: ScreenCaptureKit calls back on its own
dispatch queue, and results reach the app through the callbacks it passed in.
"""

import threading

import CoreMedia as CM
from Foundation import NSObject
from libdispatch import dispatch_queue_create
import objc
import ScreenCaptureKit as SC

from .audio import CHUNK_MS, Chunker, planes_to_int16, to_int16

RATE = 48000
CHANNELS = 2
FLOAT = 1 << 0  # kAudioFormatFlagIsFloat
NON_INTERLEAVED = 1 << 5  # kAudioFormatFlagIsNonInterleaved
ANSWER_SECONDS = 8


def describe(error, what):
    reason = (error.localizedDescription() if error is not None else "") or ""
    return (
        f"This Mac's sound {what}. {reason} Turn ClickAway on in System Settings → "
        "Privacy & Security → Screen & System Audio Recording."
    ).replace("  ", " ")


def layout_values(layout):
    """Return ``(rate, channels, flags)`` from a Core Audio stream description.

    PyObjC 12 bridges ``const AudioStreamBasicDescription *`` as a plain tuple,
    while older/test bridges can expose the C fields as attributes.  Accept both:
    the tuple follows the native ASBD field order documented by Core Audio.
    """
    try:
        rate = layout.mSampleRate
        channels = layout.mChannelsPerFrame
        flags = layout.mFormatFlags
    except AttributeError:
        try:
            rate = layout[0]
            flags = layout[2]
            channels = layout[6]
        except (IndexError, TypeError) as exc:
            raise TypeError("unexpected Core Audio stream description") from exc
    return int(rate), int(channels) or 1, int(flags)


def block_bytes(block):
    """Copy a CoreMedia block buffer into Python-owned bytes.

    PyObjC exposes CMBlockBufferCopyDataBytes' final ``void *`` as an explicit
    writable output argument.  It may also echo that output buffer in its return
    tuple, so use the status from the return value but keep our owned buffer as
    the source of truth.
    """
    length = CM.CMBlockBufferGetDataLength(block)
    if not length:
        return b""
    output = bytearray(length)
    result = CM.CMBlockBufferCopyDataBytes(block, 0, length, output)
    status = result[0] if isinstance(result, tuple) else result
    return bytes(output) if status == 0 else b""


def samples_of(sample):
    """One sample buffer as (interleaved 16-bit samples, rate, channels)."""
    frames = CM.CMSampleBufferGetNumSamples(sample)
    block = CM.CMSampleBufferGetDataBuffer(sample)
    description = CM.CMSampleBufferGetFormatDescription(sample)
    if not frames or block is None or description is None:
        return b"", 0, 0
    layout = CM.CMAudioFormatDescriptionGetStreamBasicDescription(description)
    if layout is None:
        return b"", 0, 0
    rate, channels, flags = layout_values(layout)
    data = block_bytes(block)
    if not data:
        return b"", 0, 0
    if not flags & FLOAT:
        return to_int16(data, channels, False), rate, min(channels, 2)
    if flags & NON_INTERLEAVED:
        return planes_to_int16(data, channels, frames), rate, min(channels, 2)
    return to_int16(data, channels, True), rate, min(channels, 2)


class ClickAwaySoundSink(NSObject):
    """Takes sample buffers off ScreenCaptureKit's queue, and stream failures.

    Objective-C class names belong to the whole process, so this one is spelled
    out in full rather than shortened, and the module may only be imported once.
    """

    def initWithCapture_(self, capture):
        self = objc.super(ClickAwaySoundSink, self).init()
        if self is not None:
            self.capture = capture
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample, kind):
        # An exception raised here would cross back into ScreenCaptureKit.
        try:
            if kind == SC.SCStreamOutputTypeAudio:
                self.capture.received(*samples_of(sample))
        except Exception as exc:
            self.capture.failed(f"This Mac's sound could not be read: {exc}")

    def stream_didStopWithError_(self, stream, error):
        try:
            self.capture.failed(describe(error, "stopped"))
        except Exception:
            pass


class SystemSoundCapture:
    """Streams what this Mac is playing to ``on_chunk`` until ``stop`` is called."""

    def __init__(self, on_chunk, on_format=None, on_error=None):
        self.on_chunk = on_chunk
        self.on_format = on_format or (lambda rate, channels: None)
        self.on_error = on_error or (lambda text: None)
        self.running = threading.Event()
        self.stream = self.queue = self.sink = None
        self.chunker = None
        self.format = None
        self.lock = threading.Lock()

    def start(self):
        """Begin opening the stream. The format is reported once sound arrives."""
        self.running.set()
        threading.Thread(
            target=self._open, daemon=True, name="clickaway-mac-sound"
        ).start()

    def stop(self):
        self.running.clear()
        with self.lock:
            stream, self.stream = self.stream, None
            self.chunker, self.format = None, None
        if stream is not None:
            stream.stopCaptureWithCompletionHandler_(lambda error: None)

    def received(self, samples, rate, channels):
        if not samples or not self.running.is_set():
            return
        with self.lock:
            if self.format != (rate, channels):
                self.format = (rate, channels)
                self.chunker = Chunker(
                    rate * channels * 2 * CHUNK_MS // 1000, self.on_chunk
                )
                self.on_format(rate, channels)
            self.chunker.add(samples)

    def failed(self, reason):
        if self.running.is_set():
            self.running.clear()
            self.on_error(reason)

    def _open(self):
        try:
            display = self._display()
            configuration = SC.SCStreamConfiguration.alloc().init()
            configuration.setCapturesAudio_(True)
            configuration.setExcludesCurrentProcessAudio_(True)
            configuration.setSampleRate_(RATE)
            configuration.setChannelCount_(CHANNELS)
            # The picture is thrown away, so ask for the smallest and slowest one.
            configuration.setWidth_(2)
            configuration.setHeight_(2)
            configuration.setMinimumFrameInterval_(CM.CMTimeMake(1, 1))
            content = SC.SCContentFilter.alloc().initWithDisplay_excludingWindows_(
                display, []
            )
            self.sink = ClickAwaySoundSink.alloc().initWithCapture_(self)
            self.queue = dispatch_queue_create(b"clickaway-sound", None)
            stream = SC.SCStream.alloc().initWithFilter_configuration_delegate_(
                content, configuration, self.sink
            )
            # A stream with nothing taking its picture can stop on its own, so the
            # two-pixel frames are collected and dropped alongside the sound.
            for kind in (SC.SCStreamOutputTypeScreen, SC.SCStreamOutputTypeAudio):
                added, error = stream.addStreamOutput_type_sampleHandlerQueue_error_(
                    self.sink, kind, self.queue, None
                )
                if not added:
                    raise RuntimeError(describe(error, "could not be shared"))
            self.stream = stream
            self._await(stream.startCaptureWithCompletionHandler_, "could not start")
            if not self.running.is_set():
                self.stop()
        except Exception as exc:
            self.failed(str(exc))

    @staticmethod
    def _display():
        found, failed = [], []
        ready = threading.Event()

        def answered(content, error):
            if error is not None:
                failed.append(error)
            elif content is not None and content.displays():
                found.append(content.displays()[0])
            ready.set()

        SC.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            True, False, answered
        )
        if not ready.wait(ANSWER_SECONDS):
            raise RuntimeError("macOS did not answer the request to share sound.")
        if failed or not found:
            raise RuntimeError(
                describe(failed[0] if failed else None, "was not allowed")
            )
        return found[0]

    @staticmethod
    def _await(call, what):
        """Run one completion-handler call and wait for its answer off the Qt thread."""
        done, failure = threading.Event(), []

        def answered(error):
            if error is not None:
                failure.append(describe(error, what))
            done.set()

        call(answered)
        if not done.wait(ANSWER_SECONDS):
            raise RuntimeError("macOS did not answer the request to share sound.")
        if failure:
            raise RuntimeError(failure[0])
