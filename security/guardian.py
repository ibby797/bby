#!/usr/bin/env python3
"""
Security Guardian — a read-only security posture auditor.

It checks whether the real, battle-tested defenses on your computer are
actually switched on (firewall, disk encryption, built-in antivirus,
automatic updates, screen lock, etc.) and tells you exactly what to fix.

Design principles:
  * READ-ONLY. It never changes a single setting. It only *looks*.
  * No admin/root required. If a check needs privileges it can't get, it
    reports "MANUAL" instead of guessing or failing.
  * Zero third-party dependencies. Pure Python standard library so it runs
    anywhere Python 3.8+ runs.
  * Honest. It never claims to make you "100% safe". It reports facts and
    points you at the fix.

Usage:
    python3 guardian.py             # human-readable report
    python3 guardian.py --json      # machine-readable JSON
    python3 guardian.py --no-color  # disable ANSI colors

There is NO version of a program that blocks "all" malware. Security is
layered: keep software updated, use a password manager + MFA, back up your
data, and stay skeptical of links and attachments. This tool audits the
layers a computer can check for you; checklist.md covers the rest.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Callable, Optional


# --------------------------------------------------------------------------- #
# Result model
# --------------------------------------------------------------------------- #
class Status(str, Enum):
    PASS = "PASS"      # protection confirmed on
    WARN = "WARN"      # weak or partially configured
    FAIL = "FAIL"      # protection confirmed off / missing
    MANUAL = "MANUAL"  # can't be auto-checked; verify yourself
    INFO = "INFO"      # informational, not scored


# Weight each status contributes toward the posture score (0..1 of "good").
_SCORE = {
    Status.PASS: 1.0,
    Status.WARN: 0.5,
    Status.FAIL: 0.0,
    Status.MANUAL: None,  # excluded from score
    Status.INFO: None,    # excluded from score
}


@dataclass
class Result:
    name: str
    status: Status
    detail: str = ""
    remediation: str = ""
    section: str = "General"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


# --------------------------------------------------------------------------- #
# Safe command helper — read-only, timed out, never raises
# --------------------------------------------------------------------------- #
def run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    """Run a read-only command. Returns (returncode, stdout, stderr).

    Never raises. Returns rc=127 if the program isn't installed, rc=124 on
    timeout. We only ever invoke *status/query* commands here — nothing that
    modifies the system.
    """
    if shutil.which(cmd[0]) is None and not os.path.isabs(cmd[0]):
        return 127, "", f"{cmd[0]}: not found"
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out after {timeout}s"
    except Exception as exc:  # pragma: no cover - defensive catch-all
        return 1, "", f"{cmd[0]}: {exc}"


def _has(binary: str) -> bool:
    return shutil.which(binary) is not None


# --------------------------------------------------------------------------- #
# Cross-platform checks
# --------------------------------------------------------------------------- #
COMMON_MALWARE_PORTS = {
    23: "Telnet (unencrypted remote shell)",
    2323: "Telnet alt (common IoT botnet target)",
    3389: "RDP (Remote Desktop) — heavily attacked if internet-exposed",
    5900: "VNC remote desktop",
    445: "SMB file sharing — WannaCry/EternalBlue vector",
}


def check_listening_ports() -> list[Result]:
    """Flag network services listening on all interfaces.

    Cross-platform via Python sockets is not possible without scanning, so we
    parse `ss`/`netstat`. Every open port is attack surface. We only warn on
    ports bound to non-loopback addresses.
    """
    section = "Network exposure"
    text = ""
    for cmd in (["ss", "-tlnH"], ["ss", "-tln"], ["netstat", "-tln"], ["netstat", "-an"]):
        rc, out, _ = run(cmd)
        if rc == 0 and out:
            text = out
            break
    if not text:
        return [Result(
            "Listening network ports", Status.MANUAL,
            "Could not enumerate ports (ss/netstat unavailable).",
            "On your OS, list listening services and disable any you don't need.",
            section,
        )]

    exposed: list[str] = []
    risky: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        for token in parts:
            # look for host:port with a real (non-loopback, non-wildcard-only) bind
            if ":" not in token:
                continue
            host, _, port = token.rpartition(":")
            if not port.isdigit():
                continue
            host = host.strip("[]")
            if host in ("", "*"):
                host = "0.0.0.0"
            pnum = int(port)
            loopback = host.startswith("127.") or host == "::1"
            if not loopback and host in ("0.0.0.0", "::", "0"):
                entry = f"port {pnum}"
                if pnum in COMMON_MALWARE_PORTS:
                    risky.append(f"{entry} — {COMMON_MALWARE_PORTS[pnum]}")
                else:
                    exposed.append(entry)
            break

    results = []
    if risky:
        results.append(Result(
            "High-risk listening ports", Status.FAIL,
            "Exposed to the network: " + "; ".join(sorted(set(risky))),
            "Turn off these services unless you truly need them, and never expose "
            "them directly to the internet — put them behind a VPN.",
            section,
        ))
    if exposed:
        results.append(Result(
            "Other listening ports", Status.WARN,
            "Bound to all interfaces: " + ", ".join(sorted(set(exposed))[:12]),
            "Confirm each is a service you intend to run. Bind local-only "
            "services to 127.0.0.1 and close what you don't use.",
            section,
        ))
    if not results:
        results.append(Result(
            "Listening network ports", Status.PASS,
            "No services found listening on public interfaces.",
            "", section,
        ))
    return results


def check_hosts_file() -> list[Result]:
    """A tampered hosts file is a classic malware/adware technique."""
    section = "Integrity"
    path = (
        r"C:\Windows\System32\drivers\etc\hosts"
        if platform.system() == "Windows"
        else "/etc/hosts"
    )
    try:
        with open(path, "r", errors="replace") as fh:
            lines = fh.readlines()
    except Exception as exc:
        return [Result("Hosts file", Status.MANUAL,
                       f"Could not read {path}: {exc}", "", section)]

    suspicious = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) < 2:
            continue
        ip, names = parts[0], parts[1:]
        # Redirecting well-known domains to a non-local IP is a red flag.
        local_ip = ip in ("127.0.0.1", "::1", "0.0.0.0", "255.255.255.255")
        for name in names:
            n = name.lower()
            hot = any(k in n for k in (
                "google", "microsoft", "apple", "windowsupdate", "bank",
                "paypal", "amazon", "facebook", "cloudflare", "mozilla",
            ))
            if hot and not local_ip:
                suspicious.append(f"{ip} -> {name}")

    if suspicious:
        return [Result(
            "Hosts file integrity", Status.FAIL,
            "Sensitive domains redirected to non-local IPs: " + "; ".join(suspicious[:6]),
            f"Inspect {path}. Malware/adware edits it to hijack traffic. Remove "
            "entries you didn't add.",
            section,
        )]
    return [Result("Hosts file integrity", Status.PASS,
                   "No suspicious domain redirections found.", "", section)]


def check_dns() -> list[Result]:
    """Informational: which DNS-ish resolver posture is in use."""
    section = "Network exposure"
    return [Result(
        "Encrypted DNS", Status.MANUAL,
        "Whether DNS is encrypted (DoH/DoT) can't be reliably auto-detected.",
        "Enable DNS-over-HTTPS in your browser/OS, or use a filtering resolver "
        "(e.g. a reputable public resolver) to block known-malicious domains.",
        section,
    )]


# --------------------------------------------------------------------------- #
# Linux checks
# --------------------------------------------------------------------------- #
def linux_checks() -> list[Result]:
    out: list[Result] = []
    sec = "Linux hardening"

    # Firewall
    if _has("ufw"):
        rc, o, _ = run(["ufw", "status"])
        if rc == 0 and "Status: active" in o:
            out.append(Result("Firewall (ufw)", Status.PASS, "ufw is active.", "", sec))
        elif rc == 0:
            out.append(Result("Firewall (ufw)", Status.FAIL, "ufw is installed but inactive.",
                              "Enable it: sudo ufw enable", sec))
        else:
            out.append(Result("Firewall (ufw)", Status.MANUAL,
                              "ufw present but status needs privileges.",
                              "Run: sudo ufw status", sec))
    elif _has("firewall-cmd"):
        rc, o, _ = run(["firewall-cmd", "--state"])
        st = Status.PASS if (rc == 0 and "running" in o) else Status.FAIL
        out.append(Result("Firewall (firewalld)", st, o or "not running",
                          "Enable: sudo systemctl enable --now firewalld", sec))
    else:
        rc, o, _ = run(["nft", "list", "ruleset"])
        if rc == 0 and o.strip():
            out.append(Result("Firewall (nftables)", Status.WARN,
                              "nftables rules exist; no ufw/firewalld front-end.",
                              "Confirm the ruleset denies unexpected inbound traffic.", sec))
        else:
            out.append(Result("Firewall", Status.MANUAL,
                              "No ufw/firewalld detected.",
                              "Install and enable a firewall (e.g. ufw): "
                              "sudo apt install ufw && sudo ufw enable", sec))

    # Disk encryption
    rc, o, _ = run(["lsblk", "-o", "NAME,TYPE,FSTYPE"])
    if rc == 0 and ("crypt" in o or "crypto_LUKS" in o):
        out.append(Result("Disk encryption (LUKS)", Status.PASS,
                          "Encrypted block device detected.", "", sec))
    elif os.path.exists("/etc/crypttab") and os.path.getsize("/etc/crypttab") > 0:
        out.append(Result("Disk encryption", Status.PASS,
                          "/etc/crypttab is configured.", "", sec))
    else:
        out.append(Result("Disk encryption", Status.WARN,
                          "No encrypted volume detected.",
                          "If this device can be lost/stolen, enable full-disk "
                          "encryption (LUKS). Usually chosen at install time.", sec))

    # Automatic security updates
    unattended = os.path.exists("/etc/apt/apt.conf.d/20auto-upgrades")
    dnf_auto = _has("dnf-automatic") or os.path.exists("/etc/dnf/automatic.conf")
    if unattended or dnf_auto:
        out.append(Result("Automatic updates", Status.PASS,
                          "Automatic/unattended updates appear configured.", "", sec))
    else:
        out.append(Result("Automatic updates", Status.WARN,
                          "Automatic security updates not clearly configured.",
                          "Debian/Ubuntu: sudo apt install unattended-upgrades. "
                          "Fedora: enable dnf-automatic.timer.", sec))

    # Pending updates (best-effort, read-only)
    if _has("apt"):
        rc, o, _ = run(["apt", "list", "--upgradable"], timeout=15)
        if rc == 0:
            n = max(0, len([l for l in o.splitlines() if "/" in l]) - 1)
            if n == 0:
                out.append(Result("Pending updates", Status.PASS, "System looks up to date.", "", sec))
            else:
                out.append(Result("Pending updates", Status.WARN, f"{n} package(s) upgradable.",
                                  "Run: sudo apt update && sudo apt upgrade", sec))

    # ClamAV (optional on Linux, but nice to know)
    if _has("clamscan") or _has("clamdscan"):
        out.append(Result("Antivirus (ClamAV)", Status.INFO, "ClamAV is installed.", "", sec))

    return out


# --------------------------------------------------------------------------- #
# macOS checks
# --------------------------------------------------------------------------- #
def macos_checks() -> list[Result]:
    out: list[Result] = []
    sec = "macOS hardening"

    # FileVault (disk encryption)
    rc, o, _ = run(["fdesetup", "status"])
    if rc == 0:
        st = Status.PASS if "On" in o else Status.FAIL
        out.append(Result("Disk encryption (FileVault)", st, o,
                          "Turn on: System Settings > Privacy & Security > FileVault.", sec))

    # Application firewall
    rc, o, _ = run(["defaults", "read", "/Library/Preferences/com.apple.alf", "globalstate"])
    if rc == 0 and o.strip() in ("1", "2"):
        out.append(Result("Application firewall", Status.PASS, "Firewall is enabled.", "", sec))
    elif rc == 0:
        out.append(Result("Application firewall", Status.FAIL, "Firewall is off.",
                          "Enable: System Settings > Network > Firewall.", sec))

    # Gatekeeper
    rc, o, _ = run(["spctl", "--status"])
    if rc == 0:
        st = Status.PASS if "enabled" in o else Status.FAIL
        out.append(Result("Gatekeeper (app signing)", st, o or "unknown",
                          "Keep Gatekeeper enabled so only trusted apps run.", sec))

    # System Integrity Protection
    rc, o, _ = run(["csrutil", "status"])
    if rc == 0:
        st = Status.PASS if "enabled" in o else Status.FAIL
        out.append(Result("System Integrity Protection", st, o,
                          "Re-enable SIP from Recovery if it is disabled.", sec))

    # Automatic updates
    rc, o, _ = run(["defaults", "read", "/Library/Preferences/com.apple.SoftwareUpdate",
                    "AutomaticCheckEnabled"])
    if rc == 0:
        st = Status.PASS if o.strip() == "1" else Status.WARN
        out.append(Result("Automatic updates", st,
                          "enabled" if st is Status.PASS else "disabled",
                          "Turn on automatic updates in System Settings > General "
                          "> Software Update.", sec))
    return out


# --------------------------------------------------------------------------- #
# Windows checks
# --------------------------------------------------------------------------- #
def _ps(script: str, timeout: float = 20.0) -> tuple[int, str, str]:
    return run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout)


def windows_checks() -> list[Result]:
    out: list[Result] = []
    sec = "Windows hardening"

    # Microsoft Defender status
    rc, o, _ = _ps(
        "$s=Get-MpComputerStatus; "
        "'{0}|{1}|{2}' -f $s.RealTimeProtectionEnabled,"
        "$s.AntivirusSignatureAge,$s.AMServiceEnabled"
    )
    if rc == 0 and "|" in o:
        rtp, sigage, svc = (o.split("|") + ["", "", ""])[:3]
        on = rtp.strip().lower() == "true"
        out.append(Result("Antivirus (Defender real-time)",
                          Status.PASS if on else Status.FAIL,
                          f"RealTimeProtection={rtp}, service={svc}",
                          "Turn on real-time protection in Windows Security > "
                          "Virus & threat protection.", sec))
        try:
            if int(float(sigage)) > 3:
                out.append(Result("Antivirus signatures", Status.WARN,
                                  f"Definitions are {sigage} days old.",
                                  "Update in Windows Security or run "
                                  "'Update-MpSignature'.", sec))
        except ValueError:
            pass

    # Firewall (all profiles)
    rc, o, _ = _ps(
        "(Get-NetFirewallProfile | ForEach-Object "
        "{ '{0}={1}' -f $_.Name,$_.Enabled }) -join ';'"
    )
    if rc == 0 and o:
        all_on = "False" not in o
        out.append(Result("Firewall (all profiles)",
                          Status.PASS if all_on else Status.FAIL, o,
                          "Enable the firewall for Domain, Private, and Public "
                          "profiles in Windows Security.", sec))

    # BitLocker
    rc, o, _ = _ps(
        "(Get-BitLockerVolume -MountPoint $env:SystemDrive)."
        "ProtectionStatus"
    )
    if rc == 0 and o:
        on = o.strip() in ("On", "1")
        out.append(Result("Disk encryption (BitLocker)",
                          Status.PASS if on else Status.WARN,
                          f"System drive protection: {o}",
                          "Turn on BitLocker (Windows Pro) or Device Encryption "
                          "(Windows Home) in Settings > Privacy & security.", sec))

    # Automatic updates service
    rc, o, _ = _ps("(Get-Service wuauserv).StartType")
    if rc == 0 and o:
        st = Status.WARN if o.strip().lower() == "disabled" else Status.PASS
        out.append(Result("Automatic updates", st, f"Windows Update start type: {o}",
                          "Keep Windows Update enabled and install updates promptly.", sec))
    return out


# --------------------------------------------------------------------------- #
# Universal advisory checks (things software can't verify for you)
# --------------------------------------------------------------------------- #
def advisory_checks() -> list[Result]:
    sec = "Habits (verify yourself)"
    items = [
        ("Multi-factor authentication (MFA)",
         "Turn on MFA/2FA for email, banking, and social accounts. It stops the "
         "vast majority of account takeovers even if your password leaks. Prefer "
         "an authenticator app or hardware key over SMS."),
        ("Password manager + unique passwords",
         "Use a reputable password manager and a different strong password for "
         "every site. Reused passwords are the #1 cause of account compromise."),
        ("Backups (3-2-1 rule)",
         "Keep 3 copies of important data, on 2 types of media, with 1 offline/"
         "offsite. Good backups are your only real defense against ransomware."),
        ("Phishing skepticism",
         "Don't click unexpected links or open unexpected attachments. Verify the "
         "sender out-of-band. Most malware arrives through people, not exploits."),
        ("Software from trusted sources only",
         "Install apps only from official stores/vendor sites. Avoid pirated "
         "software and 'cracks' — they are a top malware delivery method."),
        ("Screen lock + auto-lock",
         "Require a PIN/password/biometric and set a short auto-lock timeout on "
         "every device, phone included."),
    ]
    return [Result(name, Status.MANUAL, "Human check.", advice, sec)
            for name, advice in items]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def gather(sections: Optional[set[str]] = None) -> list[Result]:
    system = platform.system()
    results: list[Result] = []

    results += check_listening_ports()
    results += check_hosts_file()
    results += check_dns()

    if system == "Linux":
        results += linux_checks()
    elif system == "Darwin":
        results += macos_checks()
    elif system == "Windows":
        results += windows_checks()
    else:
        results.append(Result("Platform", Status.INFO,
                              f"Unrecognized platform '{system}'; ran generic checks only.",
                              "", "General"))

    results += advisory_checks()

    if sections:
        results = [r for r in results if r.section in sections]
    return results


def score(results: list[Result]) -> tuple[float, int, int, int]:
    """Return (percentage, n_pass, n_warn, n_fail) over scored checks."""
    vals = [_SCORE[r.status] for r in results if _SCORE[r.status] is not None]
    n_pass = sum(1 for r in results if r.status is Status.PASS)
    n_warn = sum(1 for r in results if r.status is Status.WARN)
    n_fail = sum(1 for r in results if r.status is Status.FAIL)
    pct = (sum(vals) / len(vals) * 100.0) if vals else 0.0
    return pct, n_pass, n_warn, n_fail


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
_COLORS = {
    Status.PASS: "\033[32m",
    Status.WARN: "\033[33m",
    Status.FAIL: "\033[31m",
    Status.MANUAL: "\033[36m",
    Status.INFO: "\033[90m",
}
_RESET = "\033[0m"
_ICON = {
    Status.PASS: "OK  ",
    Status.WARN: "WARN",
    Status.FAIL: "FAIL",
    Status.MANUAL: "CHK ",
    Status.INFO: "INFO",
}


def render(results: list[Result], use_color: bool) -> str:
    def col(status: Status, text: str) -> str:
        if not use_color:
            return text
        return f"{_COLORS[status]}{text}{_RESET}"

    lines: list[str] = []
    lines.append("=" * 68)
    lines.append("  SECURITY GUARDIAN  —  local posture audit (read-only)")
    lines.append(f"  Host: {socket.gethostname()}   OS: {platform.platform()}")
    lines.append("=" * 68)

    by_section: dict[str, list[Result]] = {}
    for r in results:
        by_section.setdefault(r.section, []).append(r)

    for section, items in by_section.items():
        lines.append("")
        lines.append(f"  {section}")
        lines.append("  " + "-" * (len(section)))
        for r in items:
            tag = col(r.status, f"[{_ICON[r.status]}]")
            lines.append(f"  {tag} {r.name}")
            if r.detail:
                lines.append(f"         {r.detail}")
            if r.remediation and r.status in (Status.WARN, Status.FAIL, Status.MANUAL):
                lines.append(f"         -> {r.remediation}")

    pct, n_pass, n_warn, n_fail = score(results)
    lines.append("")
    lines.append("=" * 68)
    grade = (
        "STRONG" if pct >= 85 else
        "FAIR" if pct >= 60 else
        "NEEDS WORK"
    )
    summary = (f"  Posture score: {pct:5.1f}%  ({grade})   "
               f"OK={n_pass}  WARN={n_warn}  FAIL={n_fail}")
    if use_color:
        c = Status.PASS if pct >= 85 else Status.WARN if pct >= 60 else Status.FAIL
        summary = col(c, summary)
    lines.append(summary)
    lines.append("  Reminder: no tool blocks 'everything'. Fix FAILs first, then")
    lines.append("  work the CHK items and checklist.md. Layers beat silver bullets.")
    lines.append("=" * 68)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only security posture auditor. Never changes settings.",
    )
    parser.add_argument("--json", action="store_true", help="output machine-readable JSON")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    parser.add_argument("--section", action="append", default=None,
                        help="only run checks in this section (repeatable)")
    args = parser.parse_args(argv)

    sections = set(args.section) if args.section else None
    results = gather(sections)

    if args.json:
        pct, n_pass, n_warn, n_fail = score(results)
        payload = {
            "host": socket.gethostname(),
            "os": platform.platform(),
            "score_pct": round(pct, 1),
            "counts": {"pass": n_pass, "warn": n_warn, "fail": n_fail},
            "results": [r.to_dict() for r in results],
        }
        print(json.dumps(payload, indent=2))
    else:
        use_color = (not args.no_color) and sys.stdout.isatty() and os.name != "nt"
        print(render(results, use_color))

    # Exit code reflects severity so it's usable in scripts/CI:
    # 0 = no FAILs, 1 = at least one FAIL.
    return 1 if any(r.status is Status.FAIL for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
