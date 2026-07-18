# The "Protect Every Device" Checklist

No app blocks *all* malware. Attackers get in through **out-of-date software,
reused passwords, and tricking people** far more often than through exotic
viruses. This checklist closes those doors on every device you own. Work top to
bottom — the first section stops the most attacks for the least effort.

Legend: ☐ = do it once · 🔁 = keep doing it

---

## 1. The five that stop ~90% of attacks

- ☐ **Turn on automatic updates** everywhere (OS, browser, phone, apps).
  Unpatched software is the #1 way malware spreads.
- ☐ **Turn on MFA / 2FA** for email first, then banking, then everything else.
  Use an **authenticator app or hardware key** — SMS codes can be stolen.
  Email is the master key: whoever controls it can reset your other logins.
- ☐ **Use a password manager** and a **unique** password per site. Reused
  passwords mean one leak unlocks everything.
- ☐ **Back up your important data** (see §5). Backups are the *only* thing that
  reliably beats ransomware.
- 🔁 **Slow down on links & attachments.** Unexpected message → don't click.
  Verify with the sender through a different channel. People are the #1 way in.

---

## 2. Every computer (Windows / macOS / Linux)

- ☐ Firewall **on**.
- ☐ Full-disk encryption **on** (BitLocker / FileVault / LUKS) — protects data
  if the device is lost or stolen.
- ☐ Built-in antivirus **on** (Windows Defender is genuinely good — you do not
  need to buy a third-party AV). Keep its real-time protection enabled.
- ☐ Screen lock with a password/PIN + short auto-lock timeout.
- ☐ Use a **standard (non-admin)** account for daily use; only elevate when
  needed. This limits what malware can do if it runs.
- ☐ Remove software you don't use (less to attack, less to patch).
- ☐ Run `python3 guardian.py` in this folder to confirm the above are actually on.

## 3. Phones & tablets (iPhone / Android)

- ☐ Auto-updates on; install OS updates promptly.
- ☐ Screen lock + biometric; short auto-lock.
- ☐ Install apps **only** from the official App Store / Google Play. Don't
  sideload or jailbreak/root unless you truly know the risks.
- 🔁 Review app permissions occasionally; revoke location/mic/camera from apps
  that don't need them.
- ☐ Turn on **Find My / Find My Device** and remote wipe.
- ☐ Enable automatic cloud backup (encrypted).

## 4. Your home network

- ☐ Change the router's **default admin password**.
- ☐ Use **WPA3 or WPA2**, a strong Wi-Fi password, and update router firmware.
- ☐ Put smart-home/IoT gadgets on a **guest network**, away from your computers.
- ☐ **Never** expose Remote Desktop (3389), VNC, SMB, or Telnet to the internet.
  If you need remote access, use a **VPN**.
- ☐ On public Wi-Fi, prefer a trustworthy VPN and stick to HTTPS sites.

## 5. Backups (the 3-2-1 rule)

- ☐ **3** copies of anything you can't bear to lose.
- ☐ On **2** different media (e.g. computer + external drive/cloud).
- ☐ With **1** copy **offline or offsite** — transformation-proof against
  ransomware, which encrypts everything it can reach.
- 🔁 **Test a restore** occasionally. A backup you've never restored is a guess.

## 6. Accounts & identity

- 🔁 Check your email/passwords against known breaches; rotate anything exposed.
- ☐ Set up account **recovery** (backup codes, recovery email/phone) and store
  the codes offline.
- ☐ Freeze your credit if identity theft is a concern (region-dependent).
- 🔁 Be alert to **social engineering**: no real bank/company asks for your
  password, MFA code, or remote access. Hang up and call the official number.

---

## If you think you're already infected

1. **Disconnect from the internet** (unplug Ethernet / turn off Wi-Fi) to stop
   data theft and spread.
2. Don't log into banking from the suspect device.
3. From a **different, clean device**, change passwords for important accounts
   (email first) and confirm MFA is on.
4. Run a full scan with your built-in AV (Windows Defender Offline Scan is
   strong). Consider a reputable second-opinion scanner.
5. If it's ransomware or you can't clean it: **wipe and reinstall the OS**, then
   restore data from a *known-good* backup. Don't pay ransoms.
6. Serious case (business, finances, extortion)? Get professional help and, where
   appropriate, report it to your national cybercrime authority.

---

*This checklist is general guidance, not a guarantee. Security is ongoing, not a
one-time setup — revisit it a couple of times a year.*
