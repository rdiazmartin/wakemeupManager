# PRD Quality Review — wakemeupManager

## Overall verdict

Adequate, decision-ready enough to hand to UX/architecture — the product spine (WOL stays local, SSH-wake via tailnet, one-shot enrollment password, Linux v1, single token) is crisp and internally coherent in nearly every mention. It falls short of strong because Done-ness is polluted with a cluster of unbounded "configurable"/"razonable" consequences, the retry policy in UJ-1 has no owning FR or bounds, and SM-1 is impossible to verify against the FR-10 single-token assumption. Fix the measured items and the two open-question/body tensions and this PRD is ready to drive epics.

## Decision-readiness — adequate

The PRD makes the decisions that matter at this stage and states them repeatedly so they cannot be missed: WOL is emitted only by the BE on the physical LAN (§0, §4.2, Non-Goal line 251, Assumption §9 line 308), transport app→BE is Tailnet/LAN, enrollment uses a one-shot password (FR-6, Glossary "Password de un solo uso"), v1 is Linux-only (FR-7 out-of-scope, §5), single user/token (FR-10 assumption). The six Open Questions (§8) are real decisions, not stubs, and §6.2 already tags the schedule feature for a PM decision.

But two open questions contradict what the body already asserts. OQ-2 asks "¿Cómo se regenera/pierde un token de usuario en v1?" while FR-10 consequence 2 states it as fact ("regeneración posible vía CLI del administrador en v1") — a builder could implement the CLI while the decision is formally open. OQ-6 asks "¿soporta múltiples rangos desde v1?" while FR-1's description already promises "el/los rango(s) de IPs configurados" (line 83) and the FR titles/scoping elsewhere treat ranges as singular (FR-1 line 89, FR-3). Storage of inventory is open (OQ-3) yet FR-6 consequence 1 asserts "no persiste en disco, logs ni base de datos" — the sentence is true under either SQLite or flat file, but naming "base de datos" in a formal consequence before the storage decision is loose. The working title is unconfirmed ("Working title — confirm.", line 9) — a trivial but real decision pending.

### Findings
- **[high]** OQ-2 and FR-10 contradict each other (§8 Q2 vs §4.4 FR-10) — OQ-2 asks how tokens are regenerated/invalidated while FR-10 consequence 2 asserts CLI regeneration as a done decision. *Fix:* fold token regeneration into FR-10 or into an assumption, and re-scope OQ-2 to the truly open part (invalidation on lost phone).
- **[medium]** OQ-6 vs §4.1 plural "rango(s)" (line 83 vs FR-1 line 89) — the body already promises plural ranges while the open question asks whether plural is in v1. *Fix:* either commit to single range in v1 (fix FR-1 description) or mark multiple ranges as in-scope and re-word OQ-6 as a v2 question about cross-VLAN.
- **[low]** Storage decision (OQ-3) leaks into FR-6 wording (line 152 mentions "base de datos" pre-decision). *Fix:* rephrase as "no persiste en disco ni en el almacenamiento del inventario (SQLite o fichero, decisión OQ-3)".
- **[low]** Working title unconfirmed (line 9) — "Working title — confirm." is a pending decision with no owner or deadline. *Fix:* resolve it or move to Open Questions.

## Substance over theater — strong

Every section earns its place; there is no filler, no marketing, no hypothetical case study. The "Por qué importa" paragraph (line 21) is concrete about the pain (desktop apps, MACs by hand, scripts) and the differentiator (shutdown-from-phone, one-shot credential pattern). The Glossary isn't ornamental prose — every entry is behaviorally defined and most are used verbatim downstream (e.g., "Máquina descubierta" vs "Máquina gestionada" in FR-6 consequence 3 and UJ-2's climax). The counter-metrics block (SM-C1, SM-C2) is genuine anti-misuse guidance rather than decoration, and it names the exact optimization it forbids ("escaneos tan agresivos que las máquinas caen de la tabla"). The only editorial flourish, "gancho emocional" in §6.2, is a working note, not theater.

### Findings
- **[low]** "beacon" appears twice (lines 19, 47) while the Glossary term is "Magic packet" (line 71) — §0 promises "La documentación usa el vocabulario del Glosario (§3) de forma exacta". *Fix:* replace "beacon WOL" (line 19) and "el beacon se pierde" (line 47) with "magic packet".
- **[low]** None — dimension otherwise clean; no findings of substance.

## Strategic coherence — adequate

The invariant "WOL never crosses the VPN" holds in every mention I could find (lines 13, 71, 117, 131, 251, 308), and "Tailnet / VPN" is used equivalently throughout as the Glossary defines (line 73), so no internal drift there. The one-shot-credential theme is consistent across Vision (line 21), Glossary (line 76), UJ-2 (lines 52, 55), FR-6 (line 152), and FR-14 — with FR-11 consequence 2 ("Los eventos de alta nunca contienen la password") as a correct subset of FR-6 consequence 1, so FR-6 and FR-11 are consistent, not contradictory (verified). UJ-2's edge case ("la password **igualmente** se descarta", line 55) matches FR-6's "exitosa o fallida" (line 154) and the Glossary ("la descarta, exitosa o fallida", line 76) — verified consistent; and the transport claim (stdin or chmod-600 file, never in argv visible to `ps`, line 153) is technically sound with sshpass.

Two cracks. UJ-1's edge case (line 47) introduces a retry policy ("si el beacon se pierde, el BE reintenta según política configurable y deja rastro en el log") that no FR owns or bounds — FR-4 consequence 3 explicitly makes wake fire-and-forget ("El endpoint responde éxito aunque la máquina tarde en arrancar"), so the retry behavior exists only as a journey note, with no FR, no default, and no bounds; and SM-3's 95%-in-≤60s target arguably depends on that undefined retry policy. Second crack: FR-13 consequence 1 says "Encender está disponible siempre" while UJ-1's edge case says the button "no repite el envío" when online — available-always vs. no-op-when-online is unspecified UI semantics.

### Findings
- **[medium]** UJ-1 retry policy orphaned from the FR set (§2.3 UJ-1, line 47 vs §4.2 FR-4, line 128) — "reintenta según política configurable" has no owning FR and no default/bounds, yet SM-3 depends on it. *Fix:* add a FR (e.g., FR-4.5 "Reintento de wake": N attempts with backoff, defaults N=1, configurable, logged) or delete the sentence and make SM-3 assume no retry.
- **[low]** FR-13 "Encender está disponible siempre" (line 227) vs UJ-1 "no repite el envío" when already online (line 47) — is the button disabled, or a no-op, or does it show the current state? *Fix:* specify the online-state behavior in FR-13 consequence 1.

## Done-ness clarity — adequate

This is the weakest dimension. The good news: every FR has a "Consequences (testable)" block, and most are genuinely verifiable (FR-1 inventory completeness with N machines; FR-3's 10-minute re-scan and POST /scan; FR-4's packet bytes "MAC destino repetida 16 veces sobre 6×0xFF"; FR-6's no-persistence-after-failure; FR-8's 600 perms + dedicated user; FR-9's "404 máquina inexistente, 409 alta ya realizada"). FR-6 vs FR-11 verified consistent (see Strategic coherence).

But the rubric's forbidden words and unbounded values appear exactly where rigor matters:
- FR-2 consequence 1 (line 103): "TTL configurable (default razonable, p. ej. 60 s)" — "razonable" hedges the only stated value, and the *checking* cadence of FR-2 ("comprobación periódica", line 100) has no interval at all; TTL freshness (60 s) is not the scan interval.
- FR-12 consequence 1 (line 218): "polling configurable" — no default, no bounds, no unit (seconds? minutes?).
- UJ-1 edge case (line 47): "política configurable" — retry count/backoff undefined (also flagged under coherence).
- FR-5 consequence 1 (line 138): "usa **preferentemente** una interfaz Ethernet no-WiFi" — "preferentemente" is unverifiable; nothing states the fallback or how a violable preference is detected/tested.
- FR-3 consequence 3 (line 113): "no se eliminan sin confirmación" — the confirmation channel (API flag? UI dialog? TTL-expiry grace period?) is unspecified, so the negative consequence is untestable as written.
- FR-15 consequence 1 (line 245): "validación de conexión con feedback" — "feedback" is unbounded: what constitutes a valid connection and what message/state follows failure?

Metrics are the bigger Done-ness problem. SM-1 (line 278) requires "Roberto y al menos una persona más del hogar usan la app ≥3 días a la semana", but with FR-10's single-token/no-accounts assumption the BE's logs (FR-11) cannot attribute actions to Roberto vs. María — the metric's primary clause is unmeasurable. SM-4 (line 283) "≥50% de los apagados se hacen desde la app (vs. en la máquina)" has an unmeasurable denominator: physical-machine shutdowns are never logged anywhere, so the ratio cannot be computed.

### Findings
- **[high]** SM-1 unmeasurable under FR-10's single-token assumption (§7 SM-1, line 278 vs §4.4 FR-10, line 197) — no per-user attribution exists in v1 logging. *Fix:* reword SM-1 to device-level usage ("la app se usa ≥3 días/semana por al menos dos dispositivos") or add per-device identity to v1, or downgrade to SM-1.2 and pick a measurable primary.
- **[high]** FR-2 "razonable" + missing check interval (§4.1 FR-2, lines 100–103) — "TTL configurable (default razonable, p. ej. 60 s)" leaves the freshness value uncommitted and FR-2 never states the periodic-check interval. *Fix:* "TTL default 60 s, configurable entre 15 s y 300 s; comprobación periódica cada TTL/2" (bounds + default + algorithm).
- **[high]** FR-12 "polling configurable" unbounded (§4.5 FR-12, line 218) — no default or range for the automatic refresh. *Fix:* "polling automático cada 30 s por defecto, configurable 10–300 s, pausable bajo battery-saver".
- **[medium]** FR-5 "preferentemente" unverifiable (§4.2 FR-5, line 138) — a preference cannot be tested. *Fix:* "si existe interfaz Ethernet, el WOL se envía solo por ella y expone el nombre de la interfaz en GET /status; en ausencia de Ethernet, estado 'warning'".
- **[medium]** FR-3 "sin confirmación" undefined (§4.1 FR-3, line 113) — no confirmation mechanism specified, so the non-deletion consequence is untestable. *Fix:* "las máquinas ausentes se marcan offline y solo desaparecen mediante DELETE explícito o UI con diálogo de confirmación".
- **[medium]** FR-15 "feedback" unbounded (§4.5 FR-15, line 245) — no definition of a valid connection or the failure UX. *Fix:* "al guardar, la app llama a GET /status; 401 → 'token rechazado', timeout → 'BE inalcanzable'".
- **[medium]** SM-4 denominator unmeasurable (§7 SM-4, line 283) — "vs. en la máquina" physical shutdowns leave no trace. *Fix:* measure only the numerator ("apagados desde la app ≥5/semana") or run a manual household sample count for the first month.
- **[low]** FR-6 "se usa una única vez" (line 152) is not directly observable; only the no-persistence half is testable. Acceptable as a proxy — say so explicitly in the FR so nobody writes a fake test. *Fix:* add "la propiedad de uso único es invariante de diseño; verificamos la ausencia de persistencia".

## Scope honesty — strong

§5 lists seven explicit non-goals and §6.2 lists MVP out-of-scope with v2 mapping; the FR-level "Out of Scope" blocks (FR-1, FR-4, FR-7) match the non-goals one-for-one (multi-segment/L2, unicast WOL / wake-over-VPN, Windows/macOS). §6.1 enumerates exactly the FR ranges that will be built (FR-1..FR-3, FR-4+FR-5, FR-6..FR-8, FR-9..FR-11, FR-12..FR-15) with no phantom scope. Features that look tempting are explicitly deferred with rationale rather than smuggled in ("Schedules/horarios de despertado — v2 (gancho emocional; añade valor real)", line 268). FR-5 is honestly labeled as documentation/conditioning work ("detecta y comunica") rather than over-promised reliability. Nothing in the body silently exceeds the stated non-goals.

### Findings
- **[low]** "No va a sustituir a un CMS/centro de control de la red (routers, DNS...)" (§5, line 256) — "CMS" is the only vague entry in an otherwise concrete list. *Fix:* drop "CMS" and keep "no es un centro de control de red (router/DNS/ACL)".
- **[low]** §6.2 schedules note duplicates OQ-1 (§8, line 291) — both re-open the same decision. *Fix:* keep the v2 note and make OQ-1 reference it instead of restating.

## Downstream usability — adequate

The PRD is genuinely consumable by UX, architecture, and story writers: FR descriptions map to UJs explicitly ("Realiza UJ-1, UJ-2, UJ-3" in each feature description), SM→FR validation mappings are stated (SM-2→FR-6, SM-3→FR-4, SM-4→FR-7), §9 anchors every assumption to section+FR, and the JSON/error vocabulary is concrete enough for a first API contract ("404 máquina inexistente, 409 alta ya realizada", "1xx endpoint list", "claves → authorized_keys"). The transport details a build needs are present: ssh-copy-id with stdin/chmod-600 file, `shutdown`/`systemctl poweroff`, sudoers NOPASSWD pattern, keystore storage, dedicated non-root user with 600-perm keys.

What downstream consumers still have to invent: (a) any retry semantics for wake (see Strategy/UJ-1), (b) concrete bounds for the three "configurable" values (FR-2/FR-12/UJ-1), (c) an error-code catalog beyond the two examples, (d) what "confirmación" for deletion means (FR-3), and (e) session persistence in the app ("sesión previa guardada", UJ-1 line 43) is mentioned in a journey but has no FR or storage note. Also FR-6's "El transporte del alta acepta la contraseña por stdin o fichero temporal chmod 600" (line 153) uses "transporte del alta", which is not glossary vocabulary, in a section the PRD itself promises to keep glossary-exact. None of this blocks starting UX/architecture, which is the right bar for "adequate".

### Findings
- **[medium]** Missing build-consumable retry/error contract (§4.2) — wake is fire-and-forget per FR-4 consequence 3 yet UJ-1 promises retries; downstream cannot implement either way. *Fix:* resolve via a new FR-4.x with explicit attempt count/default/bounds (see Strategic coherence finding) and add an HTTP error catalog to FR-9.
- **[medium]** "sesión previa guardada" (UJ-1, line 43) has no owning FR — token storage (FR-15) implies it but session/silent-reauth behavior is unspecified. *Fix:* add a consequence to FR-15: "la app reutiliza el token almacenado sin re-login salvo 401".
- **[low]** "El transporte del alta" (FR-6 consequence 2, line 153) is non-glossary jargon in a doc promising exact glossary vocabulary. *Fix:* "el BE acepta la password por stdin o fichero temporal chmod 600…".

## Shape fit — strong

At 308 lines (~7–8 pages rendered) the document sits inside the 5–8 page calibration for a self-hosted household tool and does not over-engineer: 15 FRs is a defensible surface for discovery+WOL+shutdown+API+app, all scoped to one platform and one user model. The shape mirrors the product: five feature blocks (4.1–4.5) each with Description→FRs→Consequences(testable)→Out of Scope, then Non-Goals, MVP, Metrics, Open Questions, Assumptions. UJ-JTBD-user structure in §2 is proportional (3 journeys, 2 non-users). Language (Spanish) matches the household audience and the repo's artifact language; the memlog file in the PRD directory is a useful audit artifact. The only structural nits: no addendum.md exists (rubric says expect none — correct), and the doc lacks an explicit "what changed" section, which the frontmatter `updated: 2026-09-13` only partially covers for a draft in flux.

### Findings
- **[low]** No change record for a draft expected to iterate (frontmatter line 5 vs status draft line 3) — downstream readers cannot see what changed between revisions. *Fix:* add one line to frontmatter or a short "Revisiones" note once edits begin.

## Mechanical notes

- **ID integrity:** UJ-1..3 contiguous ✓; FR-1..15 unique/contiguous ✓; SM-1..4 + SM-C1, SM-C2 unique ✓. Section numbering §0–§9 consistent.
- **Assumptions roundtrip (verified inline→index):** all 7 inline `[ASSUMPTION]` blocks are indexed (§2.2→line 300, FR-2→301, FR-3→302, FR-5→303, FR-7→304, FR-10→305, FR-15→306) ✓. **Reverse direction fails:** two §9 entries have **no inline marker** — "§4.3 (FR-8) — El BE gestiona un único par de claves SSH" (line 307) and "§1 — Tailscale (o VPN equivalente)…" (line 308). A builder reading FR-8 or §1 will not see these assumptions. *Fix:* add inline `[ASSUMPTION]` tags at FR-8 and §1, or trim the index to match only tagged assumptions. *(medium — the one real mechanical defect.)*
- **UJ-2 vs FR-6 (password disposal):** verified **consistent** — UJ-2 line 55 "la password **igualmente** se descarta" matches FR-6 consequence 1 "(exitosa o fallida)" (line 152), FR-6 consequence 4 (line 155), and Glossary line 76. No conflict.
- **FR-6 vs FR-11:** verified consistent — FR-11 consequence 2 (line 205) is a proper subset of FR-6 consequence 1 (line 152); both forbid password-in-logs. Redundant, not contradictory. Optional: dedupe by having FR-11 reference FR-6.
- **Password transport claim (FR-6, line 153):** technically sound — sshpass over stdin or `--password-file` with 600 perms never exposes the credential in argv visible to `ps`. Remaining nit (low): password passed via environment remains readable by root via `/proc/<pid>/environ`; consider stating "ni en argv ni en env" for completeness.
- **Terminology consistency:** "tailnet/VPN/Tailnet" used equivalently everywhere matching Glossary line 73 ✓, except "dirección tailscale" lowercase at line 179 (trivial). "alta"/"dar de alta" is used at lines 17 and 21 (§1/Vision) *before* the Glossary definition at line 75 — acceptable in a doc read linearly twice, but strictly the Vision section precedes its own glossary; consider a parenthetical "(§3)" at first Vision use. "gestionada" vs "descubierta" used exactly per Glossary throughout ✓ (lines 53, 68–69, 154, 213–215). Failures: "beacon" lines 19/47 (see Substance finding), "transporte del alta" line 153 (see Downstream finding).
- **Other:** frontmatter `status: draft` matches the "Working title" placeholder; dates consistent (created=updated=2026-09-13). No addendum.md present — nothing to cross-check. §9's FR-8 entry says "único par de claves (no una por usuario)" — this is a genuinely important security-relevant assumption that deserves the full inline treatment, reinforcing the mechanical finding.
