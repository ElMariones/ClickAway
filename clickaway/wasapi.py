"""Windows loopback capture: a copy of whatever the PC is already playing.

Everything here runs on one dedicated thread, because COM interface pointers
belong to the apartment that created them.
"""

import ctypes as C
from ctypes import wintypes as W
import threading
import time

from .audio import CHUNK_MS, to_int16

ole32 = C.WinDLL("ole32")

CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioClient = "{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}"
IID_IAudioCaptureClient = "{C8ADBD64-E71E-48A0-A4DE-185C395CD317}"
SUBTYPE_PCM = "{00000001-0000-0010-8000-00AA00389B71}"
SUBTYPE_FLOAT = "{00000003-0000-0010-8000-00AA00389B71}"

RENDER, CONSOLE = 0, 0
SHARED = 0
LOOPBACK = 0x00020000
AUTOCONVERT = 0x80000000  # Let the audio engine hand us plain 16-bit stereo,
SRC_QUALITY = 0x08000000  # resampling and downmixing in C instead of Python.
SILENT = 0x2
BUFFER_HNS = 2_000_000  # 200 ms of capture buffer, in 100-nanosecond units.
WAVE_PCM, WAVE_FLOAT, WAVE_EXTENSIBLE = 1, 3, 0xFFFE
DEVICE_INVALIDATED = 0x88890004


class GUID(C.Structure):
    _fields_ = [
        ("data1", C.c_ulong),
        ("data2", C.c_ushort),
        ("data3", C.c_ushort),
        ("data4", C.c_ubyte * 8),
    ]

    def __init__(self, text=None):
        super().__init__()
        if text:
            ole32.CLSIDFromString(C.c_wchar_p(text), C.byref(self))

    def __eq__(self, other):
        return bytes(memoryview(self)) == bytes(memoryview(other))


class WaveFormat(C.Structure):
    # WAVEFORMATEX is 18 bytes with no padding, and WAVEFORMATEXTENSIBLE follows it.
    _pack_ = 1
    _fields_ = [
        ("tag", W.WORD),
        ("channels", W.WORD),
        ("rate", W.DWORD),
        ("bytes_per_second", W.DWORD),
        ("block_align", W.WORD),
        ("bits", W.WORD),
        ("extra", W.WORD),
    ]


class WaveFormatExtensible(C.Structure):
    _pack_ = 1
    _fields_ = [
        ("format", WaveFormat),
        ("samples", W.WORD),
        ("channel_mask", W.DWORD),
        ("subformat", GUID),
    ]


ole32.CoCreateInstance.argtypes = [
    C.POINTER(GUID),
    C.c_void_p,
    C.c_ulong,
    C.POINTER(GUID),
    C.POINTER(C.c_void_p),
]
ole32.CoCreateInstance.restype = C.HRESULT
ole32.CoInitializeEx.argtypes = [C.c_void_p, C.c_ulong]
ole32.CoInitializeEx.restype = C.HRESULT
ole32.CoTaskMemFree.argtypes = [C.c_void_p]
ole32.CLSIDFromString.argtypes = [C.c_wchar_p, C.POINTER(GUID)]


def call(interface, slot, *arguments):
    """Invoke one method of a COM interface by its position in the vtable."""
    table = C.cast(interface, C.POINTER(C.c_void_p)).contents.value
    address = C.cast(table, C.POINTER(C.c_void_p))[slot]
    types = [type(argument) for argument in arguments]
    function = C.WINFUNCTYPE(C.HRESULT, C.c_void_p, *types)(address)
    return function(interface, *arguments)


def release(interface):
    if interface:
        call(interface, 2)  # IUnknown::Release


def default_speakers():
    enumerator = C.c_void_p()
    ole32.CoCreateInstance(
        C.byref(GUID(CLSID_MMDeviceEnumerator)),
        None,
        23,  # CLSCTX_ALL
        C.byref(GUID(IID_IMMDeviceEnumerator)),
        C.byref(enumerator),
    )
    device = C.c_void_p()
    try:
        call(
            enumerator,
            4,  # IMMDeviceEnumerator::GetDefaultAudioEndpoint
            C.c_int(RENDER),
            C.c_int(CONSOLE),
            C.pointer(device),
        )
    finally:
        release(enumerator)
    return device


def describe(pointer):
    """Sample rate, channel count and whether the packets hold 32-bit floats."""
    header = C.cast(pointer, C.POINTER(WaveFormat)).contents
    tag = header.tag
    if tag == WAVE_EXTENSIBLE:
        extended = C.cast(pointer, C.POINTER(WaveFormatExtensible)).contents
        if extended.subformat == GUID(SUBTYPE_FLOAT):
            tag = WAVE_FLOAT
        elif extended.subformat == GUID(SUBTYPE_PCM):
            tag = WAVE_PCM
    if tag not in (WAVE_PCM, WAVE_FLOAT) or header.bits != (
        32 if tag == WAVE_FLOAT else 16
    ):
        raise RuntimeError(
            "Windows is playing sound in a format ClickAway cannot read."
        )
    return header.rate, header.channels, tag == WAVE_FLOAT


class Stream:
    """One open loopback capture: the client, its packets and its format."""

    def __init__(self):
        self.device = self.client = self.capture = self.mix = None
        self.rate = self.channels = self.frame = 0
        self.floating = False

    def open(self):
        self.device = default_speakers()
        client = C.c_void_p()
        call(
            self.device,
            3,  # IMMDevice::Activate
            C.pointer(GUID(IID_IAudioClient)),
            C.c_ulong(23),
            C.c_void_p(),
            C.pointer(client),
        )
        self.client = client
        mix = C.c_void_p()
        call(self.client, 8, C.pointer(mix))  # IAudioClient::GetMixFormat
        self.mix = mix
        rate, channels, _ = describe(mix)
        self.rate, self.channels, self.floating = rate, min(channels, 2), False
        wanted = WaveFormat(
            WAVE_PCM,
            self.channels,
            rate,
            rate * self.channels * 2,
            self.channels * 2,
            16,
            0,
        )
        try:
            self._initialize(C.pointer(wanted), LOOPBACK | AUTOCONVERT | SRC_QUALITY)
        except OSError:
            # Older builds refuse the conversion; take the engine's own format.
            self.rate, self.channels, self.floating = describe(mix)
            self._initialize(mix, LOOPBACK)
        self.frame = self.channels * (4 if self.floating else 2)
        capture = C.c_void_p()
        call(
            self.client,
            14,  # IAudioClient::GetService
            C.pointer(GUID(IID_IAudioCaptureClient)),
            C.pointer(capture),
        )
        self.capture = capture
        call(self.client, 10)  # IAudioClient::Start
        return self.rate, min(self.channels, 2)

    def _initialize(self, format_pointer, flags):
        call(
            self.client,
            3,  # IAudioClient::Initialize
            C.c_int(SHARED),
            C.c_ulong(flags),
            C.c_longlong(BUFFER_HNS),
            C.c_longlong(0),
            format_pointer,
            C.c_void_p(),
        )

    def read(self):
        """Every packet waiting now, already converted to 16-bit samples."""
        blocks = []
        available = C.c_uint32()
        call(self.capture, 5, C.pointer(available))  # GetNextPacketSize
        while available.value:
            data = C.c_void_p()
            frames = C.c_uint32()
            flags = C.c_ulong()
            call(
                self.capture,
                3,  # IAudioCaptureClient::GetBuffer
                C.pointer(data),
                C.pointer(frames),
                C.pointer(flags),
                C.c_void_p(),
                C.c_void_p(),
            )
            try:
                count = frames.value * self.frame
                if flags.value & SILENT or not data:
                    # The buffer holds anything at all when Windows flags silence.
                    blocks.append(bytes(frames.value * min(self.channels, 2) * 2))
                elif count:
                    blocks.append(
                        to_int16(C.string_at(data, count), self.channels, self.floating)
                    )
            finally:
                call(self.capture, 4, C.c_uint32(frames.value))  # ReleaseBuffer
            call(self.capture, 5, C.pointer(available))
        return b"".join(blocks)

    def close(self):
        if self.client:
            try:
                call(self.client, 11)  # IAudioClient::Stop
            except OSError:
                pass
        release(self.capture)
        release(self.client)
        release(self.device)
        if self.mix:
            ole32.CoTaskMemFree(self.mix)
        self.__init__()


class LoopbackCapture:
    """Streams the PC's own sound to ``on_chunk`` until ``stop`` is called."""

    def __init__(self, on_chunk, on_format=None, on_error=None):
        self.on_chunk = on_chunk
        self.on_format = on_format or (lambda rate, channels: None)
        self.on_error = on_error or (lambda text: None)
        self.running = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.rate, self.channels = 0, 0

    def start(self, timeout=5):
        """Open the speakers now, so the Mac can be told the format right away."""
        self.running.set()
        threading.Thread(target=self._run, daemon=True, name="clickaway-sound").start()
        if not self.ready.wait(timeout):
            self.stop()
            raise RuntimeError("Windows sound capture did not start.")
        if self.error:
            self.stop()
            raise RuntimeError(self.error)
        return self.rate, self.channels

    def stop(self):
        self.running.clear()

    def _run(self):
        ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        first = True
        while self.running.is_set():
            stream = Stream()
            try:
                self.rate, self.channels = stream.open()
                self.error = None
                if not first:
                    self.on_format(self.rate, self.channels)
                self.ready.set()
                first = False
                self._pump(stream)
            except Exception as exc:
                self._failed(first, self._reason(exc))
                first = False
            finally:
                stream.close()

    def _pump(self, stream):
        pending = bytearray()
        chunk = stream.rate * min(stream.channels, 2) * 2 * CHUNK_MS // 1000
        while self.running.is_set():
            pending += stream.read()
            while len(pending) >= chunk:
                self.on_chunk(bytes(pending[:chunk]))
                del pending[:chunk]
            time.sleep(CHUNK_MS / 2000)

    def _failed(self, first, reason):
        self.error = reason
        if first:
            self.ready.set()
            self.running.clear()
            return
        self.on_error(reason)
        # A changed or unplugged output device comes back on its own; wait for it.
        for _ in range(20):
            if not self.running.is_set():
                return
            time.sleep(0.1)

    @staticmethod
    def _reason(exc):
        if getattr(exc, "winerror", 0) & 0xFFFFFFFF == DEVICE_INVALIDATED:
            return "The Windows sound device changed. Reconnecting to it."
        return f"Windows would not share its sound: {exc}"
