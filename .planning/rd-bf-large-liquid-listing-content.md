# Research Direction Task BF — review existing listing-notice content

## Scope — 2026-10-09 KST

The operator continued after BE. PR #261 merged as
`402bc61e87df6ee71bce5f53f740ac1aaa148e1d`, matching its reviewed tree.
All nine final-head checks passed, including both Python CI runs with 4,706
passed and three skipped. CodeRabbit approved the exact head without inline
findings or unresolved threads. The isolated GCP research checkout was verified
clean at that merge; the collector remained unchanged. BF starts from the same
clean worktree on `codex/large-liquid-listing-content`.

BE identified 14 codes without AW/AX reviewed issuer-content candidates. Their
97 windows and 5,666 exact required code-dates remain in the full population;
none is dropped. Existing BA source coordinates point to one KIND listing or
relisting notice per code. Initial BF investigation read project rules, prior
plans and producer code only; no notice body has yet been reopened.

This task reviews existing saved notice content for explicit issuer name,
own/holding-company business, domestic headquarters and legal domicile claims.
Record each field separately, including absent/ambiguous content. A listing
notice, industry label or Korean headquarters alone does not certify continuous
operating status or legal domestic-company identity. Preserve all unknown
eligibility/publication fields and short histories.

## Metadata-only registration — before raw body reads

Let `private` mean `/home/minju/.local/share/trading-engine-research`, and
`repo` mean this worktree. Only these saved JSON inputs may be read initially:

| input | path | SHA-256 |
|---|---|---|
| BA final source-point links | `private/large-liquid-ba-identity-reconciliation-20261009-v1/result.json` | `e871aa7388c046197b4b89e7185ff85f2b5ec93940165865ce663154d9f08c95` |
| BB exact required dates | `private/large-liquid-bb-replay-inventory-20261009-v1/result.json` | `2ac0b2c81d19921d19cfea95a850574d9bef8dc7b5d1ebc900aa1f039ea0d9b4` |
| BE reconciled gap map | `private/large-liquid-be-interval-gaps-20261009-v1/result.json` | `f7f13fe5c36dad14b89f6ed5defc2e474a0f8da6732f7d9136036092a600aea4` |
| AS listing acquisition registry | `repo/var/public-kind-as-listings/verified-result.json` | `44186f066bfa0127ca8358b271d0dcc98a631f82060ac68d35974b869e163001` |
| AV six listing-anchor registry | `private/large-liquid-kind-six-anchors-20261008-v1/result.json` | `a8ca29c67173e2ce088a7c75e13e946d4a30045cdcab55addfa1b6680e74ffc4` |

The fixed target codes are 0126Z0, 259960, 302440, 316140, 323410, 326030,
329180, 352820, 361610, 373220, 377300, 383220, 402340 and 443060.
Read each registry's declared case metadata to resolve exact saved body
filenames and their pins. Match code, ISIN, receipt, URL, publication/listing
roles and body hash against BA; match the named gap/date population against
BE/BB. Append the exact 14 paths and hashes here before opening any body.
No arbitrary directory scan, ancestor acquisition replay or helper import
that reads unregistered inputs is part of this step.

## Content-review boundary

After fixing the raw-body allowlist, hash-check and read only those existing
14 notice bodies. Begin with 0126Z0's one notice, then apply the same stated
field review to the other registered notices. Do not open hyperlinks, fetch
new wrappers/sections, run DART searches, read account credentials, query a
trading database, extract a market-price series or inspect returns/trial logs.
Incidental amounts visible in corporate notices must be reported as incidental
source exposure, never used as strategy observations or performance inputs.

Keep literal source claims, quotation coordinates and provenance separate from
interpretation. Distinguish issue listing, issuer formation, legal domicile,
headquarters, business activity and actual operating-period continuity. Keep
notice publication/effective/listing dates separate from `known_on`; do not
backdate retrospective statements or infer the unapproved 08:30 model.
No `IdentityPeriod`, baseline reset, selectable universe or portfolio event
may be created by this review. Five prior observations remain five for 0126Z0.

Save the exact registration snapshot, five input summaries, 14 allowed bodies,
review result and helper/test snapshots exclusively at
`private/large-liquid-bf-listing-content-20261009-v1` with 0700 directories,
0600 files and durable exclusive writes. Preserve any failed/partial root;
never overwrite or retry in place. Missing source-content obligations remain
named gaps and require a separate registered acquisition before any later
network request; no blanket annual-report collection follows automatically.

## Verification and publication

Independently check source hashes, exact quotations, issuer/issue matching,
all 14 dispositions and unchanged short/control/date populations. Test absent
or ambiguous business/domicile, cross-issuer links, late publication and an
industry label incorrectly promoted to an operating-company proof. Reuse
existing transport guards rather than build another general acquisition layer.

Actual large/liquid sizing and comparison counts remain zero; actual lot/event
and certified continuous-coverage denominators remain undefined. R3's official
historical release evidence is still missing, the KOSCOM account is unknown,
and the clarification draft remains unsent. This review does not approve an
assumption, login, account creation, paid service, inquiry dispatch or trial.

After independent verification, freeze the exact secret-free inventory and
archive to a separately registered fresh private GCP evidence root. Read back
membership, bytes, hashes and permissions. Complete local document/index
checks, scanner, exact-head CI and CodeRabbit before native merge, then
fast-forward only the existing isolated research checkout. Keep operational
collectors unchanged. Report this bounded content review separately from
strategy validation and identify the next concrete remaining obligation.

## Exact raw-body allowlist — registered before first body read

The five metadata files matched their registered pins. The two registries
resolve exactly the 14 BA source objects and BE's named 97 windows / 5,666
code-date obligations; the code, ISIN, receipt, body URL/hash and acquisition
result pin agree. No body bytes were read during that mapping.

AS means `repo/var/public-kind-as-listings`; AV means
`private/large-liquid-kind-six-anchors-20261008-v1`.

| code | exact body path under root | SHA-256 |
|---|---|---|
| 0126Z0 | AS/20251120002094-body.html | `a2cceeaf235c50ecb7fc54c2e9a1c508368494daecd8bab1164b1fd0576774dc` |
| 259960 | AS/20210806001111-body.html | `27146ff804ca4d3ab75a27c3aa0d87b98e3547203dcc17f52e931d33f5e3035e` |
| 302440 | AS/20210316004960-body.html | `a2e22bdd3c1fb013f06bb65e82ce3a595199afdc359a808f5529c4bc9b1880de` |
| 316140 | AS/20190211002089-body.html | `788fd85750ab192784962035f49895d2caf351589199c08f8b4a0b64d9939feb` |
| 323410 | AS/20210804000905-body.html | `1045bb8d6f40316909a3eaf2204d73de5445ce8db33ccc46c6a811cf9c2e4e92` |
| 326030 | AV/20200630001561-body.html | `d586f3f219cfa762c3344f9b559395f06352c4819f37d6adf6c22d9cf50ad14b` |
| 329180 | AS/20210915001176-body.html | `e819f3cbfaa76861f5034c145c9fb767804df32e1cffa229f0ccb98296343bb6` |
| 352820 | AV/20201013001470-body.html | `a650f92fcb4c521231dd4ce9b41d3393f54e0e87f73d9c51fe6087e1dc308e99` |
| 361610 | AV/20210507001469-body.html | `5cf9d83b0bd76cddc88ca996df298361bc7655d1d3726118b66737b6229b3828` |
| 373220 | AS/20220125001917-body.html | `7cbac800a0109c8b5bdaa5682d503ffba8d3662e2f271fe2b730d437c01d7848` |
| 377300 | AV/20211101001116-body.html | `f52e4e364060804d80f1379e33a07af67cb9162005777768b2c442609da421b9` |
| 383220 | AV/20210518000880-body.html | `36a7fe21cc1d11e0b92ed864dc93f4c65382b1fafd94164785d33e3492d1d20a` |
| 402340 | AV/20211125001109-body.html | `ecfe8b57fdd9c72c9915a270e36ad92f749587aee835b0801f5c982455f0688d` |
| 443060 | AS/20240503001523-body.html | `70ffa3525676da5ffffe4eb74bcc9544c63dccd0f3f0feaaf9069f2a08894d0e` |

Read 0126Z0 first, then only the remaining listed files under the same bounded
review. For each, record issuer/issue identity and literal business/product,
headquarters and incorporation-country statements independently. A principal
product/business row is a source-stated business candidate; an industry code
alone is not. Accept no implied legal domicile from a Korean name/address.
No item creates continuous eligibility or fills a short lookback.

The stored publication timestamps are KIND search-display timestamps in KST,
not certified historical dissemination or market-field availability. Preserve
this source role when carrying the claims forward. Missing/ambiguous content
stays unresolved; all 14 bodies may be reviewed, but no additional linked
source or DART acquisition is authorized by this fixed allowlist.

## Interpretation clarification after complete notice review

The complete registered bodies were read and hash-checked. All 14 contain
issuer/common-share identity, code/ISIN, listing date, industry and domestic
headquarters statements. None contains a separate principal-product,
own/consolidated-operating or holding-management business statement. Each
remains a business-content gap under AW's existing sufficient-content contract.
Industry labels are retained as literal source fields, not promoted to the
missing business proof. Incidental par/public-offering prices, share counts
and shareholder percentages were visible in the complete notices, but were
not extracted as market series or strategy/performance inputs.

A docs-only rule audit distinguishes the absence of an explicit incorporation-
country field from the actual business-content gap. AW's sufficient-content
candidate contract uses issuer name, domestic headquarters and own/consolidated
operations or holding-management business. AV requires substantive domestic
operating-company status and the exact issue bridge for an accepted bundle;
neither prescribes a particular country-field format. BF records absent legal-
domicile statements as a diagnostic, not a newly added mandatory gate. This
clarification changes no rule, eligibility decision or certification.

The fixed later GCP destination is
`/home/minjun4897/research-evidence/large-liquid-bf-listing-content-20261009-v1`.
Freeze inventory/member bytes/hashes before transfer; retain the existing
AX receiver guards and independent readback. Only the isolated research
checkout may be fast-forwarded after reviewed merge; collectors stay unchanged.

The unchanged registration prefix that existed before the first body read
has SHA-256 `562814a85bf2a0dc90897a119348696d4995b61b9723683af1daebea50f7b8e5`.
Preserve its exact bytes as `bf-prereg-before-body.md` alongside the later
execution-registration snapshot. The source allowlist and existing-body review
were fixed prospectively; the interpretation entries above follow the reads.

## Actual offline execution and independent verification

Execution registration SHA-256 was
`606cdf76e4e629da18765dbea3069e2189a296a82263fd19010dde984be01a63`.
The zero-write preflight passed and one exclusive execution saved 41 files at
the registered private root. The package includes both the original before-
body registration prefix and the later execution snapshot; the later snapshot
is not represented as a prospective record of already observed findings.

| artifact | SHA-256 |
|---|---|
| result | `1366bd3d044a99cc9cb6908330e794d7597e57c8a05f080cfae35deba57f2e57` |
| local manifest | `d938cef9f2a7c549f0dd2ca351a4d6bda1016258cf4d921da3d7d189c59e1b71` |
| review producer | `74c7f1c85fa7ac5f4b43003ae266aaadc22ddf5800593e0f37d02c3895f11089` |
| producer tests | `763ab53b1c500984716703f980b7269de21088ab0b0bbf2eef2f697b828eaa76` |
| independent reader | `e5c1ad4327379d11e1e8d213067f763572663357dc2af20511b4cf3a68711723` |
| independent receipt | `e18d23102b94db455df26ee824250adf3aad612c1c2917fc1eaf4013538c7449` |

The producer's 13 synthetic boundary tests passed. The independent reader did
not import it and reconstructed all 14 visible snapshots with a different
lexical HTML scanner. It checked all original/saved input pins, 41 exact members,
40 local-manifest entries, private permissions and 84 literal quotations with
raw/visible character and line coordinates. Its 14 synthetic checks passed;
all 15 unsafe actual-output mutations were refused. A separate static audit
found no correction needed in the review, archive/readback or sync helpers.

Each of the 14 notices supplies issuer/common-share identity, exact issue,
listing date, industry and Korean headquarters. Business statements and
explicit legal-country statements are both absent; only the first is the
existing AW business-content gap. BF adds no country-field gate. All 14 still
need a separate own/consolidated/holding-business source. The 122 prior AW/AX
content candidates stay unchanged, with zero new candidates in BF.

The full 136 codes, 917 windows, 27 controls, eight short windows and 55,773
unique required code-dates matched the pinned inputs. The reviewed group
remains 97 windows / 5,666 code-dates, and 0126Z0 retains five prior observations.
No period, reset, known-on value, certification, actual sizing or comparison
was created. Source HTTP/SSH requests, database and reserved/trial reads were
zero during content review and independent verification.

## Exact GCP archive registration — before transfer

The fixed destination remains the BF evidence root registered above. Inventory
SHA-256 is `7472f22549d067cdcb32c75f4f42bf635f3651036755ff445d7cda714f2fbbb5`.
It pins all 41 package members and eight verification/transport snapshots;
adding the inventory and transport manifest gives exactly 51 remote files.
The no-SSH, zero-write transport preflight passed for 12,194,491 expanded bytes
and 828,183 compressed bytes, with manifest SHA-256
`66b49b0046aedb56908226f65a0ae6556abb26fe5715a87de42283a7c4236616`.
The reused AX receiver is pinned at
`3d1e7a812e0abfd318c29592650638dca3c1fae003fab43f7ea007424443dc2c`.
Transfer only this inventory to the fresh private root, then separately read
back every member, byte count, hash and permission and compare collector state.
Preserve failed/partial destinations rather than overwrite them.

The single transfer completed. Separate readback at 2026-10-09 10:46:17 UTC
verified all 51 remote files against the registered membership, bytes, hashes,
manifest and 0700/0600 permissions. The operational collector remained clean
at `4ce85d714890b87f1a57ae89d4942660e41c0483`. No collector code or settings
were changed. This is evidence archival, not a deployed strategy or trial.

## Next bounded work

Start with 0126Z0's missing own/holding-business source: identify an exact
official document and relevant leaf from existing registered metadata. No
such new receipt/leaf has been established in BF. If saved metadata is
insufficient, preregister a finite one-code/date metadata search before a new
request; then pin and review only the identified business leaf. Do not expand
to whole annual reports or price sections. A later positive business candidate
would not itself certify operating continuity, public availability or a full
identity bundle. R3 historical market-field publication remains a separate
unresolved prerequisite under the operator's additional-verification choice.

Local planning-index and rule-ownership tests passed with 84 passed and one
skipped; the repository guardrail scanner passed. Reviewed-commit CI,
CodeRabbit, merge and isolated research-code synchronization are subsequent
publication steps and must be reported from their actual receipts.
