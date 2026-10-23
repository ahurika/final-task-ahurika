import crypto from "node:crypto";
import { describe, expect, it } from "vitest";
import {
  CANONICAL_FORMAT_PREFIX,
  CANONICAL_FORMAT_VERSION,
  FIELD_TAG_CHANNEL,
  FIELD_TAG_RECIPIENT_ID,
  FIELD_TAG_EMAIL_SUBJECT,
  FIELD_TAG_EMAIL_BODY,
  FIELD_TAG_PUSH_TITLE,
  FIELD_TAG_PUSH_BODY,
  serializeCanonicalNotification,
  normalizeNotificationContent,
  computeRequestFingerprint,
  verifyRequestFingerprint,
  resolveFingerprintKey,
} from "../src/idempotency";
import type { NotificationPayload } from "../src/validation";

describe("Phase 2B — Canonical Serialization and HMAC Fingerprinting", () => {
  // 32-byte test key (never used in production)
  const testHmacKey = Buffer.from(
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "hex",
  );
  const testKeyVersion = 1;

  describe("Exact Canonical Binary Serialization", () => {
    it("serializes an email notification into the exact approved tagged binary format", () => {
      const emailPayload: NotificationPayload = {
        channel: "email",
        recipient_id: "alice",
        subject: "Hi",
        body: "Test",
      };

      const canonicalBytes = serializeCanonicalNotification(emailPayload);

      // 1. Verify prefix: "relay-notification-v1\0" (22 bytes)
      const expectedPrefix = Buffer.from("relay-notification-v1\0", "utf8");
      expect(canonicalBytes.subarray(0, 22)).toEqual(expectedPrefix);

      let offset = 22;

      // 2. Channel tag 0x01, length 5 ("email")
      expect(canonicalBytes.readUInt8(offset)).toBe(FIELD_TAG_CHANNEL);
      expect(canonicalBytes.readUInt32BE(offset + 1)).toBe(5);
      expect(canonicalBytes.subarray(offset + 5, offset + 10).toString("utf8")).toBe(
        "email",
      );
      offset += 10;

      // 3. Recipient ID tag 0x02, length 5 ("alice")
      expect(canonicalBytes.readUInt8(offset)).toBe(FIELD_TAG_RECIPIENT_ID);
      expect(canonicalBytes.readUInt32BE(offset + 1)).toBe(5);
      expect(canonicalBytes.subarray(offset + 5, offset + 10).toString("utf8")).toBe(
        "alice",
      );
      offset += 10;

      // 4. Email subject tag 0x10, length 2 ("Hi")
      expect(canonicalBytes.readUInt8(offset)).toBe(FIELD_TAG_EMAIL_SUBJECT);
      expect(canonicalBytes.readUInt32BE(offset + 1)).toBe(2);
      expect(canonicalBytes.subarray(offset + 5, offset + 7).toString("utf8")).toBe(
        "Hi",
      );
      offset += 7;

      // 5. Email body tag 0x11, length 4 ("Test")
      expect(canonicalBytes.readUInt8(offset)).toBe(FIELD_TAG_EMAIL_BODY);
      expect(canonicalBytes.readUInt32BE(offset + 1)).toBe(4);
      expect(canonicalBytes.subarray(offset + 5, offset + 9).toString("utf8")).toBe(
        "Test",
      );
      offset += 9;

      expect(canonicalBytes.length).toBe(offset);
      expect(canonicalBytes.length).toBe(22 + 10 + 10 + 7 + 9); // 58 bytes
    });

    it("serializes a push notification with correct tags (0x20 title, 0x21 body)", () => {
      const pushPayload: NotificationPayload = {
        channel: "push",
        recipient_id: "dev_1",
        title: "Alert",
        body: "Message",
      };

      const canonicalBytes = serializeCanonicalNotification(pushPayload);

      // Verify channel field
      let offset = 22;
      expect(canonicalBytes.readUInt8(offset)).toBe(FIELD_TAG_CHANNEL);
      expect(canonicalBytes.readUInt32BE(offset + 1)).toBe(4);
      expect(canonicalBytes.subarray(offset + 5, offset + 9).toString("utf8")).toBe(
        "push",
      );
      offset += 9;

      // Verify recipient field
      expect(canonicalBytes.readUInt8(offset)).toBe(FIELD_TAG_RECIPIENT_ID);
      expect(canonicalBytes.readUInt32BE(offset + 1)).toBe(5);
      expect(canonicalBytes.subarray(offset + 5, offset + 10).toString("utf8")).toBe(
        "dev_1",
      );
      offset += 10;

      // Verify push title tag 0x20
      expect(canonicalBytes.readUInt8(offset)).toBe(FIELD_TAG_PUSH_TITLE);
      expect(canonicalBytes.readUInt32BE(offset + 1)).toBe(5);
      expect(canonicalBytes.subarray(offset + 5, offset + 10).toString("utf8")).toBe(
        "Alert",
      );
      offset += 10;

      // Verify push body tag 0x21
      expect(canonicalBytes.readUInt8(offset)).toBe(FIELD_TAG_PUSH_BODY);
      expect(canonicalBytes.readUInt32BE(offset + 1)).toBe(7);
      expect(canonicalBytes.subarray(offset + 5, offset + 12).toString("utf8")).toBe(
        "Message",
      );
      offset += 12;

      expect(canonicalBytes.length).toBe(offset);
      expect(canonicalBytes.length).toBe(63); // Confirms exact 63-byte push test vector
    });

    it("verifies exact hexadecimal output vector for a deterministic 60-byte email fixture", () => {
      const payload: NotificationPayload = {
        channel: "email",
        recipient_id: "bob",
        subject: "Hello",
        body: "World",
      };

      const canonicalBytes = serializeCanonicalNotification(payload);

      // Build expected hex manually to verify against the specification:
      // Prefix: "relay-notification-v1\0" (22 bytes)
      // 72656c61792d6e6f74696669636174696f6e2d763100
      // Tag 0x01, len 5 (00000005), "email" (656d61696c)
      // Tag 0x02, len 3 (00000003), "bob" (626f62)
      // Tag 0x10, len 5 (00000005), "Hello" (48656c6c6f)
      // Tag 0x11, len 5 (00000005), "World" (576f726c64)
      const expectedHex =
        "72656c61792d6e6f74696669636174696f6e2d763100" +
        "0100000005656d61696c" +
        "0200000003626f62" +
        "100000000548656c6c6f" +
        "1100000005576f726c64";

      expect(canonicalBytes.length).toBe(60);
      expect(canonicalBytes.toString("hex")).toBe(expectedHex);
    });

    it("verifies exact hexadecimal output vector for a deterministic 63-byte push fixture", () => {
      const payload: NotificationPayload = {
        channel: "push",
        recipient_id: "dev_1",
        title: "Alert",
        body: "Message",
      };

      const canonicalBytes = serializeCanonicalNotification(payload);

      // Prefix: "relay-notification-v1\0" (22 bytes)
      // 72656c61792d6e6f74696669636174696f6e2d763100
      // Tag 0x01, len 4 (00000004), "push" (70757368) (9 bytes)
      // Tag 0x02, len 5 (00000005), "dev_1" (6465765f31) (10 bytes)
      // Tag 0x20, len 5 (00000005), "Alert" (416c657274) (10 bytes)
      // Tag 0x21, len 7 (00000007), "Message" (4d657373616765) (12 bytes)
      // Total: 22 + 9 + 10 + 10 + 12 = 63 bytes
      const expectedHex =
        "72656c61792d6e6f74696669636174696f6e2d763100" +
        "010000000470757368" +
        "02000000056465765f31" +
        "2000000005416c657274" +
        "21000000074d657373616765";

      expect(canonicalBytes.length).toBe(63);
      expect(canonicalBytes.toString("hex")).toBe(expectedHex);
    });

    it("prevents adjacent field boundary collisions (length and tag separation)", () => {
      // If boundaries were not tagged and length-prefixed:
      // "A" + "BC" could serialize identically to "AB" + "C"
      const payloadA: NotificationPayload = {
        channel: "email",
        recipient_id: "user",
        subject: "A",
        body: "BC",
      };

      const payloadB: NotificationPayload = {
        channel: "email",
        recipient_id: "user",
        subject: "AB",
        body: "C",
      };

      const bytesA = serializeCanonicalNotification(payloadA);
      const bytesB = serializeCanonicalNotification(payloadB);

      expect(bytesA).not.toEqual(bytesB);

      const fpA = computeRequestFingerprint(payloadA, testHmacKey, 1);
      const fpB = computeRequestFingerprint(payloadB, testHmacKey, 1);
      expect(fpA.fingerprint).not.toEqual(fpB.fingerprint);
    });
  });

  describe("Unicode NFC Normalization Equivalence", () => {
    it("produces identical canonical bytes and fingerprints for canonically equivalent Unicode content", () => {
      // Decomposed "e" + combining acute accent (\u0065\u0301)
      const decomposedPayload: NotificationPayload = {
        channel: "email",
        recipient_id: "recipient_1",
        subject: "caf\u0065\u0301",
        body: "Cr\u0065\u0300me br\u0075\u0302l\u0065\u0301e",
      };

      // Precomposed "é", "è", "û" (\u00e9, \u00e8, \u00fb)
      const precomposedPayload: NotificationPayload = {
        channel: "email",
        recipient_id: "recipient_1",
        subject: "caf\u00e9",
        body: "Cr\u00e8me br\u00fbl\u00e9e",
      };

      // Raw strings differ before normalization
      expect(decomposedPayload.subject).not.toBe(precomposedPayload.subject);
      expect(decomposedPayload.body).not.toBe(precomposedPayload.body);

      // Normalization produces identical content
      const norm1 = normalizeNotificationContent(decomposedPayload);
      const norm2 = normalizeNotificationContent(precomposedPayload);
      expect(norm1.field1.value).toBe(norm2.field1.value);
      expect(norm1.field2.value).toBe(norm2.field2.value);

      // Serialized canonical bytes are identical
      const bytes1 = serializeCanonicalNotification(decomposedPayload);
      const bytes2 = serializeCanonicalNotification(precomposedPayload);
      expect(bytes1).toEqual(bytes2);

      // HMAC fingerprints are identical
      const fp1 = computeRequestFingerprint(decomposedPayload, testHmacKey, 1);
      const fp2 = computeRequestFingerprint(precomposedPayload, testHmacKey, 1);
      expect(fp1.fingerprint).toEqual(fp2.fingerprint);
      expect(fp1.fingerprintHex).toBe(fp2.fingerprintHex);
    });

    it("does NOT normalize recipient_id; decomposed vs precomposed recipient IDs remain distinct", () => {
      const decomposedRecipient: NotificationPayload = {
        channel: "email",
        recipient_id: "us\u0065\u0301r", // Decomposed
        subject: "Subject",
        body: "Body",
      };

      const precomposedRecipient: NotificationPayload = {
        channel: "email",
        recipient_id: "us\u00e9r", // Precomposed
        subject: "Subject",
        body: "Body",
      };

      // Recipient ID must be preserved exactly without normalization
      const norm1 = normalizeNotificationContent(decomposedRecipient);
      const norm2 = normalizeNotificationContent(precomposedRecipient);
      expect(norm1.recipientId).not.toBe(norm2.recipientId);

      const fp1 = computeRequestFingerprint(decomposedRecipient, testHmacKey, 1);
      const fp2 = computeRequestFingerprint(precomposedRecipient, testHmacKey, 1);
      expect(fp1.fingerprint).not.toEqual(fp2.fingerprint);
    });

    it("distinguishes letter case differences (case is preserved)", () => {
      const payloadUpper: NotificationPayload = {
        channel: "email",
        recipient_id: "user",
        subject: "HELLO",
        body: "WORLD",
      };

      const payloadLower: NotificationPayload = {
        channel: "email",
        recipient_id: "user",
        subject: "hello",
        body: "world",
      };

      const fpUpper = computeRequestFingerprint(payloadUpper, testHmacKey, 1);
      const fpLower = computeRequestFingerprint(payloadLower, testHmacKey, 1);

      expect(fpUpper.fingerprint).not.toEqual(fpLower.fingerprint);
    });

    it("distinguishes meaningful whitespace differences (whitespace is preserved)", () => {
      const payloadSingleSpace: NotificationPayload = {
        channel: "email",
        recipient_id: "user",
        subject: "Word One",
        body: "Body text",
      };

      const payloadDoubleSpace: NotificationPayload = {
        channel: "email",
        recipient_id: "user",
        subject: "Word  One",
        body: "Body text",
      };

      const fpSingle = computeRequestFingerprint(
        payloadSingleSpace,
        testHmacKey,
        1,
      );
      const fpDouble = computeRequestFingerprint(
        payloadDoubleSpace,
        testHmacKey,
        1,
      );

      expect(fpSingle.fingerprint).not.toEqual(fpDouble.fingerprint);
    });
  });

  describe("HMAC-SHA-256 Fingerprint Calculation & Verification", () => {
    const samplePayload: NotificationPayload = {
      channel: "email",
      recipient_id: "alice@example.com",
      subject: "Important Notice",
      body: "This is a security test notification.",
    };

    it("returns a fixed 32-byte Buffer and 64-character hexadecimal digest", () => {
      const result = computeRequestFingerprint(
        samplePayload,
        testHmacKey,
        testKeyVersion,
      );

      expect(Buffer.isBuffer(result.fingerprint)).toBe(true);
      expect(result.fingerprint.length).toBe(32);
      expect(result.fingerprintHex.length).toBe(64);
      expect(/^[0-9a-f]{64}$/.test(result.fingerprintHex)).toBe(true);
      expect(result.fingerprintFormatVersion).toBe(CANONICAL_FORMAT_VERSION);
      expect(result.hmacKeyVersion).toBe(testKeyVersion);
    });

    it("computes deterministic output for fixed test key material and input", () => {
      const run1 = computeRequestFingerprint(
        samplePayload,
        testHmacKey,
        testKeyVersion,
      );
      const run2 = computeRequestFingerprint(
        samplePayload,
        testHmacKey,
        testKeyVersion,
      );

      expect(run1.fingerprint).toEqual(run2.fingerprint);
      expect(run1.fingerprintHex).toBe(run2.fingerprintHex);
    });

    it("verifies matching fingerprint using verifyRequestFingerprint", () => {
      const result = computeRequestFingerprint(
        samplePayload,
        testHmacKey,
        testKeyVersion,
      );

      const isValid = verifyRequestFingerprint(
        samplePayload,
        result.fingerprint,
        testHmacKey,
      );
      expect(isValid).toBe(true);
    });

    it("rejects tampered payloads during fingerprint verification", () => {
      const result = computeRequestFingerprint(
        samplePayload,
        testHmacKey,
        testKeyVersion,
      );

      const tamperedPayload: NotificationPayload = {
        ...samplePayload,
        subject: "Tampered Notice",
      };

      const isValid = verifyRequestFingerprint(
        tamperedPayload,
        result.fingerprint,
        testHmacKey,
      );
      expect(isValid).toBe(false);
    });

    it("rejects verification with wrong HMAC key", () => {
      const result = computeRequestFingerprint(
        samplePayload,
        testHmacKey,
        testKeyVersion,
      );

      const wrongKey = Buffer.alloc(32, 9);
      const isValid = verifyRequestFingerprint(
        samplePayload,
        result.fingerprint,
        wrongKey,
      );
      expect(isValid).toBe(false);
    });

    it("ensures different channels produce different fingerprints", () => {
      const email: NotificationPayload = {
        channel: "email",
        recipient_id: "target",
        subject: "Title",
        body: "Body",
      };

      const push: NotificationPayload = {
        channel: "push",
        recipient_id: "target",
        title: "Title",
        body: "Body",
      };

      const fpEmail = computeRequestFingerprint(email, testHmacKey, 1);
      const fpPush = computeRequestFingerprint(push, testHmacKey, 1);

      expect(fpEmail.fingerprint).not.toEqual(fpPush.fingerprint);
    });

    it("rejects HMAC keys shorter than 32 bytes or non-Buffers", () => {
      const shortKey = Buffer.alloc(16);
      expect(() =>
        computeRequestFingerprint(samplePayload, shortKey, 1),
      ).toThrow("Fingerprint HMAC key must be a Buffer containing at least 32 bytes.");

      expect(() =>
        computeRequestFingerprint(
          samplePayload,
          "string_key" as unknown as Buffer,
          1,
        ),
      ).toThrow("Fingerprint HMAC key must be a Buffer containing at least 32 bytes.");
    });

    it("rejects non-positive or non-integer HMAC key versions", () => {
      expect(() =>
        computeRequestFingerprint(samplePayload, testHmacKey, 0),
      ).toThrow("Fingerprint HMAC key version must be a positive integer.");

      expect(() =>
        computeRequestFingerprint(samplePayload, testHmacKey, -1),
      ).toThrow("Fingerprint HMAC key version must be a positive integer.");

      expect(() =>
        computeRequestFingerprint(samplePayload, testHmacKey, 1.5),
      ).toThrow("Fingerprint HMAC key version must be a positive integer.");
    });
  });

  describe("resolveFingerprintKey (Multi-version HMAC key rotation resolution)", () => {
    const activeKeyV2 = Buffer.alloc(32, 2);
    const activeKeyVersion = 2;

    const previousKeyV1 = Buffer.alloc(32, 1);
    const previousKeys = new Map<number, Buffer>([[1, previousKeyV1]]);

    it("resolves the active key when targetVersion equals activeKeyVersion", () => {
      const resolved = resolveFingerprintKey(
        2,
        activeKeyV2,
        activeKeyVersion,
        previousKeys,
      );
      expect(resolved).toBe(activeKeyV2);
    });

    it("resolves historical key from previousKeys map when recorded version matches", () => {
      const resolved = resolveFingerprintKey(
        1,
        activeKeyV2,
        activeKeyVersion,
        previousKeys,
      );
      expect(resolved).toBe(previousKeyV1);
    });

    it("returns null if recorded key version is not found (prevents silent fallback to active key)", () => {
      const resolved = resolveFingerprintKey(
        99,
        activeKeyV2,
        activeKeyVersion,
        previousKeys,
      );
      // Critical security invariant: NEVER silently return activeKeyV2
      expect(resolved).toBeNull();
    });

    it("returns null for non-integer or non-positive target versions", () => {
      expect(
        resolveFingerprintKey(0, activeKeyV2, activeKeyVersion, previousKeys),
      ).toBeNull();
      expect(
        resolveFingerprintKey(-1, activeKeyV2, activeKeyVersion, previousKeys),
      ).toBeNull();
      expect(
        resolveFingerprintKey(1.5, activeKeyV2, activeKeyVersion, previousKeys),
      ).toBeNull();
    });

    it("successfully verifies an older reservation fingerprint across key rotation", () => {
      const historicalPayload: NotificationPayload = {
        channel: "email",
        recipient_id: "historical_user",
        subject: "Old Receipt",
        body: "Your transaction from last month.",
      };

      // 1. Client previously created reservation under HMAC key V1
      const originalResult = computeRequestFingerprint(
        historicalPayload,
        previousKeyV1,
        1,
      );
      expect(originalResult.hmacKeyVersion).toBe(1);

      // 2. Later, server is rotated to active key V2, with V1 retained in previousKeys
      // Phase 2C receives idempotency replay and resolves key using recorded hmac_key_version:
      const resolvedKey = resolveFingerprintKey(
        originalResult.hmacKeyVersion,
        activeKeyV2,
        2,
        previousKeys,
      );
      expect(resolvedKey).not.toBeNull();

      // 3. Verification succeeds against recorded reservation fingerprint using the resolved V1 key
      const isValid = verifyRequestFingerprint(
        historicalPayload,
        originalResult.fingerprint,
        resolvedKey!,
      );
      expect(isValid).toBe(true);

      // 4. Verification fails if someone mistakenly evaluated against the active V2 key
      const isInvalidAgainstActiveKey = verifyRequestFingerprint(
        historicalPayload,
        originalResult.fingerprint,
        activeKeyV2,
      );
      expect(isInvalidAgainstActiveKey).toBe(false);
    });
  });
});

