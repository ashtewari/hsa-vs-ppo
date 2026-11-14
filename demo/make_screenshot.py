#!/usr/bin/env python3
"""Render the demo workbook's 'Annual comparison' sheet to docs/images/demo-annual-comparison.png.

Maintainer tool (Windows + Microsoft Excel). Excel opens the demo workbook, recalculates every formula,
exports the sheet (table + chart) to a temporary PDF, and saves a recalculated copy of the workbook (formula
results cached, so file previewers show numbers) for attaching to a GitHub release. PyMuPDF then renders the
PDF page to a cropped PNG. Run demo/run_demo.py first.

Usage:  python demo/make_screenshot.py [--workbook demo/output/hsa_comparison_demo.xlsx]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKBOOK = ROOT / "demo" / "output" / "hsa_comparison_demo.xlsx"
DEFAULT_PNG = ROOT / "docs" / "images" / "demo-annual-comparison.png"

# Paths arrive through environment variables, so no quoting is needed inside the script.
EXCEL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$xl = New-Object -ComObject Excel.Application
try {
    $xl.Visible = $false
    $xl.DisplayAlerts = $false
    $wb = $xl.Workbooks.Open($env:HSA_DEMO_XLSX)
    $xl.CalculateFull()
    # Save the release copy before any page setup, so no printer settings are stored in it.
    $wb.SaveAs($env:HSA_DEMO_XLSX_OUT, 51)
    $ws = $wb.Worksheets.Item('Annual comparison')
    $bottom = $ws.ChartObjects(1).BottomRightCell.Row
    $page = $ws.PageSetup
    $page.PrintArea = '$A$1:$K$' + $bottom
    $page.Orientation = 2
    $page.Zoom = $false
    $page.FitToPagesWide = 1
    $page.FitToPagesTall = 1
    $page.PrintGridlines = $true
    $page.LeftMargin = $xl.InchesToPoints(0.25)
    $page.RightMargin = $xl.InchesToPoints(0.25)
    $page.TopMargin = $xl.InchesToPoints(0.25)
    $page.BottomMargin = $xl.InchesToPoints(0.25)
    $page.CenterFooter = ''
    $ws.ExportAsFixedFormat(0, $env:HSA_DEMO_PDF)
    $wb.Close($false)
}
finally {
    $xl.Quit()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($xl)
}
"""


def excel_export(workbook: Path, pdf: Path, recalculated: Path):
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "export.ps1"
        script.write_text(EXCEL_SCRIPT, encoding="utf-8")
        env = dict(os.environ, HSA_DEMO_XLSX=str(workbook), HSA_DEMO_PDF=str(pdf),
                   HSA_DEMO_XLSX_OUT=str(recalculated))
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                       env=env, check=True)


def sanitize_workbook(path: Path):
    """Remove what Excel's SaveAs adds about the maintainer (folder path, Office user name, company), then
    refuse the file if the user name or home folder still appears anywhere in it. It goes on a public release."""
    with zipfile.ZipFile(path) as z:
        parts = [(info, z.read(info.filename)) for info in z.infolist()]
    needles = {os.environ.get("USERNAME", ""), os.environ.get("USER", ""), Path.home().name, str(Path.home())}
    cleaned = []
    for info, data in parts:
        if info.filename in ("xl/workbook.xml", "docProps/core.xml", "docProps/app.xml"):
            s = data.decode("utf-8")
            for tag in ("cp:lastModifiedBy", "dc:creator", "Company", "Manager"):
                needles.update(re.findall(rf"<{tag}>([^<]*)</{tag}>", s))
                s = re.sub(rf"<{tag}>[^<]*</{tag}>", f"<{tag}>{'hsa-vs-ppo demo' if ':' in tag else ''}</{tag}>", s)
            s = re.sub(r"<mc:AlternateContent\b(?:(?!</mc:AlternateContent>).)*?x15ac:absPath.*?</mc:AlternateContent>",
                       "", s, flags=re.S)
            s = re.sub(r"<x15ac:absPath\b[^>]*/>", "", s)
            data = s.encode("utf-8")
        cleaned.append((info, data))
    needles = {n.casefold() for n in needles if len(n.strip()) >= 3 and n != "hsa-vs-ppo demo"}
    for info, data in cleaned:
        text = data.decode("utf-8", "ignore").casefold() + data.decode("utf-16-le", "ignore").casefold()
        if any(n in text for n in needles):
            path.unlink()
            raise SystemExit(f"Personal information left in part {info.filename}; recalculated workbook deleted.")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for info, data in cleaned:
            z.writestr(info, data)


def render_png(pdf: Path, png: Path, width_px: int = 1600, pad: float = 6):
    """Render page 1, cropped to the printed content."""
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        rects = [pymupdf.Rect(b[:4]) for b in page.get_text("blocks")]
        rects += [d["rect"] for d in page.get_drawings()]
        rects += [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
        clip = pymupdf.Rect()
        for r in rects:
            if r.is_valid and not r.is_empty:
                clip |= r
        clip = (clip + (-pad, -pad, pad, pad)) & page.rect
        zoom = width_px / clip.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        png.parent.mkdir(parents=True, exist_ok=True)
        pix.save(png)
        return pix.width, pix.height


def main() -> int:
    ap = argparse.ArgumentParser(description="Excel screenshot of the demo workbook (Windows + Excel).")
    ap.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    ap.add_argument("--png", type=Path, default=DEFAULT_PNG)
    args = ap.parse_args()
    if sys.platform != "win32":
        print("This script drives Microsoft Excel and runs on Windows only.", file=sys.stderr)
        return 1
    workbook = args.workbook.resolve()
    if not workbook.is_file():
        print(f"{workbook} not found. Run: python demo/run_demo.py", file=sys.stderr)
        return 1
    recalculated = workbook.with_name(workbook.stem + "_recalculated.xlsx")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "annual_comparison.pdf"
        excel_export(workbook, pdf, recalculated)
        w, h = render_png(pdf, args.png.resolve())
    sanitize_workbook(recalculated)
    print(f"Screenshot ({w}x{h}) -> {args.png}")
    print(f"Recalculated workbook (for the release asset) -> {recalculated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
