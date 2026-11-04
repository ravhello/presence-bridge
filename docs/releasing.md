---
title: Release Channels And Distribution Verification
permalink: /releasing/
---

# Release Channels And Distribution Verification

A security fix on main or in a prerelease is not sufficient for ordinary HACS
users. The fixed version must be a published full release in the normal channel.
Default HACS catalog inclusion is a separate maintainer decision.

## Before Publication

1. Inspect the exact source/tag and pass HACS, hassfest, administrator-service
   permission tests and the receiver/frontend checks. Do not disable checks.
2. Verify the packaged manifest requires Bluetooth and the packaged configuration
   services use HA's administrator-service helper. Test the downloadable payload,
   not only the working checkout.
3. Record source SHA, immutable archive names and SHA-256 hashes. Keep private
   configuration, credentials and iOS sources out of the public artifacts.
4. Use a prerelease only for an intentionally opt-in version. When users must
   receive a security fix normally, publish a full release and mark it latest.
   Preserve explicit experimental hardware limits in either channel.

## After Publication

1. Read GitHub's unauthenticated releases/latest endpoint. It must return the
   intended fixed tag with draft=false and prerelease=false.
2. Verify the tag still points to the tested commit and download the assets
   anonymously. Compare their hashes with the recorded manifest.
3. Inspect custom_components/presence_bridge in the default-channel source ZIP
   (the standard HACS repository layout), not just an optional separate asset.
   Confirm the version, required Bluetooth dependency and administrator services.
4. Check HACS's normal full-release selection with betas disabled. An API or
   source-archive check is evidence for distribution metadata, not proof of a
   successful install/restart on every user's HA host.
5. Update online installation instructions to match the real channel. Request
   review on the existing HACS submission only after these checks pass; avoid
   duplicate submissions and unsolicited repeated comments.

## Correcting A Channel Mistake

If an already validated version was accidentally published as a prerelease,
promote that same release to full/latest without moving its tag or overwriting
assets. Explain the correction in its notes, preserve the original hashes and
repeat the public default-channel verification. Do not claim new physical
compatibility merely because the distribution channel changed.
