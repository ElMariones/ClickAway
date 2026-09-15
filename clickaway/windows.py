"""Windows mouse capture on a dedicated Win32 message-loop thread."""

import ctypes as C
from ctypes import wintypes as W
import queue
import socket
import threading
import time

from .geometry import Portal, Screen, clamp

user32 = C.WinDLL("user32", use_last_error=True)
kernel32 = C.WinDLL("kernel32", use_last_error=True)
LRESULT = C.c_ssize_t
HOOKPROC = C.WINFUNCTYPE(LRESULT, C.c_int, W.WPARAM, W.LPARAM)


class MouseData(C.Structure):
    _fields_ = [
        ("pt", W.POINT),
        ("mouseData", W.DWORD),
        ("flags", W.DWORD),
        ("time", W.DWORD),
        ("extra", C.c_size_t),
    ]


class MonitorInfo(C.Structure):
    _fields_ = [
        ("cbSize", W.DWORD),
        ("monitor", W.RECT),
        ("work", W.RECT),
        ("flags", W.DWORD),
    ]


user32.SetWindowsHookExW.argtypes = [C.c_int, HOOKPROC, W.HINSTANCE, W.DWORD]
user32.SetWindowsHookExW.restype = W.HANDLE
user32.CallNextHookEx.argtypes = [W.HANDLE, C.c_int, W.WPARAM, W.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [W.HANDLE]
user32.GetMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
user32.GetMessageW.restype = C.c_int
user32.DispatchMessageW.argtypes = [C.POINTER(W.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
user32.RegisterHotKey.argtypes = [W.HWND, C.c_int, W.UINT, W.UINT]
user32.GetAsyncKeyState.argtypes = [C.c_int]
user32.GetAsyncKeyState.restype = C.c_short
user32.SetCursorPos.argtypes = [C.c_int, C.c_int]
user32.SetCursor.argtypes = [W.HANDLE]
user32.SetCursor.restype = W.HANDLE
user32.LoadCursorW.argtypes = [W.HINSTANCE, C.c_void_p]
user32.LoadCursorW.restype = W.HANDLE
user32.MonitorFromPoint.argtypes = [W.POINT, W.DWORD]
user32.MonitorFromPoint.restype = W.HANDLE
kernel32.GetModuleHandleW.argtypes = [W.LPCWSTR]
kernel32.GetModuleHandleW.restype = W.HMODULE


def screens():
    result = []
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HANDLE, W.HDC, C.POINTER(W.RECT), W.LPARAM)
    user32.GetMonitorInfoW.argtypes = [W.HANDLE, C.POINTER(MonitorInfo)]
    user32.EnumDisplayMonitors.argtypes = [
        W.HDC,
        C.POINTER(W.RECT),
        callback_type,
        W.LPARAM,
    ]

    @callback_type
    def collect(handle, hdc, rect, data):
        info = MonitorInfo()
        info.cbSize = C.sizeof(info)
        if user32.GetMonitorInfoW(handle, C.byref(info)):
            r = info.monitor
            name = (
                f"Display {len(result) + 1} · {r.right - r.left} × {r.bottom - r.top}"
            )
            if info.flags & 1:
                name += " (main)"
            result.append(
                Screen(r.left, r.top, r.right - r.left, r.bottom - r.top, name)
            )
        return True

    user32.EnumDisplayMonitors(None, None, collect, 0)
    return result or [
        Screen(0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    ]


class SocketAddress(C.Structure):
    _fields_ = [("sockaddr", C.c_void_p), ("length", C.c_int)]


class UnicastAddress(C.Structure):
    pass


UnicastAddress._fields_ = [  # Leading fields of IP_ADAPTER_UNICAST_ADDRESS_LH.
    ("length", C.c_ulong),
    ("flags", W.DWORD),
    ("next", C.POINTER(UnicastAddress)),
    ("address", SocketAddress),
]


class AdapterAddresses(C.Structure):
    pass


AdapterAddresses._fields_ = [  # Leading fields of IP_ADAPTER_ADDRESSES_LH.
    ("length", C.c_ulong),
    ("index", C.c_ulong),
    ("next", C.POINTER(AdapterAddresses)),
    ("name", C.c_char_p),
    ("unicast", C.POINTER(UnicastAddress)),
    ("anycast", C.c_void_p),
    ("multicast", C.c_void_p),
    ("dns", C.c_void_p),
    ("dns_suffix", C.c_wchar_p),
    ("description", C.c_wchar_p),
    ("friendly_name", C.c_wchar_p),
    ("physical", C.c_ubyte * 8),
    ("physical_length", C.c_ulong),
    ("flags", C.c_ulong),
    ("mtu", C.c_ulong),
    ("if_type", C.c_ulong),
    ("oper_status", C.c_int),
    ("ipv6_index", C.c_ulong),
    ("zones", C.c_ulong * 16),
    ("prefix", C.c_void_p),
    ("transmit_speed", C.c_uint64),
    ("receive_speed", C.c_uint64),
    ("wins", C.c_void_p),
    ("gateway", C.c_void_p),
]


class Guid(C.Structure):
    _fields_ = [
        ("data1", C.c_ulong),
        ("data2", C.c_ushort),
        ("data3", C.c_ushort),
        ("data4", C.c_ubyte * 8),
    ]

    def __str__(self):
        tail = bytes(self.data4).hex().upper()
        return f"{{{self.data1:08X}-{self.data2:04X}-{self.data3:04X}-{tail[:4]}-{tail[4:]}}}"


class WlanInterface(C.Structure):
    _fields_ = [("guid", Guid), ("description", C.c_wchar * 256), ("state", C.c_int)]


class WlanInterfaceList(C.Structure):
    _fields_ = [
        ("count", W.DWORD),
        ("index", W.DWORD),
        ("items", WlanInterface * 1),
    ]


class Dot11Ssid(C.Structure):
    _fields_ = [("length", C.c_ulong), ("name", C.c_ubyte * 32)]


class WlanConnection(C.Structure):  # Leading fields of WLAN_CONNECTION_ATTRIBUTES.
    _fields_ = [
        ("state", C.c_int),
        ("mode", C.c_int),
        ("profile", C.c_wchar * 256),
        ("ssid", Dot11Ssid),
    ]


def networks():
    """Connected IPv4 networks as (label, address), Wi-Fi shown by network name."""
    return describe_networks(_adapters(), _wifi_names())


def describe_networks(adapters, wifi):
    ranked = []
    for adapter in adapters:
        if adapter["up"] != 1 or adapter["type"] in (24, 131):  # Loopback, tunnel.
            continue
        ssid = wifi.get(adapter["guid"].upper())
        name = f"Wi-Fi: {ssid}" if ssid else adapter["name"]
        for address in adapter["ips"]:
            if address.startswith(("127.", "169.254.")):
                continue
            # Networks with a router first (virtual switches have none), then Wi-Fi.
            rank = (
                not adapter["gateway"],
                adapter["type"] != 71,
                not ssid,
                name.lower(),
            )
            ranked.append((rank, f"{name} · {address}", address))
    return [(label, address) for _, label, address in sorted(ranked)]


def _adapters():
    api = C.WinDLL("iphlpapi")
    api.GetAdaptersAddresses.argtypes = [
        C.c_ulong,
        C.c_ulong,
        C.c_void_p,
        C.c_void_p,
        C.POINTER(C.c_ulong),
    ]
    api.GetAdaptersAddresses.restype = C.c_ulong
    size = C.c_ulong(16384)
    for _ in range(4):
        buffer = C.create_string_buffer(size.value)
        # AF_INET with GAA_FLAG_INCLUDE_GATEWAYS. ERROR_BUFFER_OVERFLOW updates size.
        result = api.GetAdaptersAddresses(2, 0x80, None, buffer, C.byref(size))
        if result == 0:
            break
        if result != 111:
            raise OSError(f"Could not list network adapters (error {result})")
    else:
        raise OSError("Could not list network adapters")
    adapters = []
    item = C.cast(buffer, C.POINTER(AdapterAddresses))
    while item:
        adapter = item.contents
        addresses = []
        unicast = adapter.unicast
        while unicast:
            raw = C.string_at(unicast.contents.address.sockaddr, 8)
            addresses.append(socket.inet_ntoa(raw[4:8]))
            unicast = unicast.contents.next
        adapters.append(
            {
                "guid": adapter.name.decode("ascii", "replace").upper(),
                "name": adapter.friendly_name or "Network",
                "type": adapter.if_type,
                "up": adapter.oper_status,
                "gateway": bool(adapter.gateway),
                "ips": addresses,
            }
        )
        item = adapter.next
    return adapters


def _wifi_names():
    """SSID of each connected Wi-Fi adapter by GUID; empty if Windows withholds it."""
    try:
        api = C.WinDLL("wlanapi")
    except OSError:
        return {}
    api.WlanOpenHandle.argtypes = [
        W.DWORD,
        C.c_void_p,
        C.POINTER(W.DWORD),
        C.POINTER(W.HANDLE),
    ]
    api.WlanEnumInterfaces.argtypes = [
        W.HANDLE,
        C.c_void_p,
        C.POINTER(C.POINTER(WlanInterfaceList)),
    ]
    api.WlanQueryInterface.argtypes = [
        W.HANDLE,
        C.POINTER(Guid),
        C.c_int,
        C.c_void_p,
        C.POINTER(W.DWORD),
        C.POINTER(C.c_void_p),
        C.c_void_p,
    ]
    api.WlanFreeMemory.argtypes = [C.c_void_p]
    api.WlanCloseHandle.argtypes = [W.HANDLE, C.c_void_p]
    handle = W.HANDLE()
    if api.WlanOpenHandle(2, None, C.byref(W.DWORD()), C.byref(handle)):
        return {}  # No WLAN service, e.g. a desktop without Wi-Fi.
    names = {}
    interfaces = C.POINTER(WlanInterfaceList)()
    try:
        if api.WlanEnumInterfaces(handle, None, C.byref(interfaces)):
            return {}
        count = interfaces.contents.count
        items = C.cast(
            C.addressof(interfaces.contents.items), C.POINTER(WlanInterface * count)
        ).contents
        for interface in items:
            if interface.state != 1:  # wlan_interface_state_connected
                continue
            data, size = C.c_void_p(), W.DWORD()
            # wlan_intf_opcode_current_connection; denied without location access.
            if api.WlanQueryInterface(
                handle,
                C.byref(interface.guid),
                7,
                None,
                C.byref(size),
                C.byref(data),
                None,
            ):
                continue
            ssid = C.cast(data, C.POINTER(WlanConnection)).contents.ssid
            length = min(ssid.length, 32)
            names[str(interface.guid)] = bytes(ssid.name[:length]).decode(
                "utf-8", "replace"
            )
            api.WlanFreeMemory(data)
    finally:
        if interfaces:
            api.WlanFreeMemory(C.cast(interfaces, C.c_void_p))
        api.WlanCloseHandle(handle, None)
    return names


class MouseController:
    def __init__(self, on_status):
        self.on_status = on_status
        self.peer = self.portal = None
        self.remote = False
        self.buttons = set()
        self.commands = queue.SimpleQueue()
        self.thread_id = None
        self.ready = threading.Event()
        self.error = None
        self.cooldown = 0.0
        self.anchor = (0, 0)
        self.last_point = None
        self._callback = HOOKPROC(self._hook)

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="clickaway-mouse").start()
        if not self.ready.wait(3):
            raise RuntimeError("Windows input capture did not start")
        if self.error:
            raise RuntimeError(self.error)

    def configure(self, peer, local, remote, side, speed):
        self._command(("configure", peer, Portal(local, remote, side, speed)))

    def release(self):
        self._command(("release",))

    def detach(self):
        self._command(("detach",))

    def stop(self):
        self._command(("stop",))

    def _command(self, command):
        self.commands.put(command)
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x8001, 0, 0)

    def _drain(self):
        while not self.commands.empty():
            cmd = self.commands.get()
            self._return()
            if cmd[0] == "configure":
                self.peer, self.portal = cmd[1:]
            elif cmd[0] == "detach":
                self.peer = self.portal = None
            elif cmd[0] == "stop":
                user32.PostQuitMessage(0)

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        # Create this thread's message queue before exposing it to other threads.
        msg = W.MSG()
        user32.PeekMessageW(C.byref(msg), None, 0, 0, 0)
        hook = user32.SetWindowsHookExW(
            14, self._callback, kernel32.GetModuleHandleW(None), 0
        )
        if not hook:
            self.error = f"Could not capture mouse: {C.WinError(C.get_last_error())}"
        elif not user32.RegisterHotKey(
            None, 1, 0x4003, 0x7B
        ):  # MOD_NOREPEAT | CTRL | ALT, F12
            self.error = "Ctrl+Alt+F12 is already in use. Close the other ClickAway instance or shortcut app."
        self.ready.set()
        if self.error:
            if hook:
                user32.UnhookWindowsHookEx(hook)
            return
        try:
            while user32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:
                    self._return()
                elif msg.message == 0x8001:
                    self._drain()
                user32.TranslateMessage(C.byref(msg))
                user32.DispatchMessageW(C.byref(msg))
        finally:
            self._return()
            user32.UnregisterHotKey(None, 1)
            user32.UnhookWindowsHookEx(hook)

    def _send(self, message):
        if self.peer and self.peer.send(message):
            return True
        self._return()
        return False

    def _return(self):
        if self.remote:
            self.remote = False
            if self.peer:
                self.peer.send({"type": "leave"})
            self.buttons.clear()
            user32.SetCursorPos(*self.portal.return_position())
            user32.SetCursor(user32.LoadCursorW(None, C.c_void_p(32512)))
            self.on_status("windows")
        self.cooldown = time.monotonic() + 0.35
        self.last_point = None

    def _hook(self, code, message, pointer):
        try:
            if code >= 0:
                data = C.cast(pointer, C.POINTER(MouseData)).contents
                if not data.flags & 1 and self._handle(message, data):
                    return 1
        except Exception:
            # Never trap a user's mouse because a callback raised an exception.
            self._return()
            self.peer = None
            self.on_status(
                "Input capture paused after an error. Reconnect to try again."
            )
        return user32.CallNextHookEx(None, code, message, pointer)

    def _handle(self, message, data):
        if not self.peer or self.peer.closed.is_set() or not self.portal:
            if self.remote:
                self._return()
            return False
        x, y = data.pt.x, data.pt.y
        if not self.remote:
            previous = self.last_point
            s = self.portal.local
            # Hooks see the attempted position before Windows clamps the cursor, so
            # pushing into the desktop's outer edge reports x = -3 while it stays at 0.
            beyond = (
                message == 0x200
                and not s.x <= x < s.x + s.width
                and not user32.MonitorFromPoint(data.pt, 0)
            )
            self.last_point = (clamp(x, s.x, s.x + s.width - 1) if beyond else x, y)
            toward = previous and (
                x < previous[0] if self.portal.side == "left" else x > previous[0]
            )
            held = any(user32.GetAsyncKeyState(key) & 0x8000 for key in (1, 2, 4, 5, 6))
            if (
                message == 0x200
                and toward
                and not held
                and time.monotonic() >= self.cooldown
                and self.portal.at_edge(x, y, beyond)
            ):
                px, py = self.portal.enter(y)
                if not self._send({"type": "enter", "x": px, "y": py}):
                    return False
                self.anchor = (round(s.x + s.width / 2), round(s.y + s.height / 2))
                self.remote = True
                if not user32.SetCursorPos(*self.anchor):
                    self._return()
                    return False
                self.on_status("mac")
                return True
            return False
        if message == 0x200:
            user32.SetCursor(None)
            dx, dy = x - self.anchor[0], y - self.anchor[1]
            if dx or dy:
                leave, px, py = self.portal.move(dx, dy, bool(self.buttons))
                if leave:
                    self._return()
                else:
                    self._send({"type": "move", "x": px, "y": py})
            return True
        buttons = {
            0x201: ("left", True),
            0x202: ("left", False),
            0x204: ("right", True),
            0x205: ("right", False),
            0x207: ("middle", True),
            0x208: ("middle", False),
        }
        if message in (0x20B, 0x20C):
            buttons[message] = (
                "back" if (data.mouseData >> 16) == 1 else "forward",
                message == 0x20B,
            )
        if message in buttons:
            button, down = buttons[message]
            self.buttons.add(button) if down else self.buttons.discard(button)
            self._send({"type": "button", "button": button, "down": down})
        elif message in (0x20A, 0x20E):
            delta = C.c_short(data.mouseData >> 16).value
            self._send(
                {
                    "type": "scroll",
                    "dx": delta if message == 0x20E else 0,
                    "dy": delta if message == 0x20A else 0,
                }
            )
        return True
