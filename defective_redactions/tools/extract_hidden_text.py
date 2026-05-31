#!/usr/bin/env python3
"""
Simple tool to extract hidden text from PDFs with faulty redactions.

Usage:
    python extract_hidden_text.py document.pdf
    python extract_hidden_text.py document.pdf --pages 1-10
    python extract_hidden_text.py document.pdf --json > output.json

Requirements:
    pip install pypdf
"""
import argparse
import json
import sys
from pathlib import Path

def extract_hidden_text(pdf_path, pages="all"):
    """Extract text hidden under black rectangles in a PDF."""
    try:
        import pypdf
    except ImportError:
        print("Error: pypdf not installed. Run: pip install pypdf")
        sys.exit(1)

    try:
        reader = pypdf.PdfReader(str(pdf_path))
    except Exception as e:
        return {"error": f"Could not open PDF: {e}"}

    total_pages = len(reader.pages)

    # Parse page range
    if pages == "all":
        page_nums = list(range(total_pages))
    else:
        page_nums = []
        for part in pages.split(","):
            if "-" in part:
                start, end = part.split("-")
                page_nums.extend(range(int(start)-1, int(end)))
            else:
                page_nums.append(int(part)-1)

    results = {}

    for page_num in page_nums:
        if page_num >= total_pages or page_num < 0:
            continue

        page = reader.pages[page_num]
        hidden_text = extract_page_hidden_text(page)

        if hidden_text:
            results[page_num + 1] = {
                "fragments": len(hidden_text),
                "text": " ".join(t.strip() for t in hidden_text if t.strip())
            }

    return results

def extract_page_hidden_text(page):
    """Extract text that's hidden under black rectangles on a single page."""
    try:
        content_stream = page.get_contents()
        if not content_stream:
            return []

        operations = content_stream.operations
    except Exception:
        return []

    # Track graphics state
    text_matrix = [1, 0, 0, 1, 0, 0]  # Identity matrix
    ctm = [1, 0, 0, 1, 0, 0]  # Current transformation matrix
    graphics_stack = []

    # Track colors and rectangles
    current_fill_color = None  # (r, g, b) or gray value
    text_operations = []  # [(x, y, text), ...]
    black_rectangles = []  # [(x1, y1, x2, y2), ...]

    # Current path rectangles (before paint operation)
    path_rectangles = []

    def is_black_color():
        """Check if current fill color is black or very dark."""
        if current_fill_color is None:
            return True  # PDF default is black
        if isinstance(current_fill_color, tuple):
            # RGB color
            r, g, b = current_fill_color[:3]
            return r < 0.2 and g < 0.2 and b < 0.2
        else:
            # Gray color
            return current_fill_color < 0.2

    def matrix_transform(matrix, x, y):
        """Apply transformation matrix to point."""
        a, b, c, d, e, f = matrix
        return a*x + c*y + e, b*x + d*y + f

    def decode_text(text_arg):
        """Decode PDF text string."""
        if isinstance(text_arg, bytes):
            return text_arg.decode('latin-1', errors='replace')
        return str(text_arg)

    # Process each operation in the content stream
    for args, op in operations:
        op_str = op.decode('latin-1') if isinstance(op, bytes) else str(op)

        # Graphics state stack
        if op_str == 'q':  # Save state
            graphics_stack.append((ctm[:], current_fill_color))
        elif op_str == 'Q':  # Restore state
            if graphics_stack:
                ctm, current_fill_color = graphics_stack.pop()

        # Transformation matrix
        elif op_str == 'cm':  # Concatenate matrix
            if len(args) >= 6:
                try:
                    new_matrix = [float(a) for a in args[:6]]
                    # Matrix multiplication: ctm = new_matrix * ctm
                    a1, b1, c1, d1, e1, f1 = new_matrix
                    a2, b2, c2, d2, e2, f2 = ctm
                    ctm = [
                        a1*a2 + b1*c2, a1*b2 + b1*d2,
                        c1*a2 + d1*c2, c1*b2 + d1*d2,
                        e1*a2 + f1*c2 + e2, e1*b2 + f1*d2 + f2
                    ]
                except:
                    pass

        # Colors
        elif op_str == 'rg':  # RGB nonstroking color
            if len(args) >= 3:
                try:
                    current_fill_color = tuple(float(a) for a in args[:3])
                except:
                    pass
        elif op_str == 'g':  # Gray nonstroking color
            if len(args) >= 1:
                try:
                    current_fill_color = float(args[0])
                except:
                    pass

        # Text operations
        elif op_str == 'BT':  # Begin text object
            text_matrix = [1, 0, 0, 1, 0, 0]
        elif op_str == 'Tm':  # Set text matrix
            if len(args) >= 6:
                try:
                    text_matrix = [float(a) for a in args[:6]]
                except:
                    pass
        elif op_str in ('Tj', 'TJ'):  # Show text
            if args:
                try:
                    text = decode_text(args[0])
                    # Get position from text matrix transformed by CTM
                    x, y = matrix_transform(ctm, text_matrix[4], text_matrix[5])
                    text_operations.append((x, y, text))
                except:
                    pass

        # Path operations
        elif op_str == 're':  # Rectangle
            if len(args) >= 4:
                try:
                    x, y, width, height = [float(a) for a in args[:4]]
                    # Transform rectangle corners
                    x1, y1 = matrix_transform(ctm, x, y)
                    x2, y2 = matrix_transform(ctm, x + width, y + height)
                    # Normalize to min/max coordinates
                    path_rectangles.append((
                        min(x1, x2), min(y1, y2),
                        max(x1, x2), max(y1, y2)
                    ))
                except:
                    pass

        # Paint operations
        elif op_str in ('f', 'F', 'f*', 'B', 'B*', 'b', 'b*'):  # Fill operations
            if is_black_color() and path_rectangles:
                black_rectangles.extend(path_rectangles)
            path_rectangles = []

        elif op_str in ('S', 's', 'n'):  # Stroke, close, or no-op (end path)
            path_rectangles = []

    # Find text operations that intersect with black rectangles
    hidden_texts = []
    for text_x, text_y, text in text_operations:
        for rect_x1, rect_y1, rect_x2, rect_y2 in black_rectangles:
            if (rect_x1 <= text_x <= rect_x2 and
                rect_y1 <= text_y <= rect_y2):
                hidden_texts.append(text)
                break

    return hidden_texts

def main():
    parser = argparse.ArgumentParser(description="Extract hidden text from redacted PDFs")
    parser.add_argument("pdf_file", help="Path to PDF file")
    parser.add_argument("--pages", default="all",
                       help="Page range (e.g., '1-10', '5', '1,3,5-8')")
    parser.add_argument("--json", action="store_true",
                       help="Output results as JSON")

    args = parser.parse_args()

    pdf_path = Path(args.pdf_file)
    if not pdf_path.exists():
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)

    results = extract_hidden_text(pdf_path, args.pages)

    if "error" in results:
        print(f"Error: {results['error']}")
        sys.exit(1)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        if not results:
            print("No hidden text found.")
        else:
            print(f"Found hidden text on {len(results)} page(s):\n")
            for page_num, data in sorted(results.items()):
                print(f"Page {page_num} ({data['fragments']} fragments):")
                print(f"  {data['text']}")
                print()

if __name__ == "__main__":
    main()