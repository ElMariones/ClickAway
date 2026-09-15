# Verification

## Automated

Run `python -m unittest discover -s tests -v` in the installed virtual environment.

The suite covers actual TLS sockets with bidirectional traffic, wrong fingerprint
and wrong secret rejection, reconnects, fragmented frames, Unicode clipboard limits,
unresponsive peers, queue overflow, portal geometry, drag boundaries, Win32 adapter
semantics, Quartz adapter semantics, clipboard opt-out/echo prevention, and stale
connection callbacks after cancellation. Native adapter behavioral tests use mocks;
they do not establish that an OS accepted injected input.

## Physical Windows/Mac acceptance checks

These require both computers, an actual mouse, the installed apps and user-granted
macOS permissions. They are **not claimed complete** by CI.

1. Pair over the same Wi-Fi using a newly generated code. Confirm both statuses.
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
   recovery in roughly three seconds. Reconnect explicitly and try again.
9. Stop sharing; confirm the old code fails. Start again and use a new code.
10. Remove Accessibility permission; confirm the session ends and reconnecting
    explains the required permission. Re-enable it and verify mouse injection.
11. Test display scaling, monitor selection, resolution changes and a sleep/wake
    cycle. Changes should end sharing and require reconnecting.
12. Verify both zipped distributions on a clean computer without Python installed.
    Confirm the Mac build is arm64 and record OS/build versions with any issues.

When reporting an issue, include OS versions, app version, chosen side, display
sizes/scaling, connection status and exact reproduction steps. Do not include
the connection code or private clipboard contents.
