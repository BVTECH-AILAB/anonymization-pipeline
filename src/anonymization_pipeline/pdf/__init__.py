"""PDF access through PDFium (pypdfium2).

PDFium is not thread-safe, not even across different documents: every call, from the job
worker, the page previews and the uploads, runs under ``PDFIUM_LOCK``. The lock is re-entrant so
that a locked section can call helpers that take it again. Open handles (documents, pages, text
pages, bitmaps) never leave the locked section that opened them.
"""

import threading

PDFIUM_LOCK = threading.RLock()
