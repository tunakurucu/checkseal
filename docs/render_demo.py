"""Regenerate assets from actual CLI runs. Dev-only dependency: Pillow."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "checkseal.py"
ASSETS = ROOT / "assets"


def collect():
    reports = []
    with tempfile.TemporaryDirectory(prefix="checkseal-visual-demo-") as temp:
        root = Path(temp)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / ".gitignore").write_text("__pycache__/\n")
        (root / "price.py").write_text("def total(price, quantity):\n    return price * quantity\n")
        (root / "test_price.py").write_text("from price import total\nassert total(7, 3) == 21\nprint('1 test passed')\n")
        def cli(*args):
            return subprocess.run([sys.executable, "-B", str(CLI), *args], cwd=root, capture_output=True, text=True, timeout=30)
        def report():
            result = cli("check", "--format", "json")
            return json.loads(result.stdout)
        assert cli("run", "test", "--", "python3", "-B", "test_price.py").returncode == 0
        reports.append(report())
        (root / "price.py").write_text("def total(price, quantity):\n    return price + quantity\n")
        reports.append(report())
        html = cli("check", "--format", "html").stdout
        (ASSETS / "example-report.html").write_text(html, encoding="utf-8")
        assert cli("run", "test", "--", "python3", "-B", "test_price.py").returncode == 1
        reports.append(report())
    assert [r["checks"][0]["status"] for r in reports] == ["FRESH", "STALE", "FAILED"]
    return reports


def main():
    ASSETS.mkdir(exist_ok=True)
    reports = collect()
    font_root = Path("/usr/share/fonts/truetype/dejavu")
    def font(size, mono=False, bold=False):
        name = "DejaVuSans" + ("Mono" if mono else "") + ("-Bold" if bold else "") + ".ttf"
        return ImageFont.truetype(str(font_root / name), size)
    white, muted, green, amber, red = "#edf3fa", "#a6b5cc", "#84dfbd", "#ffc17b", "#ff8a93"
    frames = []
    titles = ["Tests pass.", "Then the code changes.", "Rerun. Catch the bug."]
    subtitles = ["Record the result against this version of your files.", "The old pass no longer applies to this working tree.", "Fresh evidence starts with actually running the check."]
    for i, report in enumerate(reports):
        row = report["checks"][0]
        color = [green, amber, red][i]
        im = Image.new("RGB", (1200, 680), "#0b1220")
        d = ImageDraw.Draw(im)
        d.text((62, 36), "CHECKSEAL", fill=green, font=font(19, bold=True))
        d.text((1060, 36), f"0{i+1} / 03", fill=muted, font=font(17, mono=True))
        d.text((60, 92), titles[i], fill=white, font=font(49, bold=True))
        d.text((62, 163), subtitles[i], fill=muted, font=font(22))
        d.rounded_rectangle((60, 225, 1140, 555), 20, fill="#141f31", outline="#2b3548", width=2)
        for x, c in zip([89, 112, 135], ["#ff8a93", "#ffc17b", "#84dfbd"]):
            d.ellipse((x, 246, x+11, 257), fill=c)
        d.text((175, 240), "temporary demo repository / actual CLI results", fill=muted, font=font(17, mono=True))
        command = "$ checkseal check" if i == 1 else "$ checkseal run test -- python3 -B test_price.py"
        d.text((86, 290), command, fill=white, font=font(22, mono=True))
        d.text((86, 347), "test", fill=white, font=font(27, mono=True))
        d.text((226, 347), row["status"], fill=color, font=font(27, mono=True, bold=True))
        d.text((86, 408), row["reason"], fill=muted, font=font(21, mono=True))
        detail = "changed: price.py" if i == 1 else ("exit code: 0" if i == 0 else "exit code: 1")
        d.text((86, 454), detail, fill=color, font=font(21, mono=True))
        d.text((62, 585), "Do those passing tests still apply to this code?", fill=white, font=font(25, bold=True))
        d.text((62, 630), "Python + Git  /  MIT  /  No third-party runtime dependencies", fill=muted, font=font(18))
        frames.append(im)
    frames[0].save(ASSETS / "demo.gif", save_all=True, append_images=frames[1:], duration=[2600,3400,2800], loop=0, optimize=True)
    frames[1].save(ASSETS / "social-preview.png")
    print("Generated demo.gif, social-preview.png and example-report.html from observed results.")


if __name__ == "__main__":
    main()
