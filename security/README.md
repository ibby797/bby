# Security Guardian

A small, honest toolkit to protect your devices from malware and cyber threats.

> **First, the truth:** there is no program — free or paid — that blocks *all*
> malware, viruses, and cyberattacks. Anything advertised that way is
> overselling. Real safety comes from **layers**: keep software updated, use MFA
> and unique passwords, back up your data, and stay skeptical of links. This
> toolkit helps you get those layers right instead of pretending one magic app
> can do it for you.

## What's here

| File | What it does |
|------|--------------|
| `guardian.py` | **Read-only** auditor. Checks whether your computer's real defenses (firewall, disk encryption, built-in antivirus, auto-updates, exposed ports, hosts-file tampering) are actually switched on, and tells you what to fix. |
| `checklist.md` | Plain-English hardening checklist covering **every device** — computers, phones, your Wi-Fi router, backups, and accounts. |

## Why not just build an antivirus?

Because you already have an excellent one, and rolling your own would make you
*less* safe. Windows Defender, macOS's built-in protections, and mainstream
mobile OSes are maintained by large security teams and updated constantly. The
smart move is to **make sure those are on and configured well** — which is
exactly what `guardian.py` checks — not to replace them with home-made code.

## Using the auditor

Requires only Python 3.8+ (no installs, no dependencies).

```bash
# From the repo root:
python3 security/guardian.py            # readable report with a posture score
python3 security/guardian.py --json     # machine-readable output
python3 security/guardian.py --no-color # plain text (good for logs)

# Focus on one area:
python3 security/guardian.py --section "Network exposure"
```

### How to read the results

| Tag | Meaning |
|-----|---------|
| `OK`   | Protection confirmed **on**. |
| `WARN` | Weak or only partly configured — worth improving. |
| `FAIL` | Protection confirmed **off** or missing — **fix this first**. |
| `CHK`  | Can't be auto-checked (or needs admin) — verify it yourself. |
| `INFO` | Just information. |

The **posture score** summarizes the checks that can be scored. Aim to clear
every `FAIL`, then work the `WARN` and `CHK` items and the checklist.

**Exit code:** `0` if no failures, `1` if any `FAIL` — so you can wire it into a
scheduled job or CI and get alerted when something drifts off.

### Is it safe to run?

Yes. It only ever runs **status/query** commands and reads a couple of standard
files (like the hosts file). It **never changes a setting, installs anything, or
sends data off your machine.** No network calls, no telemetry.

## Platform coverage

- **Windows** — Defender real-time protection & signature age, firewall
  profiles, BitLocker, Windows Update.
- **macOS** — FileVault, application firewall, Gatekeeper, System Integrity
  Protection, automatic updates.
- **Linux** — ufw/firewalld/nftables, LUKS disk encryption, unattended-upgrades,
  pending updates, ClamAV presence.
- **All platforms** — exposed listening ports (with extra warnings for
  RDP/VNC/SMB/Telnet), hosts-file integrity, and universal habit reminders
  (MFA, password manager, backups, phishing awareness).

## A realistic mental model

Think of security like protecting a house. There's no single unbreakable door.
You lock the doors (updates), add deadbolts (MFA), don't hand out keys (unique
passwords), keep a spare set safely offsite (backups), and don't buzz in
strangers (phishing awareness). This toolkit is the walk-through that confirms
each lock is actually engaged.

See `checklist.md` for the full walk-through, phones and router included.
