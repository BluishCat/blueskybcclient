"""Regenerate THIRD-PARTY-NOTICES.txt from the PyInstaller build of this checkout.

Run from the repository root with the project's venv python (so that
importlib.metadata sees the distributions that were bundled), after building:

    .venv\\Scripts\\python.exe -m PyInstaller Poisora.spec --noconfirm
    .venv\\Scripts\\python.exe scripts\\gen_notices.py

- Section 1/3 (Python packages and their license texts) are rebuilt from the
  modules actually listed in build/Poisora/PYZ-00.toc and PKG-00.toc.
- The header, section 2 (runtime / native libraries), curated license labels
  and license texts for packages that ship none (e.g. pygame) are carried over
  from the previous THIRD-PARTY-NOTICES.txt, so edit that file by hand when
  section 2 needs to change.
"""
import argparse, re, sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from utils.version import __version__  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("--previous", default=str(ROOT / "THIRD-PARTY-NOTICES.txt"))
ap.add_argument("--toc-dir", default=str(ROOT / "build" / "Poisora"))
ap.add_argument("--version", default=__version__)
ap.add_argument("--out", default=str(ROOT / "THIRD-PARTY-NOTICES.txt"))
args = ap.parse_args()
old_path, toc_dir, version, out_path = args.previous, args.toc_dir, args.version, args.out
old = Path(old_path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
if not (Path(toc_dir) / "PYZ-00.toc").exists():
    sys.exit(f"{toc_dir}/PYZ-00.toc not found; build the exe with PyInstaller first")

# --- modules actually bundled ---------------------------------------------
mods = set()
for toc in ("PYZ-00.toc", "PKG-00.toc"):
    p = Path(toc_dir) / toc
    if not p.exists():
        continue
    txt = p.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"\(\s*'([^']+)'\s*,\s*'([^']*)'", txt):
        name, src = m.group(1), m.group(2)
        mods.add(name.split(".")[0].split("\\")[0].split("/")[0])
        if "site-packages" in src:
            part = re.split(r"site-packages[\\/]+", src)[1]
            mods.add(re.split(r"[\\/]", part)[0].split(".")[0])

pkg2dist = metadata.packages_distributions()
BUILD_ONLY = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pip"}
dists = {}
for mod in mods:
    for d in pkg2dist.get(mod, []):
        if d.lower() in BUILD_ONLY:
            continue
        dists[d.lower()] = metadata.distribution(d)

# --- curated labels/urls from previous notices -----------------------------
old_label, old_url = {}, {}
for m in re.finditer(r"^-{80}\n(\S+) \S+  --  (.+?)(?:  \[[^\]]+\])?\n配布元: (.+)$", old, re.M):
    old_label[m.group(1).lower()] = m.group(2)
    old_url[m.group(1).lower()] = m.group(3)


def license_label(dist):
    md = dist.metadata
    name = md["Name"].lower()
    if name in old_label:
        return old_label[name]
    if md.get("License-Expression"):
        return md["License-Expression"]
    lic = md.get("License") or ""
    if lic and len(lic) < 60 and "\n" not in lic:
        return lic
    cls = [c.split(" :: ")[-1] for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    return " / ".join(cls) or "UNKNOWN"


def home_url(dist):
    md = dist.metadata
    name = md["Name"].lower()
    for pu in md.get_all("Project-URL") or []:
        k, _, v = pu.partition(",")
        if k.strip().lower() in ("source", "source code", "repository", "homepage", "home", "github"):
            return v.strip()
    return md.get("Home-page") or old_url.get(name) or f"https://pypi.org/project/{md['Name']}/"


def license_files(dist):
    files = []
    for f in dist.files or []:
        parts = [p.lower() for p in f.parts]
        base = parts[-1]
        in_meta = parts[0].endswith(".dist-info")
        if in_meta and (len(parts) > 2 and parts[1] == "licenses" or re.match(r"(licen[cs]e|copying|notice|authors)", base)):
            files.append(f)
    out = []
    for f in sorted(files, key=lambda f: str(f)):
        try:
            out.append((f.name, f.read_text(encoding="utf-8").replace("\r\n", "\n").strip("\n")))
        except Exception:
            pass
    return out


ordered = sorted(dists.values(), key=lambda d: d.metadata["Name"].lower())

# --- header + section 2 copied from previous notices -----------------------
head, rest = old.split("\n================================================================================\n 1. Python パッケージ", 1)
head = re.sub(r"^ Poisora v[0-9.]+$", f" Poisora v{version}", head, count=1, flags=re.M)
sec2 = "================================================================================\n 2." + rest.split("\n================================================================================\n 2.", 1)[1]
sec2 = sec2.split("\n================================================================================\n 3.", 1)[0]

lines = [head.rstrip("\n"), "", "", "=" * 80, " 1. Python パッケージ", "=" * 80, "",
         "  Package             Version       License",
         "  ------------------  ------------  ----------------------------------"]
for d in ordered:
    lines.append(f"  {d.metadata['Name']:<18}  {d.version:<12}  {license_label(d)}")
lines += ["", sec2.rstrip("\n"), "", "=" * 80, " 3. ライセンス全文 (Python パッケージ)", "=" * 80, ""]

missing = []
for d in ordered:
    name, ver, lab, url = d.metadata["Name"], d.version, license_label(d), home_url(d)
    lf = license_files(d)
    if not lf:
        # Reuse the full text from the previous notices (e.g. pygame ships no license file)
        m = re.search(r"^-{80}\n" + re.escape(name) + r" \S+  --  [^\n]*\n[^\n]*\n-{80}\n\n(.*?)(?=\n\n\n-{80}\n|\Z)", old, re.S | re.M)
        if m:
            lf = [(None, m.group(1).rstrip("\n"))]
    if not lf:
        missing.append(name)
        lf = [(None, f"(ライセンス本文はパッケージに同梱されていません。{url} を参照してください)")]
    for fname, text in lf:
        tag = f"  [{fname}]" if len(lf) > 1 and fname else ""
        lines += ["-" * 80, f"{name} {ver}  --  {lab}{tag}", f"配布元: {url}", "-" * 80, "", text, "", ""]

out = "\n".join(lines).rstrip("\n") + "\n"
out = out.replace("  --  MIT License\n", "  --  MIT\n").replace("         MIT License\n", "         MIT\n")
Path(out_path).write_bytes(("\ufeff" + out.replace("\r\n", "\n").replace("\n", "\r\n")).encode("utf-8"))
print("packages:", len(ordered))
print(", ".join(f"{d.metadata['Name']}={d.version}" for d in ordered))
print("no license file:", missing)
