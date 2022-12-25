#!/usr/bin/env python3

# Common
import os
import re
import io
import unicodedata
import hashlib
import zipfile
import shutil

# DB
import sqlite3
from contextlib import closing

# ZIP
from PIL import Image, ImageOps

# PDF
import poppler
from poppler import PageRenderer
from poppler import RenderHint

# EPUB
from ebooklib import epub
from bs4 import BeautifulSoup

# Import from web
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import NotFound

from settings import (
    DATABASE_PATH,
    SCHEMA_PATH,
    IMG_SUFFIX,
    UPLOADDIR_PATH,
    THUMBDIR_PATH,
    EPUB_CHUNK_SPLIT,
)


# DB Initialization
def init_db():
    if os.path.exists(DATABASE_PATH):
        return

    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        with open(SCHEMA_PATH, mode="r") as f:
            db.cursor().executescript(f.read())
        db.commit()

    os.makedirs(os.path.dirname(os.path.abspath(__file__)) + "/static/", exist_ok=True)
    os.makedirs(UPLOADDIR_PATH, exist_ok=True)
    os.makedirs(THUMBDIR_PATH, exist_ok=True)


# SQlite3 Row -> Dict with error handling
def sqlresult_to_an_entry(result):
    try:
        return dict(result)
    except TypeError:
        raise IndexError


# Image generation
def pdf2img(filename, page=0, dpi=192, antialias=True):
    pdf = poppler.load_from_file(filename)
    if page >= pdf.pages:
        raise IndexError

    renderer = PageRenderer()
    renderer.set_render_hint(RenderHint.text_antialiasing, antialias)
    renderer.set_render_hint(RenderHint.antialiasing, antialias)
    image = renderer.render_page(pdf.create_page(page), xres=dpi, yres=dpi)

    pil_image = Image.frombytes(
        "RGBA",
        (image.width, image.height),
        image.data,
        "raw",
        str(image.format),
    )
    pil_image = pil_image.convert("RGB")
    return pil_image

def can_be_int(s):
    try:
        int(s)
        return True
    except:
        return False
    
def number_to_fixed_digits(filename):
    numbers = [str(i) for i in range(10)]
    states = [char in numbers for char in filename]
    chunks = []
    begins = 0
    for n in range(len(states)+1):
        if n == 0:
            continue
        if n == len(states) or states[n-1] != states[n]:
            chunk = filename[begins:n]
            if can_be_int(chunk):
                chunk = f'{int(chunk):06d}'
            chunks.append(chunk)
            begins = n
    return ''.join(chunks)

def zipcat(filename, page=None):
    # *** REFACT ***  split len and get #
    with zipfile.ZipFile(filename) as archive:
        entries = archive.namelist()
        image_srcs = []
        for i in entries:
            if archive.getinfo(i).is_dir():
                continue
            if i.lower().endswith(IMG_SUFFIX):
                image_srcs.append(i)
        image_srcs = sorted(image_srcs, key=lambda l:number_to_fixed_digits(l))
        if page is None:
            return len(image_srcs)

        with archive.open(image_srcs[page]) as file:
            img = Image.open(file)
            imgmode = img.mode
            imgtype = img.format
            img = img.copy()
            return img, imgtype, imgmode


# Text to N-grammed text
def n_gram(txt, gram_n=2):
    splitted = [txt[n : n + gram_n] for n in range(len(txt) - gram_n + 1)]
    return " ".join(splitted)


# N-Grammed text to normal text
def n_gram_to_txt(txt):
    txt_ary = re.split(r"[\s\-\/\;]", txt)
    if max([len(a) for a in txt_ary]) < 3:
        # seems n-grammed
        recovered = txt_ary[0][:-1]
        recovered += "".join([a[-1] for a in txt_ary if len(a) != 0])
        return recovered
    else:
        # maybe Non n-grammed
        return txt


# Memo for 2-byte characters
twobyte_chars = r"[\u3041-\u3096\u30A1-\u30FA々〇〻\u3400-\u9FFF\uF900-\uFAFF]|[\uD840-\uD87F\uDC00-\uDFFF\u3000-\u303F]"


# Text to n-gram, if 2-byte chars are contained.
# * Text -> Text but こんにちは -> こん んに にち ちは
def ngram_if_2byte(text, gram_n=2):
    txt_ary = re.split(r"[\s\-\/\;]", text)
    if max([len(a) for a in txt_ary]) > 40 or re.match(twobyte_chars, text):
        # Contains non-western language
        return n_gram(text, gram_n=gram_n)
    else:
        # The text is already tokenized (european lang.).
        return text


# Cleanup dirty OCRed text
def clean_ocr_text(t):
    # Join to one line
    t = "".join(t.splitlines()).strip()

    # Remove extra whitespaces
    t = re.sub("[ 　\t,\"'●■□一]+", " ", t)
    t = re.sub(". . ", "", t)

    # Remove errornous whitespaces in Japanese OCR
    for _ in range(3):
        t = re.sub(f"({twobyte_chars}+)[ \t　]({twobyte_chars}+)", "\\1\\2", t)

    # Remove ligartures (fi, fl and so on)
    t = unicodedata.normalize("NFKC", t)

    # Remove errornous dots
    t = re.sub(r"[\.,\"'●■□~=ー\−][\.,\"'●■□~=ー\−]+", "", t)

    return t


# Extract PDF text per page
def pdf2txt(pdf_path):
    pdf = poppler.load_from_file(pdf_path)
    pages = []
    for i in range(pdf.pages):
        # Extraction
        t = pdf.create_page(i).text()
        # Clean up
        t = clean_ocr_text(t)
        # Finally append
        pages.append(t)
    return pages


# Text -> excerpted text (in search result)
def excerpt(txt, start, end, length):
    is_asian = any([True for c in txt if unicodedata.east_asian_width(c) in "FWA"])
    if is_asian:
        length = length // 2
    start = 0 if start < length else start - length
    end = -1 if len(txt) - end < length else end + length
    return txt[start:end]


# Search result to showable format
def show_hit_text(text, query):
    hit_excerpt = ""
    for q in query.split(" "):
        match = re.search(q, text, re.IGNORECASE)
        if match is None:
            continue
        begin, end = match.start(), match.end()
        hit_excerpt += "..." + excerpt(text, begin, end, 40)
    hit_excerpt += "..."
    return hit_excerpt


def register_file(a_file, database):
    """
    Registers file into database.
    a_file: Werkzeug's FileStorage or string.
    database: sqlite3
    """
    if type(a_file) == FileStorage:
        file_data = a_file
        filename = secure_filename(a_file.filename)
    elif type(a_file) == str:
        file_data = None
        filename = os.path.basename(a_file)

    # Get current maximum number of data
    # * REFACT
    cursor = database.cursor()
    cursor.execute("select max(number) from books")
    try:
        num_max = int(cursor.fetchone()[0])
    except TypeError:  # This mean the DB has no entry
        num_max = 0
    new_number = num_max + 1

    # Get name and suffix
    filename = os.path.basename(filename)
    if "." not in filename:
        filename = f"{new_number}.{filename}"  # Japanese filenames
    basename, suffix = os.path.splitext(filename)

    filetype = suffix.replace(".", "")
    if filetype in ["", None]:
        raise TypeError(f"No suffix. Something wrong with file ({filename})?")

    # Rename the file into a sequential number
    new_filename = str(new_number) + suffix.lower()
    new_file_real = os.path.join(UPLOADDIR_PATH, new_filename)

    # Already existed?
    if os.path.exists(new_file_real) or os.path.isfile(new_file_real):
        raise OSError(f"Collision uploading (file {new_number}). Try again.")

    # Seems OK, Go ahead
    try:
        if file_data:
            # Saving uploaded file
            a_file.save(new_file_real)
        else:
            # Copy original to upload folder
            shutil.copyfile(a_file, new_file_real)

        # Calculate MD5
        with open(new_file_real, "rb") as f:
            hash_md5 = hashlib.md5(f.read()).hexdigest()
            cursor.execute("select * from books where md5 = ?", (hash_md5,))
            data_same_md5 = cursor.fetchall()

        # Collision! which file is the problem?
        if len(data_same_md5) > 0:
            os.remove(new_file_real)  # Clean up the file
            collision_no = ", ".join(
                [f"{dict(d)['number']} {dict(d)['title']}" for d in data_same_md5]
            )
            raise KeyError(f"Same file (No. {collision_no}) exists in the DB")

        # Seems OK so I'll insert entry into DB
        cursor.execute(
            "insert into books (number, title, filetype, md5, tags) values (?, ?, ?, ?, ?)",
            (new_number, basename, filetype, hash_md5, ""),
        )

    except sqlite3.Error as e:
        # Clean up the file
        os.remove(new_file_real)
        # Rollback the database
        cursor.connection.rollback()
        # Raise error
        raise sqlite3.Error(f"SQL Error:{e}", "failed")

    # Finally commit
    cursor.connection.commit()
    return new_number


# Make a thumbnail and text index
def refresh_entry(book_number, database, extract_title=False):
    cursor = database.cursor()
    cursor.execute("select * from books where number = ?", (book_number,))
    entry = cursor.fetchone()

    if entry is None:
        raise IndexError(f"No entry #{book_number} found")

    filetype = entry["filetype"]
    filename = str(book_number) + f".{filetype}"
    file_thumbnail = str(book_number) + ".jpg"
    file_real = os.path.join(UPLOADDIR_PATH, filename)
    thumb_real = os.path.join(THUMBDIR_PATH, file_thumbnail)

    book_title = entry["title"]

    # ---- FILE TYPE DEPENDENT ---- #
    if filetype == "pdf":
        pdf = poppler.load_from_file(file_real)

        # Metadata Extraction
        pagenum = pdf.pages
        thumbnail = pdf2img(file_real)
        if extract_title and pdf.title:
            book_title = pdf.title

        # Generate text index
        # * Maybe really slow. consider optimization.
        page_ngram = [ngram_if_2byte(p) for p in pdf2txt(file_real)]
        index_data = tuple(
            (book_number, pos, text) for pos, text in enumerate(page_ngram)
        )
        cursor.execute("delete from fts where number = ?", (book_number,))
        cursor.executemany(
            "insert into fts (number, page, ngram) values (?, ?, ?)", index_data
        )

    if filetype == "zip":
        pagenum = zipcat(file_real)
        thumbnail, _, _ = zipcat(file_real, page=0)

    if filetype == "epub":
        book = epub.read_epub(file_real)

        # Thumbnail
        try:
            cover_items = [i for i in book.get_items() if type(i) == epub.EpubCover]
            if cover_items:
                # EPUB3
                cover_item = cover_items[0]
            else:
                # EPUB2
                cover_metadata = book.get_metadata("OPF", "cover")
                cover_id = cover_metadata[0][1]["content"]
                cover_item = book.get_item_with_id(cover_id)

            cover_bytes = cover_item.get_content()
            thumbnail = Image.open(io.BytesIO(cover_bytes))

        except IndexError:
            # No thumbnail image was found.
            thumbnail = Image.new("RGB", (100, 140))

        # N-Gram and insert into FTS
        # * We need to reorder the items specified in spine.
        spine_list = [s[0] for s in book.spine if s[1] == "yes"]
        items_in_spine = [book.get_item_with_id(i) for i in spine_list]
        pagenum = len(items_in_spine)

        # * Each 'item' of epub can be long, so here to split them into small chunks.
        # TODO: section size dependent chunk size?
        index_data = []
        for pos, section in enumerate(items_in_spine):
            content = section.get_body_content().decode()
            soup = BeautifulSoup(content, features="html.parser")
            text = soup.get_text()
            lines = [line.strip() for line in text.splitlines()]
            text = " ".join(lines)
            chunk_length = len(text) // EPUB_CHUNK_SPLIT
            if chunk_length == 0:
                continue

            chunks = [
                text[i : i + chunk_length] for i in range(0, len(text), chunk_length)
            ]
            for minipos, chunk in enumerate(chunks):
                minipos = round(minipos / EPUB_CHUNK_SPLIT, 2)
                chunk_ngram = ngram_if_2byte(chunk)
                index_data.append(
                    (
                        book_number,
                        (pos + minipos),
                        chunk_ngram,
                    )
                )

        index_data = tuple(index_data)
        cursor.execute("delete from fts where number = ?", (book_number,))
        cursor.executemany(
            "insert into fts (number, page, ngram) values (?, ?, ?)", index_data
        )

        # Title and author from metadata
        book_title_meta = book.get_metadata("DC", "title")
        if book_title_meta and extract_title:
            book_title = book_title_meta[0][0]

    # ---- COMMON ---- #
    # Page number update
    cursor.execute(
        "update books set pagenum = ? where number = ?", (pagenum, book_number)
    )

    # Shrink and save thumbnail
    thumbnail = thumbnail.convert("RGB")
    thumbnail = ImageOps.contain(thumbnail, (400, 400))
    thumbnail.save(thumb_real, "JPEG")

    # Book title update
    cursor.execute(
        "update books set title = ? where number = ?", (book_title, book_number)
    )

    # Spread view: 1=True, 0=False
    if entry["spread"] is None:
        cursor.execute(
            "update books set spread = ? where number = ?", (True, book_number)
        )

    # L2R view: 1=True, 0=False
    if entry["r2l"] is None:
        cursor.execute(
            "update books set r2l = ? where number = ?", (False, book_number)
        )

    # Hiding: 1=True, 0=False
    if entry["hide"] is None:
        cursor.execute(
            "update books set hide = ? where number = ?", (False, book_number)
        )

    # Update MD5 hash
    with open(file_real, "rb") as f:
        hash_md5 = hashlib.md5(f.read()).hexdigest()
    cursor.execute("update books set md5 = ? where number = ?", (hash_md5, book_number))

    # Finally commit
    cursor.connection.commit()


def remove_entry(number, database):
    cursor = database.cursor()

    # Choose the entry
    cursor.execute("select filetype from books where number = ?", (number,))
    data = sqlresult_to_an_entry(cursor.fetchone())
    filetype = data["filetype"]

    # Deleting
    # * slow! what is the reason?
    cursor.execute("delete from books where number = ?", (number,))
    cursor.execute("delete from fts where number = ?", (number,))

    try:
        # Remove the book file
        os.remove(os.path.join(UPLOADDIR_PATH, str(number) + "." + filetype))
        # Remove thumbnail image
        os.remove(os.path.join(THUMBDIR_PATH, str(number) + ".jpg"))
    except FileNotFoundError:
        pass

    cursor.connection.commit()
