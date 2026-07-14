/**
 * The clipboard is not a safe place, and the UI says so wherever it is offered.
 *
 * Clipboard managers keep history, sync it between devices, and back it up. A
 * recovery key or a decrypted note that goes through the clipboard may outlive the
 * paste by months. This is a documented, accepted risk (THREAT_MODEL.md T-13), and
 * the honest mitigation is to tell the user rather than to pretend.
 */

export const stubbornClipboardWarning =
  "Clipboard managers often keep a history — and may sync it to other devices. " +
  "Clear it, or prefer saving to a file.";
