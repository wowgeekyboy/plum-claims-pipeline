"""
Generate visual mock medical documents (PDFs and JPGs) for demo purposes.

These look like real Indian medical documents — prescriptions, bills, lab reports.
Used in the UI demo and for the demo video.

Run: python scripts/generate_mock_documents.py
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "mock_docs"
OUT_DIR.mkdir(exist_ok=True)


def get_font(size: int) -> ImageFont.FreeTypeFont:
    """Get a font that supports the characters we need."""
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/SFNSText.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def draw_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    font: ImageFont.FreeTypeFont,
    fill: str = "black",
) -> int:
    """Draw text at (x, y), return the new y position after the line."""
    draw.text((x, y), text, font=font, fill=fill)
    bbox = draw.textbbox((x, y), text, font=font)
    return bbox[3] + 4


def make_prescription_jpg(out_path: Path, content: dict) -> None:
    """Generate a JPG that looks like a doctor's prescription."""
    img = Image.new("RGB", (800, 1000), color="white")
    d = ImageDraw.Draw(img)

    # Border
    d.rectangle([(20, 20), (780, 980)], outline="black", width=2)

    # Header
    y = 40
    font_lg = get_font(24)
    font_md = get_font(18)
    font_sm = get_font(14)

    y = draw_text(d, content.get("doctor_name", "Dr. Arun Sharma"), 50, y, font_lg)
    y = draw_text(d, "MBBS, MD (Internal Medicine)", 50, y, font_md)
    y = draw_text(d, f"Reg. No: {content.get('doctor_registration', 'KA/45678/2015')}", 50, y, font_sm)
    y = draw_text(d, "City Medical Centre, 12 MG Road, Bengaluru", 50, y, font_sm)
    y = draw_text(d, "Ph: +91-80-XXXXXXXX", 50, y, font_sm)

    y += 20
    d.line([(50, y), (750, y)], fill="black", width=1)
    y += 20

    # Patient details
    y = draw_text(d, f"Patient: {content.get('patient_name', 'Rajesh Kumar')}", 50, y, font_md)
    y = draw_text(d, f"Date: {content.get('date', '2024-11-01')}", 50, y, font_md)
    y += 20

    # Diagnosis
    y = draw_text(d, f"Diagnosis: {content.get('diagnosis', 'Viral Fever')}", 50, y, font_md)
    y += 20

    # Medicines
    y = draw_text(d, "Rx:", 50, y, font_md)
    for i, med in enumerate(content.get("medicines", ["Paracetamol 650mg", "Vitamin C 500mg"]), 1):
        y = draw_text(d, f"  {i}. {med} — 1-1-1 x 5 days", 70, y, font_sm)

    y += 20
    if content.get("tests_ordered"):
        y = draw_text(d, "Investigations:", 50, y, font_sm)
        y = draw_text(d, ", ".join(content["tests_ordered"]), 50, y, font_sm)

    y += 40
    y = draw_text(d, "Follow-up: After 5 days if no improvement", 50, y, font_sm)
    y += 60

    # Stamp
    d.ellipse([(550, y), (750, y + 100)], outline="red", width=3)
    y2 = y + 30
    draw_text(d, "DOCTOR'S", 580, y2, font_sm, fill="red")
    draw_text(d, "STAMP", 600, y2 + 25, font_sm, fill="red")
    draw_text(d, "[Signature]", 570, y2 + 55, font_sm, fill="red")

    img.save(out_path, "JPEG", quality=90)
    print(f"  Created: {out_path.name}")


def make_bill_jpg(out_path: Path, content: dict) -> None:
    """Generate a JPG that looks like a hospital/pharmacy bill."""
    img = Image.new("RGB", (800, 1100), color="white")
    d = ImageDraw.Draw(img)

    d.rectangle([(20, 20), (780, 1080)], outline="black", width=2)

    y = 40
    font_lg = get_font(28)
    font_md = get_font(18)
    font_sm = get_font(14)

    # Header
    y = draw_text(d, content.get("hospital_name", "CITY MEDICAL CENTRE").upper(), 50, y, font_lg)
    y = draw_text(d, "12 MG Road, Bengaluru - 560001", 50, y, font_sm)
    y = draw_text(d, "GSTIN: 29XXXXX1234X1ZX", 50, y, font_sm)
    y = draw_text(d, "Ph: 080-XXXXXXXX", 50, y, font_sm)

    y += 20
    d.line([(50, y), (750, y)], fill="black", width=2)
    y += 20

    y = draw_text(d, "BILL / RECEIPT", 50, y, font_lg)
    y = draw_text(d, f"Bill No: CMC/2024/08321   Date: {content.get('date', '2024-11-01')}", 50, y, font_md)
    y += 10
    d.line([(50, y), (750, y)], fill="black", width=1)
    y += 15

    # Patient
    y = draw_text(d, f"Patient Name: {content.get('patient_name', 'Rajesh Kumar')}", 50, y, font_md)
    y = draw_text(d, "Age/Gender: 39 / Male", 50, y, font_sm)
    y += 15

    # Line items table
    y = draw_text(d, "DESCRIPTION", 50, y, font_sm)
    draw_text(d, "QTY", 500, y, font_sm)
    draw_text(d, "RATE", 580, y, font_sm)
    draw_text(d, "AMOUNT", 680, y, font_sm)
    y += 5
    d.line([(50, y), (750, y)], fill="black", width=1)
    y += 10

    total = 0
    for li in content.get("line_items", [{"description": "Consultation Fee", "amount": 1000}]):
        desc = li.get("description", "")
        amt = li.get("amount", 0)
        qty = li.get("quantity", 1)
        rate = amt / qty if qty else amt
        y = draw_text(d, f"{desc}", 50, y, font_sm)
        draw_text(d, str(qty), 500, y, font_sm)
        draw_text(d, f"{rate:.2f}", 580, y, font_sm)
        draw_text(d, f"{amt:.2f}", 680, y, font_sm)
        total += amt
        y += 5

    y += 10
    d.line([(50, y), (750, y)], fill="black", width=1)
    y += 15
    draw_text(d, "Subtotal:", 580, y, font_md)
    draw_text(d, f"{total:.2f}", 680, y, font_md)
    y += 25
    draw_text(d, "Total Amount:", 580, y, font_lg)
    draw_text(d, f"Rs. {total:,.2f}", 670, y, font_lg)

    y += 60
    draw_text(d, "Payment Mode: Cash / UPI / Card", 50, y, font_sm)
    y += 30
    draw_text(d, "Received by: [Cashier Name]", 50, y, font_sm)
    draw_text(d, "[Cashier Stamp]", 600, y, font_sm)

    img.save(out_path, "JPEG", quality=90)
    print(f"  Created: {out_path.name}")


def make_blurry_jpg(out_path: Path) -> None:
    """Generate a JPG that looks like a blurry, unreadable bill."""
    img = Image.new("RGB", (800, 1000), color="#e0e0e0")
    d = ImageDraw.Draw(img)
    font = get_font(24)

    # Random text-like shapes (unreadable)
    import random
    random.seed(42)
    for _ in range(40):
        x = random.randint(20, 700)
        y = random.randint(20, 900)
        w = random.randint(50, 300)
        h = random.randint(8, 20)
        gray = random.randint(100, 200)
        d.rectangle([(x, y), (x + w, y + h)], fill=(gray, gray, gray))

    # Apply Gaussian blur
    from PIL import ImageFilter
    img = img.filter(ImageFilter.GaussianBlur(radius=8))
    img.save(out_path, "JPEG", quality=60)
    print(f"  Created: {out_path.name} (simulated unreadable)")


def make_lab_report_jpg(out_path: Path, content: dict) -> None:
    """Generate a JPG that looks like a lab report."""
    img = Image.new("RGB", (800, 1100), color="white")
    d = ImageDraw.Draw(img)
    d.rectangle([(20, 20), (780, 1080)], outline="black", width=2)

    y = 40
    font_lg = get_font(24)
    font_md = get_font(18)
    font_sm = get_font(14)

    y = draw_text(d, "PRECISION DIAGNOSTICS PVT LTD", 50, y, font_lg)
    y = draw_text(d, "NABL Accredited Lab   |   Lab ID: KA-NABL-1234", 50, y, font_sm)
    y = draw_text(d, "45 Jayanagar, Bengaluru   |  Ph: 080-XXXXXXXX", 50, y, font_sm)
    y += 20
    d.line([(50, y), (750, y)], fill="black", width=1)
    y += 20

    y = draw_text(d, f"Patient: {content.get('patient_name', 'Rajesh Kumar')}", 50, y, font_md)
    y = draw_text(d, "Age/Sex: 39 / Male", 50, y, font_sm)
    y = draw_text(d, f"Ref Doctor: {content.get('referring_doctor', 'Dr. Arun Sharma')}", 50, y, font_sm)
    y = draw_text(d, f"Sample Date: {content.get('date', '2024-11-01')}", 50, y, font_sm)
    y += 20

    y = draw_text(d, "TEST NAME          RESULT    UNIT    NORMAL RANGE", 50, y, font_sm)
    y += 5
    d.line([(50, y), (750, y)], fill="black", width=1)
    y += 15

    y = draw_text(d, "CBC:", 50, y, font_md)
    y = draw_text(d, "Hemoglobin         13.2      g/dL    13.0 - 17.0", 70, y, font_sm)
    y = draw_text(d, "WBC Count          9,800     /uL     4,500 - 11,000", 70, y, font_sm)
    y = draw_text(d, "Platelet Count     185,000   /uL     150,000 - 450,000", 70, y, font_sm)
    y += 20

    test_name = content.get("test_name", "Dengue NS1 Antigen")
    y = draw_text(d, f"{test_name}  NEGATIVE           -", 50, y, font_sm)
    y += 30
    y = draw_text(d, "Remarks: All values within normal range.", 50, y, font_sm)
    y += 30
    y = draw_text(d, "Dr. Meena Pillai, MD (Pathology)", 50, y, font_sm)
    y = draw_text(d, "Reg. No: KA/89012/2018", 50, y, font_sm)

    img.save(out_path, "JPEG", quality=90)
    print(f"  Created: {out_path.name}")


def main() -> None:
    print(f"Generating mock medical documents in {OUT_DIR}/")
    print()

    # TC004: Clean consultation
    make_prescription_jpg(
        OUT_DIR / "sample_prescription.jpg",
        {
            "doctor_name": "Dr. Arun Sharma",
            "doctor_registration": "KA/45678/2015",
            "patient_name": "Rajesh Kumar",
            "date": "2024-11-01",
            "diagnosis": "Viral Fever",
            "medicines": ["Paracetamol 650mg - 1-1-1 x 5 days", "Vitamin C 500mg - 0-0-1 x 7 days"],
        },
    )
    make_bill_jpg(
        OUT_DIR / "sample_hospital_bill.jpg",
        {
            "hospital_name": "City Medical Centre",
            "patient_name": "Rajesh Kumar",
            "date": "2024-11-01",
            "line_items": [
                {"description": "Consultation Fee (OPD)", "amount": 1000, "quantity": 1},
                {"description": "CBC (Complete Blood Count)", "amount": 300, "quantity": 1},
                {"description": "Dengue NS1 Antigen Test", "amount": 200, "quantity": 1},
            ],
        },
    )

    # TC002: Unreadable
    make_prescription_jpg(
        OUT_DIR / "sample_prescription_clear.jpg",
        {
            "doctor_name": "Dr. Meena Iyer",
            "patient_name": "Sneha Reddy",
            "diagnosis": "Seasonal allergies",
            "medicines": ["Cetirizine 10mg", "Montelukast 10mg"],
        },
    )
    make_blurry_jpg(OUT_DIR / "sample_bill_blurry.jpg")

    # TC003: Mismatch
    make_bill_jpg(
        OUT_DIR / "sample_bill_different_patient.jpg",
        {
            "hospital_name": "City Clinic",
            "patient_name": "Arjun Mehta",
            "line_items": [{"description": "Consultation", "amount": 1500}],
        },
    )

    # TC006: Dental
    make_bill_jpg(
        OUT_DIR / "sample_dental_bill.jpg",
        {
            "hospital_name": "Smile Dental Clinic",
            "patient_name": "Priya Singh",
            "line_items": [
                {"description": "Root Canal Treatment", "amount": 8000},
                {"description": "Teeth Whitening", "amount": 4000},
            ],
        },
    )

    # TC007: MRI
    make_lab_report_jpg(
        OUT_DIR / "sample_mri_report.jpg",
        {
            "patient_name": "Suresh Patil",
            "test_name": "MRI Lumbar Spine",
            "referring_doctor": "Dr. Venkat Rao",
            "date": "2024-11-02",
        },
    )

    # TC010: Network hospital
    make_bill_jpg(
        OUT_DIR / "sample_apollo_bill.jpg",
        {
            "hospital_name": "Apollo Hospitals",
            "patient_name": "Deepak Shah",
            "line_items": [
                {"description": "Consultation Fee", "amount": 1500},
                {"description": "Medicines", "amount": 3000},
            ],
        },
    )

    # TC012: Bariatric
    make_bill_jpg(
        OUT_DIR / "sample_bariatric_bill.jpg",
        {
            "hospital_name": "Manipal Hospitals",
            "patient_name": "Anita Desai",
            "line_items": [
                {"description": "Bariatric Consultation", "amount": 3000},
                {"description": "Personalised Diet and Nutrition Program", "amount": 5000},
            ],
        },
    )

    print()
    print("Done! Mock documents generated in mock_docs/")


if __name__ == "__main__":
    main()
