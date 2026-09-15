# Experimental WHO1001 hardware inspection — panel 0.22.1

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
4. After dispatch, collection ends 0.5 seconds after the observed description
   boundary, provided no further WHO1001 messages arrive. Without that boundary
   it waits up to 20 seconds. **End reading**
   or navigating away stops collection and invalidates a still-queued request.
   A request already transmitted cannot be withdrawn; no follow-up frame is sent.
5. Compare the hardware ID, firmware, internal slots and module addresses with
   MyHOME Suite or the physical device. Expand **Received WHO1001 frames** to
   inspect the original evidence and timestamps.

Do not run MyHOME Suite or another diagnostic tool concurrently with this read.
The protocol does not supply a request ID linking every response to a client.
The backend detects another WHO1001 transmission through this gateway's monitor
and ends its window; it cannot guarantee detection of an external client's read.

**Physical WHO1001 check confirmed on the user's F454 (2026-09-15).** The user
provided 13 frames from local address `01`, showing hardware ID `009B5409`
(decimal `10179593`), firmware `1.1.0`, catalogue signature `107/6/1/1`, light
actuator slots 1/2 (Object 6) at `24`/`01`, and light control slots 3/4 (Object 400)
with no DIM32 address response. The user then reported the same result when
reading A=2, PL=4. This validates this device's two addresses returning the same
hardware ID; it does not establish universal module completeness or validate
programming, routed buses or other device families. The second read was confirmed
by the user; its full raw capture was not supplied. The 0.22 inventory UI still
needs the next physical acceptance check described below.

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
| DIM32 | Address reported for that internal slot | No inferred WHO, destination address, confirmed HA registry link or assignment |
| Other shapes/dimensions | Original frame and reception date | No speculative decoding or configuration interpretation |

Recognized Object labels are limited to documented examples: 6 light actuator,
8 dimmer, 128 daylight/presence sensor, 164 daylight sensor, 218 shutter actuator,
400 light control, 401 automation control, 406 scheduled scenario PLUS and
431 IR scenario control. Unknown Object IDs remain visible numerically.

A finished window means collection ended, **not that every module was received**.
Missing firmware, identity or modules stay explicitly unknown. Results are
transient, scoped to the panel instance and gateway, and are not a new source of
configuration truth. Leaving/reloading the panel discards them; switching its
sections preserves completed observations in the 0.22 temporary inventory. Retained timestamps
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


## Temporary inventory and HA candidates (0.22.0)

A frontend display cache retains only terminal reads without a reason/error and
with a valid nonzero hardware ID. It groups by **config entry + hardware ID**.
It is not shared between browsers and never uses localStorage, sessionStorage,
HA storage, profiles or registry writes. Starting an inspection remains the only
operation sending a bus read. Inventory rendering and candidate lookup send none.

- Keep the latest successful observation per gateway + normalized requested A/PL.
  `01` and `0001` replace each other; `24` and `0024` are different addresses.
  A successful reread with a different ID removes that address from the old ID's
  group. Failed, cancelled, ambiguous or unidentified reads leave previous
  observations intact, labeled with their previous completion date.
- A hardware card lists explicitly interrogated addresses and uses the latest
  retained read for firmware, modules, reported module addresses and raw frames.
  It does not merge a missing current field with an old value. Differing retained
  descriptions show a notice; all observations can still be partial.
- At most 100 address observations are retained **across all gateways**. Replacing
  a read renews its position; adding beyond the limit evicts the oldest address.
- Changing gateway, internal section or panel language preserves this cache and
  cancels any active collection. Leaving/reloading the panel or replacing the HA
  connection discards it. Clear inventory affects only the selected gateway and
  is disabled during a read. Other gateways remain isolated.
- Candidate entities use the current backend-provided HA inventory: same config
  entry, strictly local normalized address, and WHO 1 for Objects 6/8 or WHO 2 for
  Object 218. Routed/private-riser addresses, unknown types and control modules
  have no inferred matches. All matching entities are listed, including secondary
  entities. Multiple matches are not reduced to an assumed primary entity.
- Candidates are suggestions, not confirmed physical links. Names/removals update
  with the normal panel inventory refresh; no live-state subscription is added.
  Unknown module addresses remain unknown. HA devices are never regrouped in the
  native registry by this view.

### Next physical acceptance check

Read `01`, then `24`, waiting for each observation window to finish. Expect one
hardware card `009B5409` with both explicitly read addresses, the four modules in
its latest details and possible HA entities for the two light actuator addresses
when present in the selected gateway's inventory. Slots 3/4 may have no candidates.
Switch to Entities and back: the card remains without another diagnostic request.
Clear this gateway's inventory: the card disappears without a bus command. Reload
the panel: no retained hardware inventory should reappear.


## Early description completion (0.22.1)

The user's supplied 13-frame capture spans about 0.675 seconds and ends with
`*1001*4*0##`. Waiting the entire 20 seconds after that burst added unnecessary
latency. The backend now treats this exact RX frame as an observed interview
boundary **only after receiving a valid hardware ID at the requested local A/PL**.
An early/generic boundary before that identity does not enable fast completion.
DIM4 responses and WHAT4 at another WHERE do not enable it either.

Once armed, a separate 0.5-second quiet timer ends collection normally. Every
subsequent retained WHO1001 RX frame restarts that timer, allowing trailing
modules/addresses to be included. Ordinary WHO1/2 traffic does not prolong it.
The independent 20-second maximum, measured from request TX, is never extended.
If no qualified boundary is received, the existing maximum wait still applies.
Cancellation, shutdown, overlapping diagnostic TX, conflicting IDs and all bounds
cancel both timers and retain their original error/cleanup behavior. No new bus
frame is transmitted. The normal finished event enables the next read and feeds
the temporary inventory through the same existing path.

The [interview notes](https://github.com/OpenWebNet-HA/OWNd/wiki/Diagnostic-Device-interview)
were rechecked on 2026-09-15. They describe WHAT4 as an observable boundary while
explicitly leaving its broader semantics under investigation. Thus this heuristic
still does not prove description completeness or correlation with external
clients. Existing guidance to avoid concurrent diagnostic tools remains relevant.
The supplied capture is replayed in a regression test, with additional cases for
trailing frames, wrong/early boundaries, the maximum deadline and cancellation.
With the same arrival timing, completion should occur about 1.2 seconds after the
first response instead of waiting the full 20 seconds; physical timing acceptance
of this patch is pending.
