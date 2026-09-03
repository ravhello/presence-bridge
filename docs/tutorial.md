---
title: Setup tutorial
permalink: /tutorial/
---

# Presence Bridge setup tutorial

## 1. Check the requirements

You need Home Assistant 2025.1 or newer, an MQTT broker already connected to
Home Assistant, an always-on Windows 10/11 computer with Bluetooth LE, and an
iPhone running iOS 17 or newer. Keep each observer computer in a fixed place.

## 2. Install the free integration

1. In HACS, open **Integrations**.
2. From the three-dot menu, select **Custom repositories**.
3. Enter `https://github.com/ravhello/presence-bridge`, choose
   **Integration**, and confirm.
4. Install **Presence Bridge** and restart Home Assistant.
5. Open **Settings > Devices & services > Add integration**, search for
   **Presence Bridge**, and complete setup.

## 3. Prepare the Windows observer

Download and extract the latest repository release. On the fixed Windows
computer, open PowerShell **as Administrator** in `bridge\windows` and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

Enter a stable ID such as `living_room_pc`, a readable name, and credentials
for a dedicated MQTT user. The installer asks for the password interactively,
so it does not enter shell history. At the end, both the GATT check and the
scheduled task must report success.

When the observer appears in the Presence Bridge panel, assign it to the Home
Assistant area where it is physically installed. Room estimates use the fixed
observer that receives the strongest fresh iPhone signal.

## 4. Pair a person

Open **Presence Bridge** in the Home Assistant sidebar, select a person and the
nearest observer, then choose **Create code**. Open Presence Pair on the iPhone,
complete the one-time personal purchase, and scan the QR code. The Windows
receiver accepts automatically. If iOS asks, allow Presence Pair to
use Bluetooth, then keep the app open until the green confirmation appears.

The default code expires after three minutes. It contains no permanent Home
Assistant password, and the app does not need to remain open after pairing.

## 5. Verify the result

Home Assistant creates three entities for each paired phone:

- a presence binary sensor;
- a sensor containing the estimated Bluetooth room;
- a device tracker that can be assigned to a Home Assistant person.

Take the iPhone close to the observer and confirm that the presence sensor turns
on and the room sensor shows the assigned area. With multiple observers, assign
each one to its area and repeat the check while moving between rooms.

## 6. Update or remove

Update the integration from HACS. For a Windows observer, rerun `install.ps1`
from the new release while keeping its existing ID. The installer stops the old
task, updates the files, and verifies the new service. Run `uninstall.ps1` as
Administrator to remove it.

For important automations, combine Bluetooth with motion, doors, Wi-Fi, and
other signals. Bluetooth alone cannot guarantee an exact room position.
