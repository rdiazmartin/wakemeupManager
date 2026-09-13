# Tech Review — Architecture Spine Stack Section

**Document reviewed:** `ARCHITECTURE-SPINE.md` (Stack, AD-2/AD-5/AD-7/AD-8, Structural Seed)
**Review date:** 2026-09-13 (same day the research was pinned)
**Method:** live web spot-check (PyPI API, Maven Central / Google Maven metadata, Python.org, Gradle services) + **source-level verification of the asyncssh 2.24.0 wheel** (unpacked `sftp.py`, `connection.py`, `public_key.py`) + empirical local check of ping//proc/net/arp under a non-root uid.

---

## 1. asyncssh (2.24.0) — [OK]

- **Version:** 2.24.0 is the latest release on PyPI (`requires_python >=3.10` → compatible with pinned Python 3.14). Verified live.
- **SFTP append `'a'`:** verified in the actual 2.24.0 wheel (`asyncssh/sftp.py`, `SFTPClient.open`): python mode strings are mapped by `_open_modes`, `'a'` → `FXF_WRITE|FXF_CREAT|FXF_APPEND`, and `'a+'` (read+append) also available. Idempotent-appended `authorized_keys` write is sound; for verification-after-append use `'a+'` or a separate read at implementation time.
- **Exec:** `SSHClientConnection.run()`/`create_process()` documented in the shipped docs; matches AD-4's fixed-command, no-free-shell shutdown (`conn.run("systemctl poweroff")`).
- **Host-key fingerprint during connect:** `connect(..., known_hosts=..., client_keys=[...], password=..., username=...)` — all kwargs exist (`asyncssh/connection.py`, `connect()` docs). After auth, `conn.get_server_host_key()` returns the `SSHKey` object and `key.get_fingerprint('sha256')` (default hash) returns the fingerprint (`public_key.py:513`). That is exactly what AD-2/AD-5 need.
  - ⚠ Note (design-inherent, not an error): at enrollment with `known_hosts=None` the host key is not verified (TOFU bootstrap). The spine already pins the fingerprint at that point and refuses actions on mismatch (`no_fiable`), so this is sound.
- OpenSSH-style authorized_keys/known_hosts support is listed in the library features. **Fit: correct.**

## 2. aiosqlite (0.22.x) — [OK]

- Latest stable: **0.22.1** (2025-12-23), `requires_python >=3.9`, production/stable classifier. Async bridge over `sqlite3` (one worker thread per connection, request queue) — fits AD-7's single-writer SQLite inventory/token-hash store.

## 3. Python 3.14 / uv — [OK] (one implementation note)

- Latest is **3.14.7** (2025-08); 3.14 is in "bugfix" maintenance, supported through 2030-10 (python.org release table).
- uv latest stable: **0.12.13**.
- ⚠ Note: Raspberry Pi OS Bookworm's **system** Python is 3.11. 3.14 on the Pi therefore requires uv's managed interpreters (`uv python install`), which the "uv (gestión de dependencias)" row already implies. State it explicitly in the deploy docs (`deploy/wakemeup.service` must use the uv-managed interpreter path).

## 4. FastAPI + uvicorn (0.141.x) — [OK]

- Latest stable: **0.141.1** (requires Python >=3.10). Pin is current. uvicorn unversioned → resolve at install; no issue.

## 5. `ping` (distro binary) + `/proc/net/arp` without root — [OK]

- Bookworm `iputils-ping` (3:20221126-1+deb12u1) ships `/bin/ping` with **file capability `cap_net_raw=ep`** and uses **ICMP datagram sockets** (kernel `net.ipv4.ping_group_range`). Per the Bookworm `ping(8)` man page, raw capability is only needed for non-echo queries; plain echo works unprivileged via the ICMP echo socket, so a non-root `wakemeup` user pings out of the box with the stock binary — the "no extra packages" claim holds.
- `/proc/net/arp` is mode **0444** (world-readable) on a stock kernel — verified empirically here (read as uid 1000, non-root). Claim holds.
- Empirical check on this machine (uid 1000, `ping -c1 -W1 127.0.0.1` → success with `/bin/ping` `cap_net_raw=ep`): mechanism confirmed.

## 6. EncryptedSharedPreferences deprecation + Keystore/DataStore — [OK]

- `androidx.security:security-crypto` **1.1.0** is the latest stable (Google Maven; released 2025-07-30). Official release notes: **"Deprecated all APIs in favour of existing platform APIs and direct use of Android Keystore."** (recorded at 1.1.0-beta01 and carried into 1.1.0).
- The spine's AD-8 stance — exclude EncryptedSharedPreferences; token in Keystore, DataStore for storage — **matches Google's current guidance** (direct Keystore use + DataStore; "EncryptedSharedPreferences queda excluida por deprecación" is accurate).
- datastore-preferences **1.2.1** confirmed on Google Maven (latest stable; 1.3.0 is still alpha).

## 7. Compose BOM 2026.09.00 — [OK]

- **Exists and is the latest release** of `androidx.compose:compose-bom` (Google Maven, lastUpdated 2026-09-09). Resolved the 2026.09.00 POM: it pins `androidx.compose.material3:material3` at **1.4.0** exactly as claimed (also Compose UI 1.12.1).

## 8. Material 3 dynamic colors (Android 12+) + fallback <12 — [OK]

- Unpacked `material3-android:1.4.0` AAR: `dynamicLightColorScheme` / `dynamicDarkColorScheme` / `DynamicTonalPaletteKt` present; the 3.1-variant (`dynamicLightColorScheme31`) used on API 31+ (Android 12). On < API 31 the static palette path is the designed behavior — the spine's "minSdk 26 … Dynamic Color solo en Android 12+ y degrada al tema grafito" assumption is correct.
- material3 minSdk = 21, so minSdk 26 is safe; targetSdk 36 is a current stable platform (36.1 minor + 37.x exist in the stable channel, so 36 is not the bleeding edge).

## 9. Kotlin / AGP / Gradle — [OK], with one "one-behind" item

- Kotlin **2.4.20**: latest on Maven Central (kotlin-stdlib, lastUpdated 2026-09-07). Compose compiler ships with Kotlin, so BOM 2026.09.00 + Kotlin 2.4.20 is a coherent current pair.
- AGP **9.4.0**: latest stable on Google Maven (9.5.0-alpha05 is newest overall).
- Gradle: current stable is **9.7.1** (released 2026-08-19). **9.6.x exists and is a recent stable, compatible with AGP 9.4.** ⚠ Minor: pin is one minor behind current; no functional risk, but consider 9.7.x at scaffold time.

## 10. Ktor client 3.5.2 — [OK]

- **3.5.2 is the latest** (Maven Central, lastUpdated 2026-07-31). Coroutine-native, kotlinx-serialization integration — sound fit for the data/remote layer on Kotlin 2.4.20 (library metadata backwards-compatible).

## 11. Raspberry Pi OS Bookworm arm64 + systemd — [OK]

- Bookworm images remain current/supported; the choice predates the Trixie-based release and is deliberately conservative. systemd (252) supports system units and `Wants=network-online.target` — matches the deployment block.

---

## Verdict summary

| Item | Verdict |
| --- | --- |
| asyncssh 2.24.0 + SFTP append + exec + host fingerprint | [OK] (verified at source level in the wheel) |
| aiosqlite 0.22.x async fit | [OK] |
| ping non-root ICMP + /proc/net/arp no-root read (Bookworm) | [OK] (man page + empirical) |
| EncryptedSharedPreferences deprecated; Keystore+DataStore | [OK] (official 1.1.0 notes) |
| Compose BOM 2026.09.00 (M3 1.4.0) | [OK] (exists; POM resolved, M3 1.4.0 confirmed) |
| M3 dynamic colors 12+ / fallback <12 | [OK] (AAR class-level verified) |
| Python 3.14.7 / FastAPI 0.141.1 / uv 0.12.13 | [OK] |
| Kotlin 2.4.20 / AGP 9.4.0 / Ktor 3.5.2 / security-crypto 1.1.0 / DataStore 1.2.1 / targetSdk 36 | [OK] |
| Gradle 9.6.x | [OK] ⚠ one minor behind current 9.7.1 |

**Overall: the Stack section is trustworthy.** Every pinned version was confirmed as existing current-stable against live registries, and the riskiest claims (asyncssh API surface, EncryptedSharedPreferences deprecation, unprivileged ping/ARP, BOM→M3 mapping) were verified beyond search results — asyncssh at wheel source level, ping//proc/net/arp empirically, and the BOM POM resolved. The only items worth touching at scaffold time: Gradle 9.6.x → 9.7.x and making explicit that Python 3.14 is uv-managed on Bookworm (system Python is 3.11).
