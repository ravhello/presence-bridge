---
title: Setup tutorial
permalink: /tutorial/
---

# Presence Bridge setup tutorial

## 1. Check the requirements

You need Home Assistant 2025.1 or newer, an MQTT broker already connected to
Home Assistant, an always-on Windows 10/11 computer with Bluetooth LE, and an
iPhone running iOS 17 or newer. Keep each observer computer in a fixed place.
The adapter must support both BLE central connections and peripheral
advertising. During enrollment the configured Windows user must be signed in;
a locked session is sufficient. Passive observation runs as SYSTEM before
login. Use a Windows version receiving security updates.

## 2. Install the free integration

The current release is the **0.1.36 public preview**. Presence Pair is still in
TestFlight, not yet on the public App Store. You need access to that app for
initial enrollment. This repository can be installed through HACS as a custom
repository; it is not a default catalog listing.

1. In HACS, open **Integrations**.
2. From the three-dot menu, select **Custom repositories**.
3. Enter `https://github.com/ravhello/presence-bridge`, choose
   **Integration**, and confirm.
4. Enable pre-releases for this repository, select **v0.1.36**, install
   **Presence Bridge**, and restart Home Assistant. HACS normally excludes betas;
   see its [pre-release switch documentation](https://www.hacs.xyz/docs/use/entities/switch/).
5. Open **Settings > Devices & services > Add integration**, search for
   **Presence Bridge**, and complete setup.

Alternatively, extract the release's `presence_bridge-0.1.36.zip` into
`<HA config>/custom_components/presence_bridge/`. Its `manifest.json` must sit
directly in that folder. Back up HA before updating; then restart and add the
integration as above. Use the matching 0.1.36 Windows receiver package.

## 3. Prepare the Windows observer

Download and extract `presence-bridge-windows-VERSION.zip` from the matching
release. On the fixed Windows computer, open PowerShell **as Administrator** in
the extracted folder containing `install.ps1` and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

Enter a stable ID such as `living_room_pc`, a readable name, and credentials
for a dedicated MQTT user. The installer asks for the password interactively,
so it does not enter shell history. At the end, the scheduled task must report
`Running`.

When the observer appears in the Presence Bridge panel, assign it to the Home
Assistant area where it is physically installed. Room estimates use the fixed
observer that receives the strongest fresh iPhone signal.

## 4. Pair a person

Open **Presence Bridge** in the Home Assistant sidebar, select a person and the
nearest observer, then choose **Create code**. Open Presence Pair on the iPhone,
scan the QR code; the current compatibility release needs no purchase. The iPhone then
becomes temporarily visible to the selected receiver, which connects and
accepts automatically. If iOS asks, allow Presence Pair to use Bluetooth or tap
Pair, then keep the app open until the green confirmation appears.
There is nothing to click or approve on the Windows computer.

**Report a problem** is available on the main iPhone screen and in Help. Add a
description, inspect the optional diagnostics, then choose Compose email or
Share report. Support is at `rikyravi@gmail.com`; sending always requires your
action in your email/share app. Never include QR codes, keys or HA backups.

If pairing does not finish, use the specific title and diagnostic code shown by
the app to distinguish permission, discovery, connection, QR verification, and
encrypted-bond failures. The HA panel reports whether the receiver is searching,
has found the iPhone, or has opened the connection. **New code** restarts the whole attempt
without a manual step on the computer.

The code is valid for ten minutes to scan. A scan completed in time starts a
single five-minute attempt on the iPhone that is no longer interrupted by the
original expiry. As soon as the receiver verifies that exact session, the QR is
consumed and cannot be reused. It contains no permanent Home Assistant password,
and the app does not need to remain open after pairing. The same five minutes
cover proximity, retries, bond reuse and HA verification; no phase resets the timer.

## 5. Verify the result

The iPhone does not need Wi-Fi for Bluetooth pairing. The Windows receiver
still needs network access to HA and MQTT; Ethernet works. An already valid
bond may complete without another iOS Pair prompt. Completion still requires
the protected Bluetooth acknowledgement and HA storage.

For optional live dBm readings on the phone, use **Live signal in the app** in
the Presence Bridge panel, choose the paired person and scan that separate QR.
This is read-only HTTPS access to HA, not another Bluetooth pairing. The phone
needs a route to your HA server for this monitor; on the usual private LAN,
use Wi-Fi or your existing secure VPN. No public port needs to be opened.
Measurements show their age and expire when no fresh sample is received.
**Revoke signal access** revokes the monitor without removing the Bluetooth bond.

Home Assistant creates three entities for each paired phone:

- a presence binary sensor;
- a sensor containing the estimated Bluetooth room;
- a device tracker that can be assigned to a Home Assistant person.

Take the iPhone close to the observer and confirm that the presence sensor turns
on and the room sensor shows the assigned area. With multiple observers, assign
each one to its area and repeat the check while moving between rooms.

## 6. Update or remove

To dissociate a phone, remove its identity from the Presence Bridge panel.
Starting with 0.1.26, this also removes its saved Bluetooth pairing on the
Windows receiver that enrolled it. Keep that receiver online with its Windows
account signed in; the short-lived helper handles removal without a Windows
prompt. HA removes the identity only after the receiver verifies that the
selected phone's BLE/classic bonds and private key are absent. Other phones
and Wi-Fi assignments are not affected. If removal fails, keep the HA identity
and retry once the reported receiver problem is resolved.

For a completely fresh test, also use **Forget This Device** for the receiver in
the iPhone's Bluetooth settings if it is still listed. HA cannot remotely clear
iOS's saved-device list. Do not erase unrelated Bluetooth devices or reset the
whole adapter. Then generate a new QR when ready to scan.

Update the integration from HACS. For a Windows observer, rerun `install.ps1`
from the new release while keeping its existing ID. The installer stops the old
task, updates the files, and verifies the new service. Run `uninstall.ps1` as
Administrator to remove it.

For important automations, combine Bluetooth with motion, doors, Wi-Fi, and
other signals. Bluetooth alone cannot guarantee an exact room position.
