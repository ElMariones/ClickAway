# Verification

## Automated

Run `python -m unittest discover -s tests -v` in the installed virtual environment.

The suite covers:

- Real TLS sockets with bidirectional traffic, reconnects, fragmented frames, Unicode
  clipboard limits, unresponsive peers and queue overflow.
- Password pairing: the SPAKE2 group constants (re-derived from RFC 3526), matching
  and mismatched passwords, a relay presenting a different certificate, invalid
  group elements, password rules, and sharing stopping after repeated wrong passwords.
- UDP discovery of a sharing host.
- Sound: float, surround and channel-plane packets converted to 16-bit stereo
  without wrapping around at full scale, the jitter buffer filling, refilling and
  dropping what fell behind, binary sound frames over a fragmented socket and over a
  real paired connection, rejected sound settings and frame sizes, mouse messages
  overtaking queued sound, and the Mac following the direction Windows chose or
  refusing it.
- The Mac's ScreenCaptureKit capture is tested against stand-ins for the macOS
  frameworks: reading each sample-buffer format it can be handed, announcing the
  format once, chunking, and reporting a failure once. Only the arithmetic is
  covered this way; that macOS actually delivers sound is check 13 below.
- Portal geometry, drag boundaries, crossing past the desktop edge, Win32 and Quartz
  adapter semantics, network naming, clipboard opt-out and echo prevention, and
  stale connection callbacks after cancellation.

Native adapter tests use mocks; they do not establish that an OS accepted injected input.

## Physical Windows/Mac acceptance checks

These require both computers, an actual mouse, the installed apps and user-granted
macOS permissions. They are **not claimed complete** by CI.

1. Start sharing on Windows with a password. Confirm the network list shows the
   Wi-Fi name. On the Mac, type the password and connect without an IP address.
   Confirm both statuses show Connected.
2. With the Mac on the left, cross near the top, middle and bottom. Confirm the Mac
   pointer lands at the same relative height and clicks the expected item.
3. Return across the Mac's right edge. Repeat with the Mac configured on the right.
4. Test left/right/middle click, double-click, vertical/horizontal wheel scrolling,
   text selection, dragging windows and extra mouse buttons in supported apps.
5. Hold a drag against the return edge. Confirm it stays on the Mac until release.
6. Press Ctrl+Alt+F12 while the pointer is remote, including during a held drag.
   Confirm Windows becomes usable and the Mac does not retain a held button.
7. Copy Unicode and multiline text in each direction. Confirm disabling sync on
   either side blocks it. Confirm images, files and oversized text remain local.
8. Disconnect Wi-Fi while the mouse is remote and while a button is held. Confirm
   recovery in roughly three seconds. Connect again from the Mac.
9. Type a wrong password on the Mac and confirm it says so. Stop sharing and
   confirm the Mac can no longer connect. Start again with a different password.
10. Block UDP discovery (for example with a network that isolates clients) and
    confirm the Mac offers the IP address field and connects with it.
11. Remove Accessibility permission; confirm the session ends and reconnecting
    explains the required permission. Re-enable it and verify mouse injection.
12. Test display scaling, monitor selection, resolution changes and a sleep/wake
    cycle. Changes should end sharing and require reconnecting.
13. Set **Which way sound travels** to **This PC → Mac** and play a game on Windows.
    Confirm the Mac plays it mixed with a video playing on the Mac, that the volume
    slider and each delay choice work, and that turning Windows' own volume to zero
    silences the PC without quieting the Mac. Switch the PC's output device while
    connected and confirm sound returns on its own. Clear **Allow sound sharing on
    this Mac** and confirm Windows stops sending. Confirm the mouse stays responsive
    while sound is streaming, including on Wi-Fi.
14. Set the direction to **Mac → this PC**. Confirm the Mac asks for Screen & System
    Audio Recording, that granting it and reopening ClickAway makes the Mac send, and
    that a video on the Mac is heard on Windows alongside a game on Windows. Confirm
    ClickAway's own playback is not captured back, so nothing echoes. Confirm the
    permission button disappears once granted, and that revoking the permission gives
    a message rather than silence.
15. Verify both installers on a clean computer without Python installed. On Windows,
    `ClickAway-Setup-x64.exe` should install without an administrator prompt, leave a
    working Start Menu shortcut, and remove itself from Installed apps. On the Mac,
    the disk image should open a window showing ClickAway beside the Applications
    folder, and dragging it across should install a version that launches from
    Applications. Confirm the Mac build is arm64 and record OS/build versions with
    any issues.
16. Confirm Accessibility permission survives quitting and reopening the app once it
    has been installed into `/Applications` from the disk image.

When reporting an issue, include OS versions, app version, chosen side, display
sizes/scaling, connection status and exact reproduction steps. Do not include
your password or private clipboard contents.
