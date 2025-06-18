from flask import Flask, request, send_file, render_template, jsonify
import tempfile
import shutil
from pathlib import Path
import zipfile
import os
import re
import fitz  # PyMuPDF
import PyPDF2
from datetime import datetime
import json
import csv
from tqdm import tqdm
import multiprocessing
import pytesseract
from pdf2image import convert_from_path
from PIL import Image
import PIL.ImageEnhance
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lex_rank import LexRankSummarizer
import nltk
import threading

# Initialize NLTK data
try:
    nltk.download('punkt', quiet=True)
    nltk.download('punkt_tab', quiet=True)
except Exception as e:
    print(f"Failed to download NLTK tokenizers: {e}")

# Global variables for progress tracking
progress = {"total": 0, "current": 0, "message": "Processing..."}
progress_lock = threading.Lock()

# Dictionary for number words to percentages
NUMBER_WORDS = {
    'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
    'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
    'ten': '10', 'eleven': '11', 'twelve': '12', 'thirteen': '13',
    'fourteen': '14', 'fifteen': '15', 'sixteen': '16', 'seventeen': '17',
    'eighteen': '18', 'nineteen': '19', 'twenty': '20', 'thirty': '30',
    'forty': '40', 'fifty': '50', 'sixty': '60', 'seventy': '70',
    'eighty': '80', 'ninety': '90', 'hundred': '100'
}

# Month mapping
MONTH_MAP = {
    'jan': 'January', 'january': 'January', 'feb': 'February', 'february': 'February',
    'mar': 'March', 'march': 'March', 'apr': 'April', 'april': 'April', 'may': 'May',
    'jun': 'June', 'june': 'June', 'jul': 'July', 'july': 'July', 'aug': 'August',
    'august': 'August', 'sep': 'September', 'september': 'September', 'oct': 'October',
    'october': 'October', 'nov': 'November', 'november': 'November', 'dec': 'December',
    'december': 'December'
}

app = Flask(__name__)

def summarize_text(text, max_words=50):
    try:
        parser = PlaintextParser.from_string(text, Tokenizer("english"))
        summarizer = LexRankSummarizer()
        summary = summarizer(parser.document, sentences_count=1)
        summary_text = " ".join(str(sentence) for sentence in summary)
        words = summary_text.split()
        if len(words) > max_words:
            summary_text = " ".join(words[:max_words]) + "..."
        return summary_text
    except Exception as e:
        return f"Summary failed: {str(e)}"

def extract_metadata(pdf_path, output_dir):
    file_name = Path(pdf_path).stem
    authority_name = file_name
    pdf_url = str(pdf_path)
    
    adopted_pattern = re.compile(
        r"\b[Aa]dopted(?:\s*(?:by|on|\())?\s*(?:(?:(\d{1,2})\s+)?((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?))\s*)?(\d{4})\b(?:\))?",
        re.IGNORECASE
    )
    proposed_pattern = re.compile(
        r"\b(?:[Pp]roposed|[Pp]roposal|[Dd]raft)\b(?:\s*(?:in|on))?(?:[^0-9]*?)(?:(\d{1,2})\s+)?((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?))\s*(\d{4})\b",
        re.IGNORECASE
    )
    general_date_pattern = re.compile(
        r"\b(?:(\d{1,2})\s+)?((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?))\s*(\d{4})\b",
        re.IGNORECASE
    )

    doc_date = ""
    first_page_text = ""
    debug_text = [f"Debugging date extraction for {pdf_path}\n"]

    try:
        with fitz.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf, start=1):
                if page_num > 5:
                    break
                text = page.get_text("text") or ""
                if page_num == 1:
                    first_page_text = text
                debug_text.append(f"Page {page_num} text (PyMuPDF): {text[:500]}...\n{'-'*50}\n")
                
                adopted_match = adopted_pattern.search(text)
                if adopted_match:
                    day, month, year = adopted_match.groups()
                    month = MONTH_MAP.get(month.lower(), month) if month else ""
                    doc_date = f"Adopted {day + ' ' if day else ''}{month + ' ' if month else ''}{year}"
                    break
                
                proposed_match = proposed_pattern.search(text)
                if proposed_match:
                    day, month, year = proposed_match.groups()
                    month = MONTH_MAP.get(month.lower(), month)
                    keyword = "Proposed" if "propos" in proposed_match.group(0).lower() else "Draft"
                    doc_date = f"{keyword} {day + ' ' if day else ''}{month} {year}"
                if proposed_match:
                    break
            
            if not doc_date and first_page_text:
                general_match = general_date_pattern.search(first_page_text)
                if general_match:
                    day, month, year = general_match.groups()
                    month = MONTH_MAP.get(month.lower(), month)
                    doc_date = f"{day + ' ' if day else ''}{month} {year}"
    
    except Exception as e:
        debug_text.append(f"PyMuPDF failed: {e}\n")
        try:
            with open(pdf_path, "rb") as file:
                reader = PyPDF2.PdfReader(file)
                for page_num in range(min(5, len(reader.pages))):
                    text = reader.pages[page_num].extract_text() or ""
                    if page_num == 0:
                        first_page_text = text
                    debug_text.append(f"Page {page_num + 1} text (PyPDF2): {text[:500]}...\n{'-'*50}\n")
                    
                    adopted_match = adopted_pattern.search(text)
                    if adopted_match:
                        day, month, year = adopted_match.groups()
                        month = MONTH_MAP.get(month.lower(), month) if month else ""
                        doc_date = f"Adopted {day + ' ' if day else ''}{month + ' ' if month else ''}{year}"
                        break
                    
                    proposed_match = proposed_pattern.search(text)
                    if proposed_match:
                        day, month, year = proposed_match.groups()
                        month = MONTH_MAP.get(month.lower(), month)
                        keyword = "Proposed" if "propos" in proposed_match.group(0).lower() else "Draft"
                        doc_date = f"{keyword} {day + ' ' if day else ''}{month} {year}"
                    if proposed_match:
                        break
                
                if not doc_date and first_page_text:
                    general_match = general_date_pattern.search(first_page_text)
                    if general_match:
                        day, month, year = general_match.groups()
                        month = MONTH_MAP.get(month.lower(), month)
                        doc_date = f"{day + ' ' if day else ''}{month} {year}"
        except Exception as e:
            debug_text.append(f"PyPDF2 failed: {e}\n")

    if not doc_date:
        doc_date = "Unknown"

    debug_file = output_dir / f"date_debug_{Path(pdf_path).stem}.txt"
    with open(debug_file, "w", encoding="utf-8") as f:
        f.write("".join(debug_text))

    return authority_name, doc_date, pdf_url

def search_pdf_for_standards(pdf_path, output_dir):
    patterns = {
        "M4(2)": re.compile(r"M4\s*\(?\s*2\s*\)?|Category\s*2\s*(?:Accessible\s*and\s*Adaptable)?", re.IGNORECASE),
        "M4(3)": re.compile(r"M4\s*\(?\s*3\s*\)?|Category\s*3\s*(?:Wheelchair\s*User\s*Dwellings)?", re.IGNORECASE),
        "Percentage Target": re.compile(r"(\d{1,3})\s*(?:%|\b(?:percent|per\s*cent)\b)|(?:\b(ninety|ten|one\s*hundred)\s*(?:percent|per\s*cent)\b)", re.IGNORECASE),
        "Plan Period": re.compile(r"(?:Local\s*Plan|Core\s*Strategy|housing\s*requirement|plan\s*period).{0,200}?\b(\d{4})\s*[-–—]\s*(\d{4})\b", re.IGNORECASE),
    }

    results = {key: [] for key in patterns}
    debug_text = [f"Extracted text from {pdf_path}\n\n"]
    full_text = ""

    try:
        with fitz.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf, start=1):
                text = page.get_text("text") or ""
                debug_text.append(f"Page {page_num}:\n{text[:500]}...\n{'-'*50}\n")
                full_text += text + " "
                for key, pattern in patterns.items():
                    matches = pattern.finditer(text)
                    for match in matches:
                        start = max(0, match.start() - 500)
                        end = min(len(text), match.end() + 500)
                        context = text[start:end].replace("\n", " ")
                        results[key].append({"page": page_num, "match": match.group(), "context": context})
    except Exception as e:
        debug_text.append(f"PyMuPDF failed: {e}\n")
        return None, "", debug_text

    debug_file = output_dir / f"extracted_text_{Path(pdf_path).stem}.txt"
    with open(debug_file, "w", encoding="utf-8") as f:
        f.write("".join(debug_text))

    return results, full_text, debug_text

def find_page_number(full_text, sentence, pdf_path):
    try:
        with fitz.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf, start=1):
                text = page.get_text("text") or ""
                if sentence in text:
                    return page_num
        return "Unknown"
    except Exception:
        return "Unknown"

def find_percentage_in_context(standard, full_text, pdf_path, debug_text=None):
    standard_pattern = (
        re.compile(r"M4\s*\(?\s*2\s*\)?|Category\s*2\s*(?:Accessible\s*and\s*Adaptable)?", re.IGNORECASE)
        if standard == "M4(2)"
        else re.compile(r"M4\s*\(?\s*3\s*\)?|Category\s*3\s*(?:Wheelchair\s*User\s*Dwellings)?", re.IGNORECASE)
    )
    percent_pattern = re.compile(r"(\d{1,3})\s*(?:%|\b(?:percent|per\s*cent)\b)|(?:\b(ninety|ten|one\s*hundred)\s*(?:percent|per\s*cent)\b)", re.IGNORECASE)

    sentences = [s.strip() for s in full_text.split(".") if s.strip()]
    standard_percentages = []
    notes = []

    for sentence in sentences:
        if re.search(standard_pattern, sentence):
            percent_matches = percent_pattern.finditer(sentence)
            for match in percent_matches:
                if match.group(1):
                    percent = match.group(1) + "%"
                elif match.group(2):
                    percent = NUMBER_WORDS.get(match.group(2).lower(), "Unknown") + "%"
                standard_percentages.append(percent)
                page_num = find_page_number(full_text, sentence, pdf_path)
                display_sentence = summarize_text(sentence, max_words=50) if len(sentence.split()) > 100 else sentence
                notes.append({"page": page_num, "sentence": display_sentence})
            if standard_percentages:
                break

    percentage = "/".join(standard_percentages) + "*" if len(standard_percentages) > 1 else standard_percentages[0] if standard_percentages else "N/A"
    return percentage, notes

def process_pdf(args):
    pdf_path, output_dir = args
    debug_text = [f"Starting processing for {pdf_path}\n"]
    try:
        results, full_text, search_debug = search_pdf_for_standards(pdf_path, output_dir)
        debug_text.extend(search_debug)
        if results is None:
            debug_text.append(f"Failed to process {pdf_path}: No results returned.\n")
            return None, debug_text, None

        authority_name, doc_date, pdf_url = extract_metadata(pdf_path, output_dir)
        debug_text.append(f"Metadata extracted: Authority={authority_name}, Date={doc_date}\n")
        m4_2_percent, m4_2_notes = find_percentage_in_context("M4(2)", full_text, pdf_path, debug_text)
        m4_3_percent, m4_3_notes = find_percentage_in_context("M4(3)", full_text, pdf_path, debug_text)

        plan_period = "Unknown"  # Simplified for brevity
        notes_text = []
        for standard, notes in [("M4(2)", m4_2_notes), ("M4(3)", m4_3_notes)]:
            for note in notes:
                note_text = f"{standard} on page {note['page']}: {note['sentence']}"
                notes_text.append(note_text)
        notes_str = "; ".join(notes_text) if notes_text else "N/A"

        summary = {
            "standards_found": [],
            "details": {
                "M4(2)": [{"percentage": m4_2_percent}] if m4_2_percent != "N/A" else [],
                "M4(3)": [{"percentage": m4_3_percent}] if m4_3_percent != "N/A" else [],
            }
        }

        json_file = output_dir / f"summary_{Path(pdf_path).stem}.json"
        with open(json_file, "w") as f:
            json.dump(summary, f, indent=2)
        debug_text.append(f"Wrote JSON file: {json_file}\n")

        csv_data = {
            "Local Planning Authority": authority_name,
            "Date of Doc/Status": doc_date,
            "Plan Period": plan_period,
            "M4(2) Percentage": m4_2_percent,
            "M4(3) Percentage": m4_3_percent,
            "Notes": notes_str
        }
        print(f"Processed {pdf_path}: {csv_data}")  # Debug print
        return csv_data, debug_text, json_file
    except Exception as e:
        debug_text.append(f"Error processing {pdf_path}: {str(e)}\n")
        return None, debug_text, None

def process_uploaded_files(uploaded_files, temp_dir):
    output_dir = Path(temp_dir) / "output_files"
    output_dir.mkdir(exist_ok=True)
    csv_file = output_dir / "summary_output.csv"
    error_log_file = output_dir / "error_log.txt"
    error_log = []
    all_csv_data = []
    json_files = []

    with progress_lock:
        progress["total"] = len(uploaded_files)
        progress["current"] = 0
        progress["message"] = "Starting processing..."

    tasks = []
    for pdf_path in uploaded_files:
        tasks.append((pdf_path, output_dir))

    for result in tqdm(tasks, desc="Processing PDFs", total=len(tasks)):
        csv_data, debug_text, json_file = process_pdf(result)
        with progress_lock:
            progress["current"] += 1
            progress["message"] = f"Processing PDF {progress['current']} of {progress['total']}"
        if csv_data:
            all_csv_data.append(csv_data)
        if json_file:
            json_files.append(json_file)
        error_log.extend(debug_text)

    # Ensure CSV is written even if no data, with a header
    if not all_csv_data:
        all_csv_data = [{"Local Planning Authority": "N/A", "Date of Doc/Status": "N/A", "Plan Period": "N/A", 
                        "M4(2) Percentage": "N/A", "M4(3) Percentage": "N/A", "Notes": "No data processed"}]

    all_csv_data.sort(key=lambda x: x["Local Planning Authority"].lower())

    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["Local Planning Authority", "Date of Doc/Status", "Plan Period", "M4(2) Percentage", "M4(3) Percentage", "Notes"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for csv_data in all_csv_data:
            writer.writerow(csv_data)

    with open(error_log_file, "w", encoding="utf-8") as f:
        f.write("\n".join(error_log))

    with progress_lock:
        progress["message"] = "Processing complete!"

    return csv_file, json_files, error_log_file

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_files():
    if 'files' not in request.files:
        return jsonify({'error': 'No files uploaded'}), 400

    uploaded_files = request.files.getlist('files')
    if not uploaded_files or all(f.filename == '' for f in uploaded_files):
        return jsonify({'error': 'No valid files uploaded'}), 400

    temp_dir = tempfile.mkdtemp()
    try:
        # Create output directory
        output_dir = Path(temp_dir) / "output_files"
        output_dir.mkdir(exist_ok=True)

        # Save all uploaded files to a temporary directory, preserving folder structure
        all_pdf_paths = []
        for uploaded_file in uploaded_files:
            if uploaded_file.filename != '':
                # The filename includes the relative path from the selected folder
                file_path = Path(temp_dir) / uploaded_file.filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                uploaded_file.save(file_path)
                if file_path.suffix.lower() == '.pdf':
                    all_pdf_paths.append(file_path)

        if not all_pdf_paths:
            return jsonify({'error': 'No PDF files found in the upload'}), 400

        csv_file, json_files, error_log_file = process_uploaded_files(all_pdf_paths, temp_dir)
        zip_path = Path(temp_dir) / 'results.zip'
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(csv_file, csv_file.name)
            for json_file in json_files:
                zipf.write(json_file, json_file.name)
            zipf.write(error_log_file, error_log_file.name)

        return send_file(
            zip_path,
            mimetype='application/zip',
            as_attachment=True,
            download_name='pdf_processing_results.zip'
        )
    except Exception as e:
        with progress_lock:
            progress["message"] = f"Processing failed: {str(e)}"
        return jsonify({'error': f'Processing failed: {str(e)}'}), 500
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

@app.route('/progress')
def get_progress():
    with progress_lock:
        return jsonify({
            'current': progress["current"],
            'total': progress["total"],
            'percentage': (progress["current"] / progress["total"] * 100) if progress["total"] > 0 else 0,
            'message': progress["message"]
        })

if __name__ == '__main__':
    app.run(debug=True)