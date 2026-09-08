---
title: Pairing protocol
permalink: /protocol/
---

# Presence Pair protocol v2

## Goals

The protocol lets an authenticated Home Assistant administrator associate a
person with the Bluetooth identity created when that person's iPhone bonds to a
specific Windows observer. It deliberately does not give the app a Home
Assistant access token, MQTT credentials, or an IRK.

## Transport

Home Assistant and the observer communicate through the local MQTT broker under
`presence_bridge/v1/observers/<observer_id>`. After scanning a code, the iPhone
temporarily advertises a Bluetooth LE GATT service. Current apps derive a fresh
service UUID from the one-time QR secret and session id, which prevents Windows
from reusing a stale GATT catalogue after a previous bond. Receivers also
accept the original static service UUID so older app builds remain pairable.
The Windows observer scans for either service and initiates the connection and
bond.

| Characteristic | UUID | Access |
| --- | --- | --- |
| Service | Per-session UUID; legacy `61dd168c-4ec1-40de-a78c-ccdce5774bba` | Temporary iPhone advertisement |
| Session | `ef70387a-ba9d-4e83-9171-fea99252b57a` | Plain read by observer |
| Claim | `700dbb64-64ed-4f51-adae-b106a00e908a` | HMAC-authenticated read by observer |
| Result | `fb5312b1-c24c-42b5-8d21-5263874c258f` | HMAC-authenticated write by observer |

## Invitation

The authenticated HA panel creates a 32-byte random secret and a 2048-bit
ephemeral RSA key. The app receives this short-lived URI through a QR code or a
custom URL:

```text
presencepair://pair?v=2&sid=<session>&oid=<observer>&exp=<unix>&secret=<base64url>
```

The invitation is valid for at most ten minutes. The app advertises only while
that invitation is active and visible on screen. Seeing a generic Presence Pair
advertisement does not consume or shorten another invitation; the separate
five-minute completion window starts only after the exact session is read and
verified.

## Claim and acknowledgement

The observer first reads the public session characteristic and requires an
exact match with its active invitation. It then reads and verifies this claim
before requesting a Windows bond:

```text
HMAC-SHA256(secret, "presence-bridge:v2\n<sid>\n<oid>\n<exp>")
```

The claim proves that the nearby app owns the one-time invitation. The QR
secret itself is never transmitted over Bluetooth. Verifying the claim before
the bond also avoids a Windows/iOS interoperability failure where WinRT hides
the temporary iPhone GATT service immediately after committing the bond.

After verification, the observer writes an encrypted acknowledgement carrying:

```text
HMAC-SHA256(secret, "presence-bridge-result:v2\n<sid>\n<oid>\n<exp>\naccepted")
```

The app shows success after validating that acknowledgement. If WinRT closes
the temporary GATT channel immediately after a successful bond, the
acknowledgement becomes best effort: the Windows observer still captures the
new IRK and Home Assistant remains the authoritative completion state. It does
not start a second pairing prompt.

The cross-language test vectors are:

```text
sid             = abcdefghijklmnopQRSTUVWX
oid             = dell_cucina
exp             = 1800000180
secret          = bytes 00 through 1f
claim proof     = -q6gU_keDbd_kcgOXTfnolM0m3ke96HzM_b-z1uuXPk
acceptance proof = u1KQY_RD_yBH9VIx0CrrOe2nZ0zrkbuoa3iOBhH6QE4
```

## Identity transfer

The SYSTEM observer selects the IRK created by the bond. For an existing bond,
it matches the connected identity address or resolvable private address against
the retained Windows IRKs. It encrypts the result with RSA-OAEP SHA-256 and
publishes the ciphertext to HA. HA accepts the result only for the active
session, decrypts it in memory, and checks that the IRK resolves a currently
observed private address before storing it.

IRKs and raw addresses are omitted from entities, WebSocket UI payloads, logs,
and diagnostics.

## Threat model

- An attacker who only sees Bluetooth traffic cannot forge either HMAC or
  recover the QR secret from a claim.
- A QR screenshot is sensitive during its ten-minute scan window. Cancel the
  session if the code is exposed. Scanning in time gives the iPhone one bounded
  five-minute attempt; once the exact session is verified, the QR is consumed
  and cannot start another attempt.
- A different Presence Pair session is rejected before Windows attempts a bond.
- The MQTT broker is trusted local infrastructure. Use a dedicated account,
  topic ACLs, and TLS across untrusted network segments.
- Administrator access to Windows or HA can access presence identity material,
  as expected for the systems that perform resolution.
- Ambiguous IRK selection fails closed.
