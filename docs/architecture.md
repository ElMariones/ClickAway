# Architecture

## Roles

The Windows companion hosts a TCP listener and answers discovery broadcasts. The
Mac finds it, pairs using the shared password and announces its selected display's
logical size. Windows owns the portal geometry; the Mac injects absolute Quartz
coordinates offset into the selected display.

`MouseController` runs a Win32 low-level hook and global hotkey on a dedicated
message-loop thread. Hardware motion at the configured edge enters the portal.
The hook reports the attempted pointer position before Windows clamps it, so a push
past an edge with no other display behind it also counts as reaching the edge.
While remote, the Windows cursor is parked in the center of the selected display,
mouse events are suppressed locally, and deltas are measured relative to that
anchor. Injected events are ignored to avoid processing the app's own cursor warp.
The hook queues messages without waiting for socket I/O. Configuration and release
requests are delivered using `PostThreadMessage`, keeping input state on one thread.

The Mac's reader dispatches events to a locked Quartz receiver. It maintains
pressed buttons and click counts, emits drag event types while a button is held,
and releases all held buttons on leave/disconnect. It uses CG display bounds in
logical coordinates rather than multiplying by a Retina backing scale.

Windows sound is captured with WASAPI loopback (`clickaway/wasapi.py`): a second
copy of whatever the PC's default output device is already playing, taken before the
endpoint volume, so turning Windows down does not quiet the Mac. COM lives entirely
on one dedicated thread because interface pointers belong to the apartment that
created them. The audio engine is asked for 16-bit stereo at its own mix rate using
`AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM`, which downmixes in C; if a build refuses that,
the engine's own float format is taken and converted in `clickaway/audio.py`. The
thread reopens the device by itself when the default output changes, and tells the
Mac the new format. Packets are grouped into 20 ms chunks and handed to the peer.

The Mac plays them with `QAudioSink` in push mode. A jitter buffer holds the chosen
delay before playback starts and hands bytes to the sink from the Qt thread; the
network thread only appends to it. Sound that arrives later than the buffer's limit
allows is dropped rather than played late, and an emptied buffer refills before it
plays again instead of crackling. The Windows app owns the volume and delay; the Mac
mirrors them and can refuse the sound entirely, which tells Windows to stop sending.

Qt renders both apps from the same source. Native OS differences are behind the
two adapters. Worker callbacks reach the UI through queued Qt signals. Clipboard
access stays on the Qt main thread and polls at 650 ms. Incoming clipboard text
updates the local baseline so it is not echoed back.

The Windows network list comes from `GetAdaptersAddresses`. Connected Wi-Fi adapters
are labeled with their SSID from `WlanQueryInterface`; if Windows withholds the SSID
(for example when location access is off), the adapter name is shown instead.

## Connection lifecycle

1. **Start sharing.** Windows takes a password of 6 to 64 characters (trimmed and
   NFC-normalized), creates an ephemeral ECDSA certificate, and binds TCP and UDP
   port 49624 on the chosen network's IPv4 address.
2. **Discovery.** The Mac broadcasts `{"clickaway": "discover"}` to UDP 49624 on
   255.255.255.255 for up to 2 seconds. Windows replies with its TCP port, protocol
   version and computer name. If nothing answers, the user can type the PC's IP address.
3. **TLS.** The Mac opens TLS without CA or hostname checks and records the SHA-256
   digest of the certificate it received.
4. **Password exchange.** Both sides run SPAKE2 (`clickaway/pake.py`) inside TLS:
   - Group: the RFC 3526 2048-bit MODP safe prime, using its prime-order subgroup
     of squares. `M` and `N` are SHAKE-256 outputs squared into that subgroup, so
     nobody knows their discrete logarithms. `w` is SHA-512 of the password modulo
     the group order.
   - `hello` carries the Mac's element, `verify` carries Windows' element and proof,
     and `confirm` carries the Mac's proof and display size. Received elements must
     lie in the subgroup.
   - The key is SHA-256 over a length-prefixed transcript of the certificate digest,
     both elements, the shared element and `w`. Proofs are HMAC-SHA256 of that key
     with a per-role label, compared in constant time.
5. **Accept.** Windows sends `ready` and accepts one peer. Clients that fail cannot
   inject events, change the clipboard or evict an existing session.
6. **Session.** Each side runs one reader and one writer. Idle writers send a heartbeat
   every 500 ms. Receive/liveness deadlines close a stalled session after about 3 seconds.
7. **End.** Closing the session returns input to Windows and releases Mac buttons.
   Stopping sharing discards the certificate and password. Nothing auto-connects.

### What the password exchange protects

- The password never crosses the network, and a recorded exchange gives nothing to
  test password guesses against offline.
- Each connection attempt tests exactly one guess. Windows counts every exchange it
  starts as a failure until the Mac proves the password, and stops sharing after 10.
- A relay that terminates TLS with its own certificate changes the digest the Mac
  sees, so confirmation fails on both sides without revealing the password.
- A short password still limits security to those online guesses; pick something
  that isn't trivially guessable.

Certificates are loaded from a temporary directory and the key files are removed
immediately after loading into the TLS context. The password is held in memory only
while sharing and is never stored or logged.

## Wire protocol, version 3

TLS carries ordered frames: 4-byte big-endian length followed by UTF-8 JSON. Frames
are limited to 400 KiB (allowing escaped control characters in a 64 KiB clipboard).
The top bit of the length word marks a sound frame instead: up to 32 KiB of raw
interleaved little-endian 16-bit samples, which skip JSON entirely.
Schema validation rejects unknown event types, malformed keys and proofs, non-finite
coordinates, bad button states, invalid dimensions and oversized text before native
event handling.

| Message | Direction | Fields |
| --- | --- | --- |
| `hello` | Mac → Windows | `version`, `key` (SPAKE2 element, 512 hex digits) |
| `verify` | Windows → Mac | `version`, `key`, `proof` (64 hex digits) |
| `confirm` | Mac → Windows | `proof`, `width`, `height` |
| `ready` | Windows → Mac | `version` |
| `layout` | Windows → Mac | `side`, `speed` |
| `enter`, `move` | Windows → Mac | `x`, `y` relative to Mac display |
| `button` | Windows → Mac | `button`, `down` |
| `scroll` | Windows → Mac | `dx`, `dy`, Windows wheel units |
| `leave` | Windows → Mac | — |
| `clipboard` | Both | `text`, UTF-8 byte limit 65,536 |
| `sound` | Windows → Mac | `playing`, `volume` 0–1, `rate`, `channels`, `latency` ms |
| `sound` | Mac → Windows | `playing`, telling Windows whether to send sound at all |
| sound frame | Windows → Mac | raw samples, length word tagged with the top bit |
| `ping` | Both | — |

Discovery uses single UDP datagrams of JSON outside TLS: the probe
`{"clickaway": "discover", "version": 3}` and the reply
`{"clickaway": "here", "version": 3, "port": 49624, "name": "<computer name>"}`.
A reply only says that a ClickAway host is sharing; pairing still requires the password.

Outbound queues are bounded at 1,024 messages. Congestion disconnects the peer
rather than accumulating an unbounded input delay. Sound has its own queue of 25
chunks, about half a second, and the writer empties every waiting mouse and
clipboard message before it sends any of it. A full sound queue drops its oldest
chunk instead of the connection, because late sound is worthless while a late click
is not. TCP preserves click/motion
ordering, but Wi-Fi loss can cause head-of-line latency. ClickAway prioritizes a
simple, auditable encrypted transport over a custom reliable UDP protocol.

## Persisted data

Only side, speed, clipboard preference, sound preference, volume and delay are saved
in an atomic JSON replacement:

- Windows: `%LOCALAPPDATA%/ClickAway/settings.json`
- Mac: `~/Library/Application Support/ClickAway/settings.json`

There is no saved connection history, content history, password, certificate,
analytics endpoint or cloud account.
