import re
from pathlib import Path
import fitz  # PyMuPDF
import PyPDF2
from datetime import datetime
import json
import csv
import os
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

# Initialize NLTK data
try:
    nltk.download('punkt', quiet=True, download_dir='/Users/tv/nltk_data')
    nltk.download('punkt_tab', quiet=True, download_dir='/Users/tv/nltk_data')
    nltk.data.path.append('/Users/tv/nltk_data')
except Exception as e:
    print(f"Failed to download NLTK tokenizers: {e}")
    print("Please run: /usr/bin/python3 -c \"import nltk; nltk.download('punkt'); nltk.download('punkt_tab')\"")

# Dictionary to convert written-out numbers to numeric percentages
NUMBER_WORDS = {
    'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 
    'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
    'ten': '10', 'eleven': '11', 'twelve': '12', 'thirteen': '13', 
    'fourteen': '14', 'fifteen': '15', 'sixteen': '16', 'seventeen': '17',
    'eighteen': '18', 'nineteen': '19', 'twenty': '20', 'thirty': '30',
    'forty': '40', 'fifty': '50', 'sixty': '60', 'seventy': '70',
    'eighty': '80', 'ninety': '90', 'hundred': '100'
}

def summarize_text(text, max_words=50):
    """Summarize text using sumy's LexRankSummarizer."""
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
        error_msg = f"Summary failed: {str(e)}\nInput text (first 200 chars): {text[:200]}..."
        print(error_msg)  # Log to terminal for debugging
        return error_msg

def extract_metadata(pdf_path, output_dir):
    """Extract Local Planning Authority, Date of Doc/Status, and Plan Period from PDF."""
    file_name = Path(pdf_path).stem
    authority_name = file_name
    pdf_url = str(pdf_path)
    
    # Patterns for date extraction
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

    # Dictionary to convert month abbreviations to full names
    MONTH_MAP = {
        'jan': 'January', 'january': 'January',
        'feb': 'February', 'february': 'February',
        'mar': 'March', 'march': 'March',
        'apr': 'April', 'april': 'April',
        'may': 'May',
        'jun': 'June', 'june': 'June',
        'jul': 'July', 'july': 'July',
        'aug': 'August', 'august': 'August',
        'sep': 'September', 'september': 'September',
        'oct': 'October', 'october': 'October',
        'nov': 'November', 'november': 'November',
        'dec': 'December', 'december': 'December'
    }

    doc_date = ""
    first_page_text = ""
    full_text = ""
    debug_text = [f"Debugging date extraction for {pdf_path}\n"]

    # Try PyMuPDF first
    try:
        with fitz.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf, start=1):
                if page_num > 5:
                    break
                text = page.get_text("text") or ""
                full_text += text + " "
                if page_num == 1:
                    first_page_text = text
                debug_text.append(f"Page {page_num} text (PyMuPDF): {text[:500]}...\n{'-'*50}\n")
                
                # Check for adopted date
                adopted_match = adopted_pattern.search(text)
                if adopted_match:
                    day, month, year = adopted_match.groups()
                    month = MONTH_MAP.get(month.lower(), month) if month else ""
                    doc_date = f"Adopted {day + ' ' if day else ''}{month + ' ' if month else ''}{year}"
                    debug_text.append(f"Found adopted date: {doc_date} on page {page_num} (PyMuPDF)\n")
                    break
                else:
                    debug_text.append(f"No adopted date found on page {page_num} (PyMuPDF)\n")
                
                # Check for proposed/draft date
                proposed_match = proposed_pattern.search(text)
                if proposed_match:
                    day, month, year = proposed_match.groups()
                    month = MONTH_MAP.get(month.lower(), month)
                    keyword = "Proposed" if "propos" in proposed_match.group(0).lower() else "Draft"
                    doc_date = f"{keyword} {day + ' ' if day else ''}{month} {year}"
                    debug_text.append(f"Found {keyword.lower()} date: {doc_date} on page {page_num} (PyMuPDF)\n")
                else:
                    debug_text.append(f"No proposed/draft date found on page {page_num} (PyMuPDF)\n")
                
                if proposed_match:
                    break
            
            # If no adopted/proposed date, check for general date on first page
            if not doc_date and first_page_text:
                general_match = general_date_pattern.search(first_page_text)
                if general_match:
                    day, month, year = general_match.groups()
                    month = MONTH_MAP.get(month.lower(), month)
                    doc_date = f"{day + ' ' if day else ''}{month} {year}"
                    debug_text.append(f"Found general date: {doc_date} on first page (PyMuPDF)\n")
                else:
                    debug_text.append(f"No general date found on first page (PyMuPDF)\n")
    
    except Exception as e:
        debug_text.append(f"PyMuPDF failed: {e}\n")
        # Try PyPDF2
        try:
            with open(pdf_path, "rb") as file:
                reader = PyPDF2.PdfReader(file)
                for page_num in range(min(5, len(reader.pages))):
                    text = reader.pages[page_num].extract_text() or ""
                    full_text += text + " "
                    if page_num == 0:
                        first_page_text = text
                    debug_text.append(f"Page {page_num + 1} text (PyPDF2): {text[:500]}...\n{'-'*50}\n")
                    
                    # Check for adopted date
                    adopted_match = adopted_pattern.search(text)
                    if adopted_match:
                        day, month, year = adopted_match.groups()
                        month = MONTH_MAP.get(month.lower(), month) if month else ""
                        doc_date = f"Adopted {day + ' ' if day else ''}{month + ' ' if month else ''}{year}"
                        debug_text.append(f"Found adopted date: {doc_date} on page {page_num + 1} (PyPDF2)\n")
                        break
                    
                    # Check for proposed/draft date
                    proposed_match = proposed_pattern.search(text)
                    if proposed_match:
                        day, month, year = proposed_match.groups()
                        month = MONTH_MAP.get(month.lower(), month)
                        keyword = "Proposed" if "propos" in proposed_match.group(0).lower() else "Draft"
                        doc_date = f"{keyword} {day + ' ' if day else ''}{month} {year}"
                        debug_text.append(f"Found {keyword.lower()} date: {doc_date} on page {page_num + 1} (PyPDF2)\n")
                    else:
                        debug_text.append(f"No proposed/draft date found on page {page_num + 1} (PyPDF2)\n")
                    
                    if proposed_match:
                        break
                
                # If no adopted/proposed date, check for general date on first page
                if not doc_date and first_page_text:
                    general_match = general_date_pattern.search(first_page_text)
                    if general_match:
                        day, month, year = general_match.groups()
                        month = MONTH_MAP.get(month.lower(), month)
                        doc_date = f"{day + ' ' if day else ''}{month} {year}"
                        debug_text.append(f"Found general date: {doc_date} on first page (PyPDF2)\n")
                    else:
                        debug_text.append(f"No general date found on first page (PyPDF2)\n")
        except Exception as e:
            debug_text.append(f"PyPDF2 failed: {e}\n")

    # Try OCR as a fallback if no date was found or first page text is empty
    if not doc_date or not first_page_text:
        debug_text.append(f"Attempting OCR for {pdf_path}\n")
        try:
            # Convert first 5 pages to images
            images = convert_from_path(pdf_path, first_page=1, last_page=5, dpi=400)
            ocr_first_page_text = ""
            ocr_full_text = ""
            for page_num, image in enumerate(images, start=1):
                # Preprocess image: convert to grayscale and enhance contrast
                image = image.convert('L')  # Grayscale
                enhancer = PIL.ImageEnhance.Contrast(image)
                image = enhancer.enhance(2.0)  # Increase contrast
                # Perform OCR
                text = pytesseract.image_to_string(image, lang='eng')
                ocr_full_text += text + " "
                if page_num == 1:
                    ocr_first_page_text = text
                debug_text.append(f"Page {page_num} text (OCR): {text[:500]}...\n{'-'*50}\n")
                
                # Check for adopted date
                adopted_match = adopted_pattern.search(text)
                if adopted_match:
                    day, month, year = adopted_match.groups()
                    month = MONTH_MAP.get(month.lower(), month) if month else ""
                    doc_date = f"Adopted {day + ' ' if day else ''}{month + ' ' if month else ''}{year}"
                    debug_text.append(f"Found adopted date: {doc_date} on page {page_num} (OCR)\n")
                    break
                
                # Check for proposed/draft date
                proposed_match = proposed_pattern.search(text)
                if proposed_match:
                    day, month, year = proposed_match.groups()
                    month = MONTH_MAP.get(month.lower(), month)
                    keyword = "Proposed" if "propos" in proposed_match.group(0).lower() else "Draft"
                    doc_date = f"{keyword} {day + ' ' if day else ''}{month} {year}"
                    debug_text.append(f"Found {keyword.lower()} date: {doc_date} on page {page_num} (OCR)\n")
                else:
                    debug_text.append(f"No proposed/draft date found on page {page_num} (OCR)\n")
                
                if proposed_match:
                    break
            
            # If no adopted/proposed date, check for general date on first page
            if not doc_date and ocr_first_page_text:
                general_match = general_date_pattern.search(ocr_first_page_text)
                if general_match:
                    day, month, year = general_match.groups()
                    month = MONTH_MAP.get(month.lower(), month)
                    doc_date = f"{day + ' ' if day else ''}{month} {year}"
                    debug_text.append(f"Found general date: {doc_date} on first page (OCR)\n")
                else:
                    debug_text.append(f"No general date found on first page (OCR)\n")
        
        except Exception as e:
            debug_text.append(f"OCR failed: {e}\n")

    # Set to "Unknown" if no date was found
    if not doc_date:
        doc_date = "Unknown"
        debug_text.append("No date found after all methods\n")

    # Write debug text to a file in output_dir
    debug_file = output_dir / f"date_debug_{Path(pdf_path).stem}.txt"
    with open(debug_file, "w", encoding="utf-8") as f:
        f.write("".join(debug_text))

    return authority_name, doc_date, pdf_url

def search_pdf_for_standards(pdf_path, output_dir):
    """Search PDF for M4 standards, percentages, and plan periods."""
    patterns = {
        "M4(2)": re.compile(
            r"M4\s*\(?\s*2\s*\)?|Category\s*2\s*(?:Accessible\s*and\s*Adaptable)?",
            re.IGNORECASE
        ),
        "M4(3)": re.compile(
            r"M4\s*\(?\s*3\s*\)?|Category\s*3\s*(?:Wheelchair\s*User\s*Dwellings)?",
            re.IGNORECASE
        ),
        "Percentage Target": re.compile(
            r"(\d{1,3})\s*(?:%|\b(?:percent|per\s*cent)\b)|"
            r"(?:\b(ninety|ten|one\s*hundred)\s*(?:percent|per\s*cent)\b)",
            re.IGNORECASE
        ),
        "Plan Period": re.compile(
            r"(?:Local\s*Plan|Core\s*Strategy|housing\s*requirement|plan\s*period).{0,200}?\b(\d{4})\s*[-–—]\s*(\d{4})\b",
            re.IGNORECASE
        ),
    }

    results = {key: [] for key in patterns}
    debug_text = [f"Extracted text from {pdf_path}\n\n"]
    full_text = ""

    try:
        with fitz.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf, start=1):
                text = page.get_text("text") or ""
                if not text.strip():
                    debug_text.append(f"Page {page_num}:\n[No text extracted]\n{'-'*50}\n")
                    continue
                debug_text.append(f"Page {page_num}:\n{text}\n{'-'*50}\n")
                full_text += text + " "
                for key, pattern in patterns.items():
                    matches = pattern.finditer(text)
                    for match in matches:
                        start = max(0, match.start() - 500)
                        end = min(len(text), match.end() + 500)
                        context = text[start:end].replace("\n", " ")
                        results[key].append({"page": page_num, "match": match.group(), "context": context})
    except Exception as e:
        debug_text.append(f"Error: PyMuPDF failed: {e}\n")
        try:
            with open(pdf_path, "rb") as file:
                reader = PyPDF2.PdfReader(file)
                for page_num in range(len(reader.pages)):
                    text = reader.pages[page_num].extract_text() or ""
                    if not text.strip():
                        debug_text.append(f"Page {page_num + 1}:\n[No text extracted]\n{'-'*50}\n")
                        continue
                    debug_text.append(f"Page {page_num + 1}:\n{text}\n{'-'*50}\n")
                    full_text += text + " "
                    for key, pattern in patterns.items():
                        matches = pattern.finditer(text)
                        for match in matches:
                            start = max(0, match.start() - 500)
                            end = min(len(text), match.end() + 500)
                            context = text[start:end].replace("\n", " ")
                            results[key].append({"page": page_num + 1, "match": match.group(), "context": context})
        except Exception as e:
            debug_text.append(f"Error: PyPDF2 failed: {e}\n")
            return None, "", debug_text

    debug_file = output_dir / f"extracted_text_{Path(pdf_path).stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(debug_file, "w", encoding="utf-8") as f:
        f.write("".join(debug_text))

    return results, full_text, debug_text

def find_page_number(full_text, sentence, pdf_path):
    """Find the page number where a sentence appears."""
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
    """Extract percentages for M4(2) or M4(3) from text sentences."""
    standard_pattern = (
        re.compile(r"M4\s*\(?\s*2\s*\)?|Category\s*2\s*(?:Accessible\s*and\s*Adaptable)?", re.IGNORECASE)
        if standard == "M4(2)"
        else re.compile(r"M4\s*\(?\s*3\s*\)?|Category\s*3\s*(?:Wheelchair\s*User\s*Dwellings)?", re.IGNORECASE)
    )
    opposite_pattern = (
        re.compile(r"M4\s*\(?\s*3\s*\)?|Category\s*3\s*(?:Wheelchair\s*User\s*Dwellings)?", re.IGNORECASE)
        if standard == "M4(2)"
        else re.compile(r"M4\s*\(?\s*2\s*\)?|Category\s*2\s*(?:Accessible\s*and\s*Adaptable)?", re.IGNORECASE)
    )
    percent_pattern = re.compile(
        r"(\d{1,3})\s*(?:%|\b(?:percent|per\s*cent)\b)(?:\s|$)|"
        r"(?:\b(ninety|ten|one\s*hundred)\s*(?:percent|per\s*cent)\b)",
        re.IGNORECASE
    )

    sentences = [s.strip() for s in full_text.split(".") if s.strip()]
    standard_percentages = []
    notes = []

    for sentence in sentences:
        if re.search(standard_pattern, sentence):
            if re.search(opposite_pattern, sentence):
                if debug_text:
                    debug_text.append(
                        f"Skipped sentence in {Path(pdf_path).name}: '{sentence}' "
                        f"contains both {standard} and opposite standard.\n"
                    )
                continue
            percent_matches = percent_pattern.finditer(sentence)
            sentence_percentages = []
            for match in percent_matches:
                if match.group(1):
                    percent = match.group(1) + "%"
                elif match.group(2):
                    percent = NUMBER_WORDS.get(match.group(2).lower(), "Unknown") + "%"
                sentence_percentages.append(percent)
                # Find page number and summarize if needed
                page_num = find_page_number(full_text, sentence, pdf_path)
                sentence_words = len(sentence.split())
                display_sentence = summarize_text(sentence, max_words=50) if sentence_words > 100 else sentence
                notes.append({"page": page_num, "sentence": display_sentence})
                if debug_text:
                    debug_text.append(
                        f"Found {standard} with percentage {percent} on page {page_num} in {Path(pdf_path).name}: '{sentence}'\n"
                    )
            if sentence_percentages:
                standard_percentages.extend(sentence_percentages)
                break

    if standard_percentages:
        percentage = "/".join(standard_percentages) + "*" if len(standard_percentages) > 1 else standard_percentages[0]
    else:
        percentage = "N/A"
        if debug_text:
            debug_text.append(f"No valid percentage found for {standard} in {Path(pdf_path).name}\n")

    return percentage, notes

def second_check_percentage(standard, full_text, pdf_path, debug_text=None):
    """Second check for percentages near M4(2) or M4(3) keywords."""
    standard_keyword = re.compile(
        r"accessible\s*and\s*adaptable\s*(dwellings|standard|standards)" if standard == "M4(2)"
        else r"wheelchair\s*user\s*dwellings", re.IGNORECASE
    )
    opposite_keyword = re.compile(
        r"wheelchair\s*user\s*dwellings" if standard == "M4(2)"
        else r"accessible\s*and\s*adaptable\s*(dwellings|standard|standards)", re.IGNORECASE
    )
    percent_pattern = re.compile(
        r"(\d{1,3})\s*(?:%|\b(?:percent|per\s*cent)\b)(?:\s|$)|"
        r"(?:\b(ninety|ten|one\s*hundred)\s*(?:percent|per\s*cent)\b)",
        re.IGNORECASE
    )
    special_phrases = re.compile(
        r"\b(all\s*other\s*dwellings|all\s*other\s*new\s*dwellings|all\s*remaining\s*new\s*dwellings|all\s*other\s*new\s*build|remainder\s*of\s*dwellings|all\s*new\s*build|all\s*new\s*dwellings|all\s*housing|all\s*homes|all\s*new\s*homes)\b",
        re.IGNORECASE
    )
    all_new_homes = re.compile(r"\b(all\s*new\s*homes)\b", re.IGNORECASE)

    sentences = [s.strip() for s in re.split(r'[.;]', full_text) if s.strip()]
    result = "N/A"
    notes = []

    for sentence in sentences:
        if re.search(standard_keyword, sentence) and all_new_homes.search(sentence):
            page_num = find_page_number(full_text, sentence, pdf_path)
            sentence_words = len(sentence.split())
            display_sentence = summarize_text(sentence, max_words=50) if sentence_words > 100 else sentence
            notes.append({"page": page_num, "sentence": display_sentence})
            if debug_text:
                debug_text.append(
                    f"Second check found 'all new homes' for {standard} on page {page_num} in {Path(pdf_path).name}: "
                    f"'{sentence}' -> 100%\n"
                )
            return "100%", notes

        standard_matches = [(m.start(), m.end(), m.group()) for m in re.finditer(standard_keyword, sentence)]
        opposite_matches = [(m.start(), m.end(), m.group()) for m in re.finditer(opposite_keyword, sentence)]
        percent_matches = [(m.start(), m.end(), m.group(1), m.group(2)) for m in percent_pattern.finditer(sentence)]
        special_matches = [(m.start(), m.end(), m.group(1)) for m in special_phrases.finditer(sentence)]

        if not standard_matches:
            continue

        for start, end, phrase in special_matches:
            for std_start, std_end, _ in standard_matches:
                if abs(start - std_start) < 500:
                    result = phrase
                    page_num = find_page_number(full_text, sentence, pdf_path)
                    sentence_words = len(sentence.split())
                    display_sentence = summarize_text(sentence, max_words=50) if sentence_words > 100 else sentence
                    notes.append({"page": page_num, "sentence": display_sentence})
                    if debug_text:
                        debug_text.append(
                            f"Second check found special phrase '{result}' for {standard} on page {page_num} in {Path(pdf_path).name}: "
                            f"'{sentence}'\n"
                        )
                    if result in ["all new build", "all new dwellings", "all new homes"]:
                        return "100%", notes
                    return result, notes

        for p_start, p_end, numeric_percent, written_percent in percent_matches:
            if numeric_percent:
                percent = numeric_percent + "%"
            elif written_percent:
                percent = NUMBER_WORDS.get(written_percent.lower(), "Unknown") + "%"
            else:
                continue

            min_distance = float('inf')
            is_standard = False
            for std_start, std_end, _ in standard_matches:
                distance = min(abs(p_start - std_end), abs(p_end - std_start))
                if distance < min_distance:
                    min_distance = distance
                    is_standard = True
            for opp_start, opp_end, _ in opposite_matches:
                distance = min(abs(p_start - opp_end), abs(p_end - opp_start))
                if distance < min_distance:
                    min_distance = distance
                    is_standard = False

            if is_standard and min_distance < 100:
                result = percent
                page_num = find_page_number(full_text, sentence, pdf_path)
                sentence_words = len(sentence.split())
                display_sentence = summarize_text(sentence, max_words=50) if sentence_words > 100 else sentence
                notes.append({"page": page_num, "sentence": display_sentence})
                if debug_text:
                    debug_text.append(
                        f"Second check found {standard} with percentage {percent} on page {page_num} in {Path(pdf_path).name}: "
                        f"'{sentence}'\n"
                    )
                return result, notes

    if debug_text:
        debug_text.append(f"Second check: No valid result found for {standard} in {Path(pdf_path).name}\n")
    return result, notes

def third_check_percentage(standard, full_text, pdf_path, debug_text=None):
    """Third check for percentages near M4(2) or M4(3) keywords."""
    standard_pattern = re.compile(
        r"M4\s*\(?\s*2\s*\)?|Category\s*2\s*(?:Accessible\s*and\s*Adaptable)?",
        re.IGNORECASE
    ) if standard == "M4(2)" else re.compile(
        r"M4\s*\(?\s*3\s*\)?|Category\s*3\s*(?:Wheelchair\s*User\s*Dwellings)?",
        re.IGNORECASE
    )
    percent_pattern = re.compile(
        r"(\d{1,3})\s*(?:%|\b(?:percent|per\s*cent)\b)(?:\s|$)|"
        r"(?:\b(ninety|ten|one\s*hundred)\s*(?:percent|per\s*cent)\b)",
        re.IGNORECASE
    )

    sentences = [s.strip() for s in full_text.split(".") if s.strip()]
    standard_percentages = []
    notes = []

    for sentence in sentences:
        if not re.search(standard_pattern, sentence):
            continue

        standard_matches = [(m.start(), m.group()) for m in re.finditer(standard_pattern, sentence)]
        percent_matches = []
        for match in percent_pattern.finditer(sentence):
            if match.group(1):
                percent = match.group(1) + "%"
            elif match.group(2):
                percent = NUMBER_WORDS.get(match.group(2).lower(), "Unknown") + "%"
            else:
                continue
            percent_matches.append((match.start(), percent))

        for std_start, _ in standard_matches:
            closest_percent = None
            min_distance = float('inf')
            for p_start, percent in percent_matches:
                distance = abs(p_start - std_start)
                if distance < min_distance:
                    min_distance = distance
                    closest_percent = percent
            if closest_percent:
                standard_percentages.append(closest_percent)
                page_num = find_page_number(full_text, sentence, pdf_path)
                sentence_words = len(sentence.split())
                display_sentence = summarize_text(sentence, max_words=50) if sentence_words > 100 else sentence
                notes.append({"page": page_num, "sentence": display_sentence})
                if debug_text:
                    debug_text.append(
                        f"Third check found {standard} with percentage {closest_percent} on page {page_num} in {Path(pdf_path).name}: "
                        f"'{sentence}'\n"
                    )

        if re.search(standard_pattern, sentence) and not standard_percentages:
            if debug_text:
                debug_text.append(f"Third check found {standard} but no percentage in {Path(pdf_path).name}: '{sentence}'\n")

    if standard_percentages:
        unique_percentages = list(dict.fromkeys(standard_percentages))
        percentage = "/".join(unique_percentages) + "*" if len(unique_percentages) > 1 else unique_percentages[0]
    else:
        percentage = "N/A"
        if debug_text:
            debug_text.append(f"Third check: No valid percentage found for {standard} in {Path(pdf_path).name}\n")

    return percentage, notes

def find_plan_period(full_text, pdf_path, debug_text=None):
    """Extract plan period (e.g., 2013-2032)."""
    pattern = re.compile(
        r"(?:Local\s*Plan|Core\s*Strategy|housing\s*requirement|plan\s*period).{0,200}?\b(\d{4})\s*[-–—]\s*(\d{4})\b",
        re.IGNORECASE
    )
    matches = pattern.finditer(full_text)
    plan_periods = []
    for match in matches:
        start_year, end_year = match.groups()
        period = f"{start_year}-{end_year}"
        plan_periods.append(period)
        if debug_text:
            debug_text.append(f"Found plan period: {period} in {Path(pdf_path).name}\n")
    return sorted(plan_periods)[0] if plan_periods else "Unknown"

def process_pdf(args):
    """Process a single PDF to extract metadata and standards."""
    pdf_path, output_dir = args
    debug_text = [f"Starting processing for {pdf_path}\n"]
    try:
        results, full_text, search_debug = search_pdf_for_standards(pdf_path, output_dir)
        debug_text.extend(search_debug)
        if results is None:
            debug_text.append(f"Failed to process {pdf_path}: No results returned from search_pdf_for_standards.\n")
            return None, debug_text

        authority_name, doc_date, pdf_url = extract_metadata(pdf_path, output_dir)
        debug_text.append(f"Metadata extracted: Authority={authority_name}, Date={doc_date}\n")

        plan_period = find_plan_period(full_text, pdf_path, debug_text)
        m4_2_percent, m4_2_notes = find_percentage_in_context("M4(2)", full_text, pdf_path, debug_text)
        m4_3_percent, m4_3_notes = find_percentage_in_context("M4(3)", full_text, pdf_path, debug_text)

        if m4_2_percent == "N/A":
            m4_2_percent, m4_2_notes = second_check_percentage("M4(2)", full_text, pdf_path, debug_text)
        if m4_3_percent == "N/A":
            m4_3_percent, m4_3_notes = second_check_percentage("M4(3)", full_text, pdf_path, debug_text)

        if m4_2_percent == "N/A":
            m4_2_percent, m4_2_notes = third_check_percentage("M4(2)", full_text, pdf_path, debug_text)
        if m4_3_percent == "N/A":
            m4_3_percent, m4_3_notes = third_check_percentage("M4(3)", full_text, pdf_path, debug_text)

        # Format notes with text wrapping
        def wrap_text(text, width=60):
            if not text:
                return text
            words = text.split()
            lines = []
            current_line = ""
            for word in words:
                if len(current_line) + len(word) + 1 <= width:
                    current_line += ("" if not current_line else " ") + word
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = word
            if current_line:
                lines.append(current_line)
            return "\n".join(lines)

        # Format notes
        notes_text = []
        for standard, notes in [("M4(2)", m4_2_notes), ("M4(3)", m4_3_notes)]:
            for note in notes:
                note_text = f"{standard} on page {note['page']}: {note['sentence']}"
                wrapped_note = wrap_text(note_text, width=60)  # Wrap at 60 characters
                notes_text.append(wrapped_note)
        notes_str = "; ".join(notes_text) if notes_text else "N/A"

        summary = {
            "standards_found": [],
            "details": {
                "M4(2)": [{"percentage": m4_2_percent}] if m4_2_percent != "N/A" else [],
                "M4(3)": [{"percentage": m4_3_percent}] if m4_3_percent != "N/A" else [],
                "Plan Period": [{"value": plan_period}] if plan_period != "Unknown" else [],
            }
        }

        for standard in ["M4(2)", "M4(3)"]:
            if results[standard]:
                summary["standards_found"].append(standard)
                summary["details"][standard] = [{"page": r["page"], "context": r["context"]} for r in results[standard]]

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
        debug_text.append(
            f"Processed {pdf_path}: Plan Period={plan_period}, "
            f"M4(2)={m4_2_percent}, M4(3)={m4_3_percent}, Notes={notes_str}\n"
        )
        return csv_data, debug_text
    except Exception as e:
        debug_text.append(f"Error processing {pdf_path}: {str(e)}\n")
        return None, debug_text

def main():
    """Main function to process PDFs in a directory."""
    directory = input("Enter the directory path containing PDF files (e.g., ~/Desktop/Yorkshire): ")
    directory_path = Path(directory).expanduser()
    if not directory_path.is_dir():
        print(f"Error: {directory} is not a valid directory.")
        return

    pdf_files = list(directory_path.glob("*.pdf"))
    if not pdf_files:
        print(f"Error: No PDF files found in {directory}.")
        return

    output_dir = directory_path / "output_files"
    output_dir.mkdir(exist_ok=True)
    csv_file = directory_path / "summary_output.csv"
    error_log_file = directory_path / "error_log.txt"
    error_log = []
    processed_count = 0

    num_processes = min(multiprocessing.cpu_count(), 16)
    pool = multiprocessing.Pool(processes=num_processes)
    tasks = [(pdf_path, output_dir) for pdf_path in pdf_files]

    all_csv_data = []
    for result in tqdm(pool.imap_unordered(process_pdf, tasks), total=len(tasks), desc="Processing PDFs"):
        csv_data, debug_text = result
        if csv_data:
            all_csv_data.append(csv_data)
            debug_text.append(f"Added to CSV: {csv_data['Local Planning Authority']}\n")
        else:
            debug_text.append(f"Skipped writing to CSV: No csv_data returned\n")
        error_log.extend(debug_text)

    pool.close()
    pool.join()

    all_csv_data.sort(key=lambda x: x["Local Planning Authority"].lower())

    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "Local Planning Authority",
            "Date of Doc/Status",
            "Plan Period",
            "M4(2) Percentage",
            "M4(3) Percentage",
            "Notes"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for csv_data in all_csv_data:
            writer.writerow(csv_data)
            processed_count += 1

    if error_log:
        with open(error_log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(error_log))
        print(f"Errors logged to {error_log_file}")

    print(f"\nProcessed {processed_count}/{len(pdf_files)} PDFs. CSV saved to {csv_file}")
    print(f"Text and JSON files saved to {output_dir}")

if __name__ == "__main__":
    main()