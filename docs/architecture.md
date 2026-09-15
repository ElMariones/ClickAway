# Architecture

## Roles

The Windows companion hosts a TCP listener. The Mac connects to that listener and
announces its selected display's logical size. Windows owns the portal geometry;
the Mac injects absolute Quartz coordinates offset into the selected display.

`MouseController` runs a Win32 low-level hook and global hotkey on a dedicated
message-loop thread. Hardware motion at the configured edge enters the portal.
While remote, the Windows cursor is parked in the center of the selected display,
mouse events are suppressed locally, and deltas are measured relative to that
anchor. Injected events are ignored to avoid processing the app's own cursor warp.
The hook queues messages without waiting for socket I/O. Configuration and release
requests are delivered using `PostThreadMessage`, keeping input state on one thread.

The Mac's reader dispatches events to a locked Quartz receiver. It maintains
pressed buttons and click counts, emits drag event types while a button is held,
and releases all held buttons on leave/disconnect. It uses CG display bounds in
logical coordinates rather than multiplying by a Retina backing scale.

Qt renders both apps from the same source. Native OS differences are behind the
two adapters. Worker callbacks reach the UI through queued Qt signals. Clipboard
access stays on the Qt main thread and polls at 650 ms. Incoming clipboard text
updates the local baseline so it is not echoed back.

## Connection lifecycle

1. Start sharing: create a new ephemeral ECDSA certificate and 256-bit token.
2. Windows emits a `CA1-` code containing IPv4 address, TCP port, certificate SHA-256
   fingerprint and token. The user transfers it privately to the Mac.
3. Mac opens TLS and verifies the exact certificate fingerprint before sending
   the token. CA/hostname checks are replaced by this explicit certificate pin.
4. Windows checks the token in constant time and accepts one peer. Failed clients
   cannot inject events, change the clipboard or evict an existing paired session.
5. Each side runs one reader and one writer. Idle writers send a heartbeat every
   500 ms. Receive/liveness deadlines close a stalled session after roughly 3 seconds.
6. Closing the session returns input to Windows and releases Mac buttons. Stopping
   the listener invalidates the entire pairing identity. Nothing auto-connects.

Certificates are loaded from a temporary directory and the temporary key files are
removed immediately after loading into the TLS context. Pairing secrets are not
stored in settings or logged. The code itself is a credential; anyone with it can
pair while the session is listening. Generate a fresh session to revoke it.

## Wire protocol, version 1

TLS carries ordered frames: 4-byte big-endian length followed by UTF-8 JSON. Frames
are limited to 400 KiB (allowing escaped control characters in a 64 KiB clipboard).
Schema validation rejects unknown event types, non-finite coordinates, bad button
states, invalid dimensions and oversized text before native event handling.

| Message | Direction | Fields |
| --- | --- | --- |
| `hello` | Mac → Windows | `version`, `token`, `width`, `height` |
| `ready` | Windows → Mac | `version` |
| `layout` | Windows → Mac | `side`, `speed` |
| `enter`, `move` | Windows → Mac | `x`, `y` relative to Mac display |
| `button` | Windows → Mac | `button`, `down` |
| `scroll` | Windows → Mac | `dx`, `dy`, Windows wheel units |
| `leave` | Windows → Mac | — |
| `clipboard` | Both | `text`, UTF-8 byte limit 65,536 |
| `ping` | Both | — |

Outbound queues are bounded at 1,024 messages. Congestion disconnects the peer
rather than accumulating an unbounded input delay. TCP preserves click/motion
ordering, but Wi-Fi loss can cause head-of-line latency. v0.1 prioritizes a simple,
auditable encrypted transport over a custom reliable UDP protocol.

## Persisted data

Only side, speed and clipboard preference are saved in an atomic JSON replacement:

- Windows: `%LOCALAPPDATA%/ClickAway/settings.json`
- Mac: `~/Library/Application Support/ClickAway/settings.json`

There is no saved connection history, content history, password, token, certificate,
analytics endpoint or cloud account.
