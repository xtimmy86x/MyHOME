# Experimental WHO1001 hardware inspection — panel 0.21.0

This first read-only view implements a small part of anotherjulien's proposal to
inspect physical devices independently from normal WHO/WHERE entities. It reads
one device at an explicitly entered, known **local A/PL address**. It does not
create a physical inventory in storage or change Home Assistant registries.

## Use and physical check

1. Open **Hardware · experimental** and select a connected gateway.
2. Enter a known individual A and PL. For example A=0, PL=15 produces `0015`;
   A=1, PL=1 produces `11`. PL=0, ambient/group addresses and bus routing are
   not accepted in this first version.
3. Click **Read device**. Opening the section itself sends nothing.
4. After dispatch, the view collects responses for 20 seconds. **End reading**
   or navigating away stops collection and invalidates a still-queued request.
   A request already transmitted cannot be withdrawn; no follow-up frame is sent.
5. Compare the hardware ID, firmware, internal slots and module addresses with
   MyHOME Suite or the physical device. Expand **Received WHO1001 frames** to
   inspect the original evidence and timestamps.

Do not run MyHOME Suite or another diagnostic tool concurrently with this read.
The protocol does not supply a request ID linking every response to a client.
The backend detects another WHO1001 transmission through this gateway's monitor
and ends its window; it cannot guarantee detection of an external client's read.

This version has automated protocol/transport/UI verification. **Physical reading
on the user's F454 is still pending.** The user's successful automatic-cover
calibration tests concern WHO2 and do not validate this WHO1001 feature.

## Exact transmitted request

The only frame this feature can construct is:

```text
*#1001*<individual local A/PL>*0##
```

The [device interview notes](https://github.com/OpenWebNet-HA/OWNd/wiki/Diagnostic-Device-interview)
describe this as the address-based entry to an automatic device description.
The existing command workers send it as a status request, using their existing
connection, pacing and response collection. No additional socket is opened.

No WHAT commands, discovery reset/suppression commands, DIM38 request,
configuration write dimensions, programming or movement commands are exposed.
The 10-second queue guard checks that the same gateway/monitor remains loaded
and connected before dispatch. Unsubscription and HA shutdown remove listeners
and timers. One inspection can be active per config entry; another socket gets
`inspection_busy`. The observation window has fixed limits of 200 WHO1001 RX
frames (512 characters each) and 64 internal module slots.

## Interpretation and attribution

Only RX frames received after dispatch are included. Only dimension responses
whose local A/PL equals the explicitly requested address are decoded. Two- and
four-digit encodings compare by their A and PL components (`01` equals `0001`,
but `15` does not equal `0015`). Generic WHERE `0`, routed addresses and other
addresses remain raw evidence and increment `unassociated_frames`; their data is
never attached to the selected device. This may leave modules undisplayed on
hardware that reports them with generic WHERE `0`. A later extension needs real
capture evidence and a stronger selection/attribution model before handling that.

| Dimension | Displayed information | Interpretation limits |
| --- | --- | --- |
| DIM13 | 32-bit hardware ID, eight uppercase hexadecimal digits | Zero/out-of-range values are not accepted as an identity; different IDs at the same address clear decoded details and end the read |
| DIM1 | Four raw catalogue identity values | Not a unique SKU/model; unknown field meanings are not invented |
| DIM2 | V.R.B firmware components | Displayed exactly as received numeric values |
| DIM30 | Internal slot, Object ID and raw flag | Flag 0/1 is interpreted as enabled/disabled from the dedicated DIM30 notes; other values remain unknown; slot numbering is not assumed to match the Suite GUI |
| DIM32 | Address reported for that internal slot | No inferred WHO, destination address, HA registry link or assignment |
| Other shapes/dimensions | Original frame and reception date | No speculative decoding or configuration interpretation |

Recognized Object labels are limited to documented examples: 6 light actuator,
8 dimmer, 128 daylight/presence sensor, 164 daylight sensor, 218 shutter actuator,
400 light control, 401 automation control, 406 scheduled scenario PLUS and
431 IR scenario control. Unknown Object IDs remain visible numerically.

A finished window means collection ended, **not that every module was received**.
Missing firmware, identity or modules stay explicitly unknown. Results are
transient, scoped to the current view and gateway, and are not a new source of
configuration truth. Closing/reloading the view discards them. Retained timestamps
refer to frame reception, not a guaranteed device configuration-change date.

## Sources and scope of the reverse-engineered model

Consulted on 2026-09-15:

- [anotherjulien's proposal in discussion #270](https://github.com/orgs/OpenWebNet-HA/discussions/270#discussioncomment-18398814).
- [Recovered documentation announcement](https://github.com/orgs/OpenWebNet-HA/discussions/270#discussioncomment-18434993).
- [Diagnostic Device Interview](https://github.com/OpenWebNet-HA/OWNd/wiki/Diagnostic-Device-interview).
- [Device Modules and DIM30](https://github.com/OpenWebNet-HA/OWNd/wiki/Device-Modules-and-DIM30).
- [Module Addressing and DIM32](https://github.com/OpenWebNet-HA/OWNd/wiki/Module-Addressing-and-DIM32).
- [Diagnostic Architecture](https://github.com/OpenWebNet-HA/OWNd/wiki/Diagnostic-Architecture).

These are reverse-engineered observations, not a complete official protocol
specification. The dedicated DIM30 page interprets its last field as DISABLED;
the interview overview still calls that field incompletely understood. The UI
therefore labels it as interpreted and retains the raw flag for verification.

Full discovery by hardware ID, selection of unconfigured devices, generic-WHERE
attribution, additional diagnostic domains, DIM35 catalogue/firmware decoding and
all programming operations remain future work. This iteration provides a concrete
read path to test before expanding those capabilities.
