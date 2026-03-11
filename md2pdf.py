#!/usr/bin/env python3
"""
md2pdf.py — Convert Markdown files to styled PDF documents.

Uses the `markdown` library for MD→HTML conversion and `weasyprint`
for HTML→PDF rendering. Supports GitHub-flavored extensions (tables,
fenced code, TOC) and applies a clean, print-friendly stylesheet.

Usage:
    # Single file
    python3 md2pdf.py notes.md

    # Multiple files
    python3 md2pdf.py notes.md report.md design.md

    # All .md files in a directory (recursive)
    python3 md2pdf.py path/to/folder/

    # Custom output directory
    python3 md2pdf.py notes.md -o output/

    # Custom CSS stylesheet
    python3 md2pdf.py notes.md --css custom.css

Dependencies:
    pip install markdown weasyprint

Called by: CLI invocation only (standalone utility).
"""

import argparse
import sys
from pathlib import Path

import markdown
from weasyprint import HTML


# ---------------------------------------------------------------------------
# Default CSS — clean, print-friendly typography for technical Markdown docs.
# Controls page margins, font stack, code block styling, table borders, and
# heading spacing. Override with --css flag if needed.
# ---------------------------------------------------------------------------
DEFAULT_CSS = """
@page {
    size: A4;
    margin: 2cm 2.5cm;

    @bottom-center {
        content: counter(page);
        font-size: 9pt;
        color: #888;
    }
}

body {
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #222;
}

h1 {
    font-size: 22pt;
    border-bottom: 2px solid #333;
    padding-bottom: 4pt;
    margin-top: 24pt;
    margin-bottom: 12pt;
    page-break-after: avoid;
}

h2 {
    font-size: 16pt;
    border-bottom: 1px solid #999;
    padding-bottom: 3pt;
    margin-top: 20pt;
    margin-bottom: 10pt;
    page-break-after: avoid;
}

h3 {
    font-size: 13pt;
    margin-top: 16pt;
    margin-bottom: 8pt;
    page-break-after: avoid;
}

h4, h5, h6 {
    font-size: 11pt;
    font-weight: bold;
    margin-top: 12pt;
    margin-bottom: 6pt;
    page-break-after: avoid;
}

p {
    margin: 6pt 0;
    orphans: 3;
    widows: 3;
}

/* Inline code — subtle background, monospace */
code {
    font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
    font-size: 9.5pt;
    background-color: #f4f4f4;
    padding: 1pt 4pt;
    border-radius: 3pt;
}

/* Fenced code blocks — bordered box with scroll-safe wrapping */
pre {
    background-color: #f6f8fa;
    border: 1px solid #ddd;
    border-radius: 4pt;
    padding: 10pt 12pt;
    font-size: 9pt;
    line-height: 1.45;
    overflow-wrap: break-word;
    white-space: pre-wrap;
    page-break-inside: avoid;
}

pre code {
    background: none;
    padding: 0;
    font-size: inherit;
}

/* Tables — bordered, alternating row shading */
table {
    border-collapse: collapse;
    width: 100%;
    margin: 10pt 0;
    font-size: 10pt;
    page-break-inside: auto;
}

th, td {
    border: 1px solid #ccc;
    padding: 6pt 8pt;
    text-align: left;
}

th {
    background-color: #f0f0f0;
    font-weight: 600;
}

tr:nth-child(even) {
    background-color: #fafafa;
}

/* Lists */
ul, ol {
    margin: 6pt 0 6pt 20pt;
    padding: 0;
}

li {
    margin-bottom: 3pt;
}

/* Blockquotes — left-border accent */
blockquote {
    border-left: 3pt solid #ddd;
    margin: 10pt 0;
    padding: 6pt 12pt;
    color: #555;
    font-style: italic;
}

/* Horizontal rules */
hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 16pt 0;
}

/* Links — visible but not distracting in print */
a {
    color: #0366d6;
    text-decoration: none;
}

/* Images — constrained to page width */
img {
    max-width: 100%;
    height: auto;
}
"""

# ---------------------------------------------------------------------------
# Markdown extensions enabled by default. These cover the most common
# GitHub-flavored Markdown features found in technical documentation.
# ---------------------------------------------------------------------------
MD_EXTENSIONS = [
    "tables",         # Pipe-delimited tables (GFM)
    "fenced_code",    # Triple-backtick code blocks
    "codehilite",     # Syntax highlighting hooks (CSS-based)
    "toc",            # [TOC] placeholder → table of contents
    "nl2br",          # Newlines → <br> (matches GFM behavior)
    "sane_lists",     # Stricter list parsing
    "smarty",         # Smart quotes and dashes
    "meta",           # YAML-style metadata header (silently stripped)
]


def convert_md_to_pdf(md_path: Path, output_path: Path, css: str) -> None:
    """
    Convert a single Markdown file to a styled PDF.

    Parameters:
        md_path:     Path to the source .md file. Must exist and be readable.
        output_path: Path for the output .pdf file. Parent dirs created if needed.
        css:         CSS stylesheet string applied to the HTML before rendering.

    Side effects:
        - Reads md_path from disk.
        - Writes output_path to disk (creates parent directories).
        - Prints progress to stdout.

    Raises:
        FileNotFoundError: If md_path does not exist.
        OSError:           If output_path cannot be written.
    """
    # Read the raw Markdown source
    md_text = md_path.read_text(encoding="utf-8")

    # Convert Markdown → HTML fragment using enabled extensions
    html_body = markdown.markdown(md_text, extensions=MD_EXTENSIONS)

    # Wrap in a full HTML document with the stylesheet embedded.
    # The <meta charset> tag ensures weasyprint interprets unicode correctly.
    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <style>{css}</style>
</head>
<body>
{html_body}
</body>
</html>"""

    # Ensure the output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Render HTML → PDF via weasyprint. base_url set to the source file's
    # directory so relative image paths in the Markdown resolve correctly.
    HTML(string=html_doc, base_url=str(md_path.parent)).write_pdf(str(output_path))

    print(f"  ✓ {md_path} → {output_path}")


def collect_md_files(paths: list[str]) -> list[Path]:
    """
    Resolve CLI arguments to a deduplicated list of .md file paths.

    Parameters:
        paths: List of file or directory paths from the CLI. Directories
               are searched recursively for *.md files.

    Returns:
        Sorted, deduplicated list of Path objects pointing to .md files.

    Raises:
        SystemExit: If any explicit path does not exist.
    """
    md_files: set[Path] = set()

    for p_str in paths:
        p = Path(p_str).resolve()

        if p.is_dir():
            # Recursively find all .md files under the directory
            found = sorted(p.rglob("*.md"))
            if not found:
                print(f"  ⚠ No .md files found in {p}", file=sys.stderr)
            md_files.update(found)

        elif p.is_file():
            if p.suffix.lower() != ".md":
                print(f"  ⚠ Skipping non-.md file: {p}", file=sys.stderr)
                continue
            md_files.add(p)

        else:
            print(f"  ✗ Path does not exist: {p}", file=sys.stderr)
            sys.exit(1)

    return sorted(md_files)


def build_parser() -> argparse.ArgumentParser:
    """
    Construct the CLI argument parser.

    Returns:
        Configured ArgumentParser with positional inputs and optional flags.
    """
    parser = argparse.ArgumentParser(
        description="Convert Markdown files to styled PDF documents.",
        epilog="Examples:\n"
               "  python3 md2pdf.py notes.md\n"
               "  python3 md2pdf.py docs/ -o output/\n"
               "  python3 md2pdf.py *.md --css custom.css\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Positional: one or more files or directories
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Markdown files or directories to convert (directories searched recursively).",
    )
    # Optional: output directory (default: same directory as each source file)
    parser.add_argument(
        "-o", "--output-dir",
        default=None,
        help="Directory for output PDFs. Default: same directory as each .md file.",
    )
    # Optional: custom CSS file
    parser.add_argument(
        "--css",
        default=None,
        help="Path to a custom CSS file for PDF styling.",
    )
    return parser


def main() -> None:
    """
    Entry point. Parses CLI args, resolves input files, loads CSS, and
    converts each Markdown file to PDF.

    Side effects:
        - Reads input files and optional CSS file from disk.
        - Writes PDF files to disk.
        - Prints progress/error messages to stdout/stderr.
        - Calls sys.exit on fatal errors.
    """
    parser = build_parser()
    args = parser.parse_args()

    # Resolve all input paths to .md files
    md_files = collect_md_files(args.inputs)
    if not md_files:
        print("No Markdown files to convert.", file=sys.stderr)
        sys.exit(1)

    # Load CSS — custom file if specified, otherwise built-in stylesheet
    if args.css:
        css_path = Path(args.css)
        if not css_path.is_file():
            print(f"CSS file not found: {css_path}", file=sys.stderr)
            sys.exit(1)
        css = css_path.read_text(encoding="utf-8")
    else:
        css = DEFAULT_CSS

    # Resolve output directory (None means co-located with source)
    out_dir = Path(args.output_dir).resolve() if args.output_dir else None

    print(f"Converting {len(md_files)} file(s)...\n")

    # Track success/failure counts for the summary
    success = 0
    failed = 0

    for md_file in md_files:
        try:
            if out_dir:
                # Place PDF in the output directory, preserving just the filename
                pdf_path = out_dir / md_file.with_suffix(".pdf").name
            else:
                # Place PDF alongside the source .md file
                pdf_path = md_file.with_suffix(".pdf")

            convert_md_to_pdf(md_file, pdf_path, css)
            success += 1

        except Exception as e:
            print(f"  ✗ {md_file}: {e}", file=sys.stderr)
            failed += 1

    # Print summary
    print(f"\nDone: {success} converted, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
