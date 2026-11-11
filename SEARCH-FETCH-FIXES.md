# Search and profile fetching fixes

This release preserves existing campaign rules, lead statuses, sender authorizations,
Unipile endpoints, databases and the dashboard layout. It adds no API dependencies.

- A named search card remains usable when its detail endpoint returns 404/422;
  other profiles continue. Only fields actually returned by the search are kept.
- Missing detail fields no longer erase the same profile's visible search fields.
- Malformed optional sections and null detail payloads do not crash mapping.
- Known-profile verification accepts wrapped provider payloads and still requires
  the exact canonical profile URL.
- Connection checks reject missing or non-OK V1 session statuses. Search and delivery
  preflight persist an explicitly invalid session as needing reconnection.
- Dashboard loading checks the saved sender, with a short in-memory cache.
- Transient provider outages do not destroy a valid sender binding, and sender
  identity drift cannot transfer delivery authorization to another member.
- Hidden-result errors no longer imply an exhausted monthly search limit.

## External limitation

LinkedIn Classic search may return only anonymous OUT_OF_NETWORK cards. These are
not named leads and are not unmasked, fabricated or counted as saved profiles.
Individual publicly known profiles may remain accessible through the same account.
No Brave integration or other new key is added by this release. Successful direct
profile fetching must not be reported as successful automatic keyword discovery.

## Explicit private-to-admin sender transfer

The owner-only transfer preview verifies an approved source workspace, the exact
public profile, the stored member identity, and a currently active provider session.
The confirmed operation refuses an occupied admin destination and active/uncertain
work. It atomically moves only the sender binding, revokes pending send/follow-up
permissions in both queues, consumes stale connection flows, and writes an audit
record. Research databases, drafts, lead statuses and conversation history remain
in their original workspace. Ordinary reconnect/callback ownership protections are
unchanged. Transferring an account does not reset LinkedIn's search limits.

Source ownership is located by the exact verified provider member identity, rather
than inferred from the browser's Google email. A previous admin binding can be
recovered without moving research. A verified but unbound session can be attached
to the empty admin workspace without modifying any private workspace. Ambiguous
provider identities and unrelated or occupied bindings fail closed.
