---
title: Pairing protocol
permalink: /protocol/
---

# Presence Pair protocol v1

## Goals

The protocol lets an authenticated Home Assistant administrator associate a
person with the Bluetooth identity created when that person's iPhone bonds to a
specific Windows observer. It deliberately does not give the app a Home
Assistant access token, MQTT credentials, or an IRK.

## Transport

Home Assistant and the observer communicate through the local MQTT broker under
`presence_bridge/v1/observers/<observer_id>`. The iPhone and observer communicate
over a temporary Bluetooth LE GATT service.

| Characteristic | UUID | Access |
| --- | --- | --- |
| Service | `61dd168c-4ec1-40de-a78c-ccdce5774bba` | Temporary advertisement |
| Session | `ef70387a-ba9d-4e83-9171-fea99252b57a` | Plain read |
| Claim | `700dbb64-64ed-4f51-adae-b106a00e908a` | Encrypted write |
| Result | `fb5312b1-c24c-42b5-8d21-5263874c258f` | Encrypted read |

## Invitation

The authenticated HA panel creates a 32-byte random secret and a 2048-bit
ephemeral RSA key. The app receives this short-lived URI through a QR code or a
custom URL:

```text
presencepair://pair?v=1&sid=<session>&oid=<observer>&exp=<unix>&secret=<base64url>
```

The invitation is valid for at most ten minutes; the default is three minutes.
The observer advertises only while a session is active.

## Claim

The app reads the public session characteristic and requires an exact match with
the scanned invitation. It then computes:

```text
HMAC-SHA256(secret, "presence-bridge:v1\n<sid>\n<oid>\n<exp>")
```

The claim characteristic requires Bluetooth link encryption. The write both
triggers the operating-system bond and proves possession of the invitation. The
secret itself is never written over Bluetooth.

The cross-language test vector is:

```text
sid    = abcdefghijklmnopQRSTUVWX
oid    = dell_cucina
exp    = 1800000180
secret = bytes 00 through 1f
proof  = ADpo7jfRbxUAzTdGQw-jA3O_tRC_ynKLQ2hHUR94uho
```

## Identity transfer

The SYSTEM observer detects the single IRK added during the bond. It encrypts
the result with RSA-OAEP SHA-256 and publishes the ciphertext to HA. HA accepts
the result only for the active session, decrypts it in memory, and checks that
the IRK resolves a currently observed private address before storing it.

IRKs and raw addresses are omitted from entities, WebSocket UI payloads, logs,
and diagnostics.

## Threat model

- An attacker who only sees Bluetooth traffic cannot forge the HMAC or read the
  encrypted claim.
- A QR screenshot is sensitive until it expires. Cancel the session if the code
  is exposed.
- The MQTT broker is trusted local infrastructure. Use a dedicated account,
  topic ACLs, and TLS across untrusted network segments.
- Administrator access to Windows or HA can access presence identity material,
  as expected for the systems that perform resolution.
- More than one new Bluetooth identity during pairing fails closed.
