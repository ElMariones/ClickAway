<p align="center"><img src="clickaway/assets/logo.svg" width="80" alt="ClickAway logo"></p>

# ClickAway

**One mouse, two happy computers.**

Move your Windows mouse across the screen edge to use the Mac beside it. Move
back to return to Windows. Copy text on either computer and paste it on the other.
ClickAway connects directly over your Wi-Fi or Ethernet network.

Built for a Windows 11 PC and an Apple Silicon Mac, with a cozy blue-and-yellow
interface, Outfit typography, animated desk arrangements and hover effects.

![ClickAway Windows companion](docs/images/windows.png)

## What it does

- Windows → Mac mouse movement, left/right/middle clicks, double-clicks, dragging,
  wheel scrolling and extra mouse buttons (app support for extra buttons varies).
- Mac on the left by default; change it to the right in Windows at any time.
- Choose the Windows display with the shared edge and the Mac display to control.
- Adjust pointer speed for your screen sizes and Retina scaling.
- Bidirectional **plain-text clipboard** sync, up to 64 KiB per copy; disable it
  independently on either computer. Only new copies made after connecting sync.
- **Ctrl + Alt + F12** on the Windows keyboard returns control immediately.
- Certificate-pinned TLS and a random 256-bit connection key. No account, relay,
  cloud service, telemetry, or Internet connection is needed after installation.
- On disconnect, held Mac buttons are released and Windows input is restored.

## Download

Download both apps from the [latest GitHub release](https://github.com/ElMariones/ClickAway/releases/latest):

- **Windows 11 (x64):** `ClickAway-Windows-x64.zip`. Unzip it and open
  `ClickAway/ClickAway.exe`. Keep the `_internal` folder beside the executable.
- **Mac with Apple Silicon (M1 or newer):** `ClickAway-macOS-AppleSilicon.zip`.
  Unzip it and move `ClickAway.app` into Applications. It runs natively on arm64.

`SHA256SUMS.txt` on the release page lets you verify both downloads.

These are development builds. The Windows executable is unsigned, so SmartScreen
may ask you to choose **More info → Run anyway**. The Mac app is ad-hoc signed and
**not Apple-notarized**, so macOS blocks the first launch. Open System Settings →
Privacy & Security, click **Open Anyway** next to ClickAway and confirm.

Builds of every commit to `main` are also kept for 30 days as
[GitHub Actions artifacts](https://github.com/ElMariones/ClickAway/actions/workflows/build.yml)
(zipped once more by GitHub). You can also build from source using the instructions below.

## Setup

### 1. On Windows

1. Connect both computers to the same Wi-Fi network and open ClickAway.
2. Choose **Mac on the left** or **Mac on the right** to match your desk.
3. Select your Windows monitor. Its shared edge should face the Mac.
4. Select your PC's Wi-Fi IPv4 address from the list. If you have several network
   adapters, use the address shown under Windows Settings → Network & internet →
   Wi-Fi → your network's properties. You can enter it manually.
5. Click **Start sharing**. If Windows Firewall asks, allow ClickAway on your
   private/home network. It listens on **TCP port 49624**.
6. Click **Copy connection code**. Transfer this code privately to your Mac, for
   example through a note you can already open on both computers. The initial
   pairing cannot use ClickAway's clipboard because the apps are not connected yet.

### 2. On your Mac

1. Open ClickAway from Applications.
2. Click **Allow mouse control**. In System Settings → Privacy & Security →
   Accessibility, enable **ClickAway**. If you run from source, macOS may name the
   Python interpreter or Terminal instead; the packaged app has a stable identity.
3. If macOS asks for Local Network access, allow it.
4. Select the Mac display to control and paste the complete `CA1-` connection code.
5. Click **Connect to Windows**. Both apps should show **Connected**.

![ClickAway Mac companion](docs/images/mac.png)

### 3. Enjoy your desk

Move to the shared Windows screen edge. Your mouse now controls the Mac. To return,
cross the Mac's corresponding edge. Crossings preserve your relative screen height.
Use **Ctrl + Alt + F12** on Windows whenever you need to get back immediately.

Copy plain text normally on either machine, then paste using that machine's
keyboard or a context menu. Keyboard input itself stays on its original computer.
The apps may be minimized while sharing; closing either app ends the connection.

Stopping sharing invalidates the connection code. After a restart, generate a new
one. After a temporary connection loss, click Copy connection code on Windows and
connect again on the Mac. ClickAway deliberately requires a fresh connection action
instead of unexpectedly taking over your mouse after a network interruption.

## Troubleshooting

| What you see | What to check |
| --- | --- |
| Connection times out | Same LAN, correct Wi-Fi IP, and Windows Firewall allowing TCP 49624. Guest Wi-Fi/client isolation and some VPNs block direct device communication. |
| Identity changed | Stop sharing and generate a new code on Windows. Don't edit a code to bypass the identity check. |
| Connected but the Mac does not click | Enable Accessibility for the exact ClickAway app you are running. After replacing an unsigned build, remove the old Accessibility entry and add the new app if necessary. |
| Mouse doesn't cross | Choose the correct Windows monitor and edge; release held mouse buttons and move a little inward before crossing again. |
| Movement feels too fast/slow | Adjust Pointer speed on Windows; physical PC pixels and Mac display points may differ. |
| Clipboard doesn't sync | Enable it on both computers, copy new plain text after connecting, and keep it under 64 KiB. Images and files aren't supported. |
| Displays changed | Reconnect after changing display resolution, arrangement, or plugging in a monitor. The app pauses sharing when it detects a display change. |
| Shortcut cannot register | Close another ClickAway instance or an app using Ctrl+Alt+F12, then start sharing again. |

## Current scope and validation

This is **v0.1**, a first working implementation intended for a two-computer desk.
The target setup is Windows 11 25H2 and macOS Tahoe on an Apple Silicon Mac.
Windows input-hook installation, screen enumeration, protocol integration and
automated tests can be verified on the development PC. Full physical mouse
crossing, Mac permissions, and behavior on the user's exact Tahoe build require
hands-on testing on that Mac; a successful build alone does not prove these.

- One Windows host and one paired Mac per session; IPv4 local networking.
- One selected display per computer for crossings; left/right layouts only.
- No keyboard, rich-text/image/file clipboard, file dragging between computers,
  Bluetooth transport, Internet relay, automatic discovery, or automatic startup.
- A held-button drag stays on its current computer; release before crossing.
- The Windows cursor is parked while controlling the Mac; hiding it is best
  effort because individual Windows apps can reset their cursor shape.
- Login screens, UAC/secure desktops, games using raw/exclusive input, and protected
  macOS surfaces are not supported. Stop sharing before locking either computer.
- Clipboard changes within the 650 ms polling interval can coalesce. If both
  computers copy simultaneously, arrival order wins; recopy the desired text.
- No independent security audit or latency benchmark has been performed.

## Run from source

Use Python 3.12 or newer. Build each app on its own operating system.

Windows (PowerShell):

```powershell
git clone https://github.com/ElMariones/ClickAway.git
cd ClickAway
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[build]"
.\.venv\Scripts\python.exe -m clickaway
```

Mac (Terminal, native Apple Silicon Python):

```sh
git clone https://github.com/ElMariones/ClickAway.git
cd ClickAway
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[build]'
.venv/bin/python -m clickaway
```

Build the distributable archive with `python scripts/build.py` from that virtual
environment. It appears under `dist/`. The GitHub workflow builds Windows x64 and
macOS arm64 separately; pushing a version tag such as `v0.1.0` also publishes both
archives as a GitHub release. Shipping a notarized Mac build requires an Apple Developer
signing identity and notarization credentials, which are not stored in this repo.

## Development

```sh
python -m unittest discover -s tests -v
python -m clickaway --preview
python -m clickaway --preview-mac
python scripts/render_preview.py
```

The preview commands cannot capture the mouse or open a network listener. The
render script draws the actual Qt widgets to `docs/images/` for visual review.

See [Architecture](docs/architecture.md), [Manual acceptance checks](docs/testing.md)
and [third-party notices](THIRD_PARTY_NOTICES.md).

### API references

The native adapters use [Windows low-level mouse hooks](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelmouseproc)
and [Apple Quartz mouse events](https://developer.apple.com/documentation/coregraphics/cgevent/init(mouseeventsource:mousetype:mousecursorposition:mousebutton:)).
The interface uses Qt and the [Outfit typeface](https://github.com/google/fonts/tree/main/ofl/outfit).
