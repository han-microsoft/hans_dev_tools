/**
 * Message ID synchronization — reconciles temp local IDs with server canonical IDs.
 *
 * After a chat stream completes and the session is refreshed from the
 * server, local messages may have temporary IDs (temp-*, assistant-*,
 * aborted-*). This function matches them to server messages by role
 * and position proximity, replacing the temp ID with the canonical one.
 */

import type { Message } from "@/api/types";

/** Prefixes that identify temporary IDs created during streaming. */
const TEMP_PREFIXES = ["temp-", "assistant-", "aborted-"];

/** Check whether a message ID is temporary (created during streaming). */
function isTempId(id: string): boolean {
  return TEMP_PREFIXES.some((prefix) => id.startsWith(prefix));
}

/**
 * Sync local temporary message IDs with server canonical IDs.
 *
 * Matches local messages to server messages by role + position proximity (±1).
 * Only replaces IDs that start with temp-/assistant-/aborted- prefixes.
 */
export function syncMessageIds(
  localMessages: Message[],
  serverMessages: Message[],
): Message[] {
  return localMessages.map((local, i) => {
    /* Skip permanent IDs */
    if (!isTempId(local.id)) return local;

    /* Find matching server message: same role, within ±1 position,
       and not already claimed by another local message */
    const serverMatch = serverMessages.find(
      (s: Message, si: number) =>
        s.role === local.role &&
        Math.abs(si - i) <= 1 &&
        !localMessages.some((l, li) => li !== i && l.id === s.id),
    );

    if (serverMatch) return { ...local, id: serverMatch.id };
    return local;
  });
}
