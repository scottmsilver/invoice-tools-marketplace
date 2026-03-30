import logging

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s.%(msecs)03d [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=logging.INFO
)

import asyncio
import base64
import hashlib
import json
import os
import time
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import json_repair

from .models import DocumentBoundary, DocumentType, Invoice, InvoiceAnalysis, LineItem, ValidationDiscrepancy


class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


class RateLimiter:
    """Token bucket rate limiter for LLM requests."""

    def __init__(self, requests_per_minute: int):
        self.requests_per_minute = requests_per_minute
        self.request_times: deque = deque()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait until a request slot is available."""
        async with self._lock:
            now = time.time()
            # Remove requests older than 1 minute
            while self.request_times and self.request_times[0] < now - 60:
                self.request_times.popleft()

            # If at capacity, wait until oldest request expires
            if len(self.request_times) >= self.requests_per_minute:
                sleep_time = 60 - (now - self.request_times[0]) + 0.1
                if sleep_time > 0:
                    logger.info(
                        f"[RATE_LIMIT] At capacity ({self.requests_per_minute} req/min), waiting {sleep_time:.1f}s"
                    )
                    await asyncio.sleep(sleep_time)
                    # Recursively retry
                    return await self.acquire()

            # Record this request
            self.request_times.append(now)


class LLMService:
    # Class-level rate limiters (shared across instances)
    _rate_limiters: Dict[str, RateLimiter] = {}
    # Class-level in-memory cache
    _cache: Dict[str, str] = {}

    def __init__(
        self,
        provider: str,
        api_key: str,
        model_name: Optional[str] = None,
        enable_cache: bool = True,
        max_concurrent: Optional[int] = None,
        requests_per_minute: Optional[int] = None,
    ):
        """
        Initialize LLM service with support for multiple providers.

        Args:
            provider: LLM provider (openai, anthropic, gemini)
            api_key: API key for the provider
            model_name: Optional model name (uses defaults if not specified)
            enable_cache: Whether to enable in-memory caching (default: True)
            max_concurrent: Max concurrent requests (default: from env or 10)
            requests_per_minute: Rate limit (default: from env or provider default)
        """
        self.provider = LLMProvider(provider.lower())
        self.api_key = api_key
        self.enable_cache = enable_cache

        # Log cache configuration
        if self.enable_cache:
            logger.info("[LLM_CACHE] In-memory cache enabled")
        else:
            logger.info("[LLM_CACHE] Cache disabled")

        # Set default models if not specified
        if model_name is None:
            model_defaults = {
                LLMProvider.OPENAI: "gpt-4o-mini",
                LLMProvider.ANTHROPIC: "claude-3-5-sonnet-20241022",
                LLMProvider.GEMINI: "gemini-3.1-flash-lite-preview",
            }
            self.model_name = model_defaults[self.provider]
        else:
            self.model_name = model_name

        # Configure rate limits
        if requests_per_minute is None:
            # Try provider-specific env var, then model-specific, then default
            env_key = f"{self.provider.value.upper()}_REQUESTS_PER_MINUTE"
            model_key = f"{self.model_name.upper().replace('-', '_').replace('.', '_')}_REQUESTS_PER_MINUTE"

            # Default rate limits by provider/model
            rate_defaults = {
                "gemini-3.1-flash-lite-preview": 4000,
                LLMProvider.OPENAI: 500,
                LLMProvider.ANTHROPIC: 50,
                LLMProvider.GEMINI: 1000,
            }

            requests_per_minute = int(
                os.getenv(
                    model_key,
                    os.getenv(env_key, rate_defaults.get(self.model_name, rate_defaults.get(self.provider, 100))),
                )
            )

        self.requests_per_minute = requests_per_minute

        # Set up rate limiter (shared across instances of same provider)
        limiter_key = f"{self.provider.value}:{self.model_name}"
        if limiter_key not in LLMService._rate_limiters:
            LLMService._rate_limiters[limiter_key] = RateLimiter(requests_per_minute)
        self.rate_limiter = LLMService._rate_limiters[limiter_key]

        # Configure concurrency limit
        if max_concurrent is None:
            env_key = f"{self.provider.value.upper()}_MAX_CONCURRENT"
            max_concurrent = int(os.getenv(env_key, os.getenv("LLM_MAX_CONCURRENT", "10")))
        self.max_concurrent = max_concurrent
        self._semaphore = None  # Lazy-initialized per event loop

        logger.info(
            f"[LLM_RATE_LIMIT] {self.provider.value}/{self.model_name}: {requests_per_minute} req/min, max {max_concurrent} concurrent"
        )

        # Initialize the appropriate client
        if self.provider == LLMProvider.OPENAI:
            from openai import OpenAI

            self.client = OpenAI(api_key=api_key)
        elif self.provider == LLMProvider.ANTHROPIC:
            from anthropic import Anthropic

            self.client = Anthropic(api_key=api_key)
        elif self.provider == LLMProvider.GEMINI:
            from google import genai

            self.client = genai.Client(api_key=api_key)

    def _get_cache_key(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        pdf_bytes: Optional[bytes] = None,
        pdf_source_id: Optional[str] = None,
    ) -> str:
        """
        Generate a cache key based on all relevant parameters.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            pdf_bytes: Optional PDF file bytes
            pdf_source_id: Optional unique identifier for the source PDF (to avoid re-extracting same pages)

        Returns:
            SHA256 hash of all parameters
        """
        # Build cache key from all relevant data
        cache_data = {
            "provider": self.provider.value,
            "model": self.model_name,
            "prompt": prompt,
            "system_prompt": system_prompt,
        }

        # Create hash of cache data
        hasher = hashlib.sha256()
        cache_json = json.dumps(cache_data, sort_keys=True)
        hasher.update(cache_json.encode("utf-8"))

        # Use PDF source ID if provided (stable across re-extractions)
        # Otherwise fall back to PDF bytes hash (unstable due to metadata changes)
        pdf_identifier = None
        if pdf_source_id:
            pdf_identifier = f"source:{pdf_source_id}"
            hasher.update(pdf_identifier.encode("utf-8"))
        elif pdf_bytes:
            pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
            pdf_identifier = f"bytes:{pdf_hash[:16]}"
            hasher.update(pdf_hash.encode("utf-8"))

        cache_key = hasher.hexdigest()

        # Debug logging
        logger.info(f"[LLM_CACHE] Cache key calculation:")
        logger.info(f"[LLM_CACHE]   - Provider: {self.provider.value}")
        logger.info(f"[LLM_CACHE]   - Model: {self.model_name}")
        logger.info(f"[LLM_CACHE]   - Prompt length: {len(prompt)} chars")
        logger.info(f"[LLM_CACHE]   - Prompt preview: {prompt[:100]}...")
        logger.info(f"[LLM_CACHE]   - System prompt: {'Yes' if system_prompt else 'No'}")
        logger.info(f"[LLM_CACHE]   - PDF: {pdf_identifier if pdf_identifier else 'No'}")
        logger.info(f"[LLM_CACHE]   - Final cache key: {cache_key}")

        return cache_key

    async def _get_from_cache(self, cache_key: str) -> Optional[str]:
        """Get cached response if available from in-memory cache."""
        if not self.enable_cache:
            return None

        cached = LLMService._cache.get(cache_key)
        if cached is not None:
            logger.info(f"[LLM_CACHE] Cache HIT: {cache_key[:16]}...")
            return cached

        logger.info(f"[LLM_CACHE] Cache MISS: {cache_key[:16]}...")
        return None

    async def _save_to_cache(self, cache_key: str, response: str, prompt: str, system_prompt: Optional[str] = None):
        """Save response to in-memory cache."""
        if not self.enable_cache:
            return

        LLMService._cache[cache_key] = response
        logger.info(f"[LLM_CACHE] Cache SAVE: {cache_key[:16]}...")

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Get or create a semaphore for the current event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop, create semaphore without loop binding
            if self._semaphore is None:
                self._semaphore = asyncio.Semaphore(self.max_concurrent)
            return self._semaphore

        # Check if semaphore exists and is bound to current loop
        if self._semaphore is None or self._semaphore._loop != loop:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    async def _call_llm_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        pdf_bytes: Optional[bytes] = None,
        pdf_source_id: Optional[str] = None,
        bypass_cache: bool = False,
    ) -> str:
        """
        Async version of _call_llm with rate limiting and concurrency control.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            pdf_bytes: Optional PDF file bytes for vision models
            pdf_source_id: Optional stable identifier for the PDF source (for better caching)
            bypass_cache: If True, skip cache lookup and force fresh LLM call (for debugging)

        Returns:
            Response text from LLM
        """
        # Check cache first (async) unless bypassing
        cache_key = self._get_cache_key(prompt, system_prompt, pdf_bytes, pdf_source_id)
        if not bypass_cache:
            cached_response = await self._get_from_cache(cache_key)
            if cached_response is not None:
                return cached_response
        else:
            logger.info(f"[LLM_CACHE] Cache BYPASS requested - forcing fresh LLM call")

        # Apply rate limiting and concurrency control
        await self.rate_limiter.acquire()
        semaphore = self._get_semaphore()
        async with semaphore:
            # Double-check cache after acquiring lock (avoid duplicate calls) unless bypassing
            if not bypass_cache:
                cached_response = await self._get_from_cache(cache_key)
                if cached_response is not None:
                    return cached_response

            # Cache miss (or bypass) - call LLM (run synchronous client in thread pool)
            logger.info(f"[LLM_CACHE] Cache {'BYPASS' if bypass_cache else 'MISS'}: {cache_key[:16]}... - Calling LLM")
            result = await asyncio.to_thread(
                self._call_llm_sync, prompt, system_prompt, pdf_bytes, pdf_source_id, cache_key
            )

            # Save to cache after getting result
            if not bypass_cache:
                await self._save_to_cache(cache_key, result, prompt, system_prompt)

            return result

    def _call_llm_sync(
        self,
        prompt: str,
        system_prompt: Optional[str],
        pdf_bytes: Optional[bytes],
        pdf_source_id: Optional[str],
        cache_key: str,
    ) -> str:
        """
        Synchronous LLM call (used by async wrapper).

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            pdf_bytes: Optional PDF file bytes
            pdf_source_id: Optional PDF source identifier
            cache_key: Pre-computed cache key

        Returns:
            Response text from LLM
        """
        try:
            if self.provider == LLMProvider.OPENAI:
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})

                # OpenAI doesn't support PDF directly, convert to images if needed
                if pdf_bytes:
                    # For now, just use text extraction fallback
                    # TODO: Convert PDF pages to images for vision models
                    messages.append({"role": "user", "content": prompt})
                else:
                    messages.append({"role": "user", "content": prompt})

                response = self.client.chat.completions.create(
                    model=self.model_name, messages=messages, temperature=0.1
                )
                result = response.choices[0].message.content
                return result

            elif self.provider == LLMProvider.ANTHROPIC:
                # Anthropic supports PDF natively
                if pdf_bytes:
                    content = [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.b64encode(pdf_bytes).decode("utf-8"),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ]
                else:
                    content = prompt

                kwargs = {
                    "model": self.model_name,
                    "max_tokens": 4096,
                    "temperature": 0.1,
                    "messages": [{"role": "user", "content": content}],
                }
                if system_prompt:
                    kwargs["system"] = system_prompt

                response = self.client.messages.create(**kwargs)
                result = response.content[0].text
                return result

            elif self.provider == LLMProvider.GEMINI:
                # Gemini supports PDF natively via google-genai
                from google.genai import types

                contents = []
                if pdf_bytes:
                    contents.append(types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"))
                if system_prompt:
                    full_prompt = f"{system_prompt}\n\n{prompt}"
                else:
                    full_prompt = prompt
                contents.append(full_prompt)
                response = self.client.models.generate_content(model=self.model_name, contents=contents)
                result = response.text
                return result

        except Exception as e:
            logger.info(f"Error calling {self.provider.value}: {e}")
            raise

    def _call_llm(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        pdf_bytes: Optional[bytes] = None,
        pdf_source_id: Optional[str] = None,
        bypass_cache: bool = False,
    ) -> str:
        """
        Synchronous wrapper for _call_llm_async.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            pdf_bytes: Optional PDF file bytes for vision models
            pdf_source_id: Optional stable identifier for the PDF source (for better caching)
            bypass_cache: If True, skip cache lookup and force fresh LLM call (for debugging)

        Returns:
            Response text from LLM
        """
        # Run async version in event loop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(
            self._call_llm_async(prompt, system_prompt, pdf_bytes, pdf_source_id, bypass_cache)
        )

    def _parse_json_with_repair(self, json_str: str, context: str = "") -> Optional[Any]:
        """
        Parse JSON with automatic repair for common errors.

        Args:
            json_str: The JSON string to parse
            context: Context for logging (e.g., "invoice extraction")

        Returns:
            Parsed JSON object or None if unrecoverable
        """
        # First try standard parsing
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.info(f"[JSON_REPAIR] Initial parse failed for {context}: {e}")

        # Tier 1: Try json_repair
        try:
            repaired = json_repair.loads(json_str)
            logger.info(f"[JSON_REPAIR] Successfully repaired JSON for {context}")
            return repaired
        except Exception as repair_error:
            logger.info(f"[JSON_REPAIR] json_repair failed for {context}: {repair_error}")

        # Tier 2: Ask LLM to fix the JSON
        try:
            logger.info(f"[JSON_REPAIR] Attempting LLM repair for {context}")
            fix_prompt = f"""The following JSON is malformed. Please fix it and return ONLY the corrected JSON, no explanations:

{json_str}

Return only valid JSON that preserves all the data."""

            # Call LLM without caching (bypass_cache=True)
            fixed_response = self._call_llm(fix_prompt, bypass_cache=True)

            # Extract JSON from response
            start_idx = fixed_response.find("{")
            if start_idx == -1:
                start_idx = fixed_response.find("[")
            end_idx = fixed_response.rfind("}")
            if end_idx == -1 or end_idx < start_idx:
                end_idx = fixed_response.rfind("]")

            if start_idx != -1 and end_idx > start_idx:
                fixed_json_str = fixed_response[start_idx : end_idx + 1]

                # Try json_repair on the LLM's attempt
                try:
                    result = json_repair.loads(fixed_json_str)
                    logger.info(f"[JSON_REPAIR] LLM + json_repair succeeded for {context}")
                    return result
                except Exception as e:
                    logger.info(f"[JSON_REPAIR] LLM repair + json_repair failed for {context}: {e}")

        except Exception as llm_error:
            logger.info(f"[JSON_REPAIR] LLM repair attempt failed for {context}: {llm_error}")

        # All attempts failed
        logger.info(f"[JSON_REPAIR] All repair attempts failed for {context}")
        return None

    def _fill_boundary_gaps(
        self, boundaries: List[DocumentBoundary], pages_text: List[str], exclude_pages: List[int] = None
    ) -> List[DocumentBoundary]:
        """
        Ensure all non-excluded pages are covered by boundaries. Add single-page boundaries for any missing pages.

        Args:
            boundaries: List of detected boundaries
            pages_text: List of text content from each page
            exclude_pages: List of page numbers to exclude from gap filling (e.g., parent pages)

        Returns:
            Updated list of boundaries with gaps filled
        """
        exclude_pages = exclude_pages or []
        covered_pages = set()
        for boundary in boundaries:
            covered_pages.update(boundary.pages)

        total_pages = len(pages_text)
        # Only check non-excluded pages for gaps
        all_pages = set(range(1, total_pages + 1)) - set(exclude_pages)
        missing_pages = all_pages - covered_pages

        if missing_pages:
            logger.info(f"[BOUNDARY_GAP] Found {len(missing_pages)} missing pages: {sorted(missing_pages)}")
            for page_num in sorted(missing_pages):
                page_text = pages_text[page_num - 1]
                preview = page_text[:100].replace("\n", " ") if page_text else "No text"

                new_boundary = DocumentBoundary(
                    pages=[page_num],
                    type=DocumentType.SUPPORTING,
                    confidence=0.5,
                    text_preview=preview,
                )
                boundaries.append(new_boundary)
                logger.info(f"[BOUNDARY_GAP] Added boundary for page {page_num}")

        return boundaries

    def detect_document_boundaries(
        self,
        pages_text: List[str],
        pdf_bytes: Optional[bytes] = None,
        pdf_source_id: Optional[str] = None,
        exclude_pages: Optional[List[int]] = None,
        bypass_cache: bool = False,
    ) -> List[DocumentBoundary]:
        """
        Detect document boundaries in a multi-page PDF.

        Args:
            pages_text: List of text content from each page
            pdf_bytes: Optional PDF bytes for vision models to see layout/formatting
            pdf_source_id: Optional stable identifier for caching (e.g., "upload_id:pages")
            exclude_pages: Optional list of page numbers to exclude from segmentation (e.g., parent pages)
            bypass_cache: If True, skip cache lookup and force fresh LLM call (for debugging)

        Returns:
            List of DocumentBoundary objects (with gaps filled for non-excluded pages)
        """
        exclude_pages = exclude_pages or []

        # Create a summary of each page with more context (excluding specified pages)
        pages_summary = []
        visual_page_num = 1  # Track page number in the visual PDF (after exclusions)
        for i, text in enumerate(pages_text):
            page_num = i + 1
            if page_num in exclude_pages:
                continue  # Skip excluded pages
            # Use more text for better detection
            preview = text[:800].replace("\n", " ")
            # When pdf_bytes is provided, use visual page numbers that match the PDF
            # Otherwise use original page numbers
            label_page_num = visual_page_num if pdf_bytes else page_num
            pages_summary.append(f"Page {label_page_num}:\n{preview}...\n")
            visual_page_num += 1

        prompt = f"""You are analyzing a multi-page PDF containing multiple separate invoices/receipts that need to be segmented.

TASK: Identify where each separate document starts and ends. Documents can be 1-4 pages, including supporting materials.

CRITICAL GROUPING RULES - Keep pages together when:

1. MULTI-PAGE INVOICES (Same document continuation):
   - "Page X of Y" indicators on consecutive pages (e.g., "Page 1 of 2", "Page 2 of 2")
   - Same invoice/order number on both pages
   - Same vendor/company name on both pages

2. PAYMENT PROOF + INVOICE (Related transaction documents):
   - Credit card receipt followed by invoice with MATCHING total amount
   - Check copy followed by invoice with MATCHING amount
   - Payment confirmation followed by related invoice
   - Wire transfer followed by invoice
   -> These are proof of payment for the same transaction and MUST stay together

3. INVOICE + SUPPORTING DOCUMENTATION:
   - Invoice that MENTIONS "see attached" or "supporting documentation"
   - Invoice followed by breakdown/details/calculations
   - Invoice followed by work order, timesheet, or material list
   - Invoice followed by backup documentation (no invoice number)
   -> The supporting pages provide detail for the main invoice

4. INVOICE + CREDIT MEMO/ADJUSTMENT:
   - Original invoice followed by credit memo or adjustment for same transaction
   - Invoice followed by change order or modification

SEPARATION RULES - Start a NEW document when:
   - Different vendor/company name (unless it's payment proof)
   - Different invoice number (not just missing - actually different)
   - Different transaction date by more than a few days
   - Clear visual separation with new header/logo
   - Unrelated amounts (not matching totals)

DECISION PROCESS:
For each page, check if it should be grouped with the next page:
1. Is it Page X of Y with matching invoice number on next page? -> GROUP
2. Is it a payment proof (credit card/check) with matching amount on next page? -> GROUP
3. Does it mention "attached" or "supporting" with related content on next page? -> GROUP
4. Is the next page a breakdown/detail page without its own invoice number? -> GROUP
5. Otherwise -> SEPARATE

EXAMPLES:

Example 1 - Credit Card + Invoice (KEEP TOGETHER):
  Page 5: "Visa ending 4567, Amount: $5,204.70, Date: 9/15/25"
  Page 6: "Enbridge Gas, Invoice #SJ0010029621, Total Due: $5,204.70"
  -> Result: {{"pages": [5, 6], "reasoning": "Credit card payment proof followed by invoice with MATCHING amount $5,204.70"}}

Example 2 - Invoice with Supporting Documentation (KEEP TOGETHER):
  Page 9: "Summit Fabricators, Invoice #7001, $32,500, See attached breakdown"
  Page 10: "Structural Steel Breakdown: Contract $21,000 + Changes $11,500 = $32,500"
  -> Result: {{"pages": [9, 10], "reasoning": "Invoice mentions 'attached breakdown' and page 10 is the supporting calculation"}}

Example 3 - Invoice with Referenced Supporting Material (KEEP TOGETHER):
  Page 3: "Summit Co, Invoice #7005, $500, Inspection per National Engineering report"
  Page 4: "National Engineering, Inspection Report, Project: Main St Renovation"
  -> Result: {{"pages": [3, 4], "reasoning": "Invoice references National Engineering, next page is that referenced report"}}

Example 4 - Two SEPARATE invoices from same vendor (SPLIT):
  Page 8: "ABC Corp, Invoice #123, Total: $1,500"
  Page 9: "ABC Corp, Invoice #124, Total: $2,000"
  -> Result: Two separate documents (different invoice numbers, unrelated amounts)

PAGES TO ANALYZE:
{chr(10).join(pages_summary)}

Return a JSON array grouping pages into separate documents:
[
  {{
    "pages": [1],
    "type": "supporting_invoice",
    "confidence": 0.95,
    "reasoning": "Single page invoice from ABC Corp, invoice #123, no continuation markers"
  }},
  {{
    "pages": [2, 3],
    "type": "supporting_invoice",
    "confidence": 0.90,
    "reasoning": "Two-page invoice from XYZ Inc, invoice #456, marked 'Page 1 of 2' and 'Page 2 of 2' with same invoice number"
  }},
  ...
]

IMPORTANT:
- All documents should be type "supporting_invoice" (these are supporting documents, not parent invoices)
- ALWAYS check for payment proof + invoice relationships (matching amounts)
- ALWAYS check for invoice + supporting documentation relationships
- Look for references like "see attached", "per [vendor] report", "supporting documentation"
- Pages without invoice numbers are often supporting docs for the previous page
- Credit card receipts/checks are payment proof, not separate invoices
- If consecutive pages have matching totals, they likely belong together
- Single-page invoices are common, but 2-3 page groups are also common when supporting docs are included

Return ONLY valid JSON, no markdown, no explanation."""

        # If PDF bytes provided, use vision to see actual layout
        if pdf_bytes:
            logger.info("PDF BYTES PROVIDED")
            response = self._call_llm(
                prompt, pdf_bytes=pdf_bytes, pdf_source_id=pdf_source_id, bypass_cache=bypass_cache
            )
        else:
            response = self._call_llm(prompt, pdf_source_id=pdf_source_id, bypass_cache=bypass_cache)

        # Extract JSON from response
        try:
            # Try to find JSON in the response
            start_idx = response.find("[")
            end_idx = response.rfind("]") + 1
            if start_idx != -1 and end_idx > start_idx:
                json_str = response[start_idx:end_idx]
                boundaries_data = self._parse_json_with_repair(json_str, "boundary detection")
                if boundaries_data is None:
                    raise ValueError("Failed to parse boundaries JSON after repair attempts")

                boundaries = []
                # Create mapping from visual page numbers to original page numbers
                visual_to_original = {}
                if pdf_bytes and exclude_pages:
                    visual_num = 1
                    for orig_num in range(1, len(pages_text) + 1):
                        if orig_num not in exclude_pages:
                            visual_to_original[visual_num] = orig_num
                            visual_num += 1

                # First pass: validate page numbers are within expected range
                max_visual_page = len(visual_to_original) if visual_to_original else len(pages_text)
                valid_boundaries_data = []
                for idx, item in enumerate(boundaries_data):
                    pages = item.get("pages", [])
                    # Check if all pages are within valid range (1 to max_visual_page)
                    if all(1 <= p <= max_visual_page for p in pages):
                        valid_boundaries_data.append(item)
                    else:
                        logger.info(
                            f"[BOUNDARY_FILTER] Skipping boundary {idx+1} with out-of-range pages: {pages} (max valid page: {max_visual_page})"
                        )

                for idx, item in enumerate(valid_boundaries_data):
                    pages = item.get("pages", [])

                    # If pdf_bytes was provided, remap visual page numbers back to original
                    if pdf_bytes and exclude_pages and visual_to_original:
                        pages = [visual_to_original.get(p, p) for p in pages]

                    # Filter out any pages that are in exclude_pages
                    if exclude_pages:
                        pages = [p for p in pages if p not in exclude_pages]
                        if not pages:
                            # Skip this boundary entirely if all pages were excluded
                            logger.info(f"[BOUNDARY_FILTER] Skipping boundary - all pages were in exclude list")
                            continue

                    # Filter out any pages that are out of range
                    pages = [p for p in pages if 1 <= p <= len(pages_text)]
                    if not pages:
                        logger.info(f"[BOUNDARY_FILTER] Skipping boundary - all pages were out of range")
                        continue

                    # Get preview text from first page
                    text_preview = pages_text[pages[0] - 1][:200] if pages else ""

                    # Map LLM response to our enum values
                    type_str = item.get("type", "other")
                    type_map = {
                        "parent_invoice": "parent",
                        "supporting_invoice": "supporting",
                        "parent": "parent",
                        "supporting": "supporting",
                        "other": "other",
                    }
                    doc_type = type_map.get(type_str, "other")

                    boundaries.append(
                        DocumentBoundary(
                            pages=pages,
                            type=DocumentType(doc_type),
                            confidence=item.get("confidence", 0.5),
                            text_preview=text_preview,
                        )
                    )

                # Fill gaps: ensure all non-excluded pages are covered
                boundaries = self._fill_boundary_gaps(boundaries, pages_text, exclude_pages)
                return boundaries
        except (json.JSONDecodeError, ValueError) as e:
            logger.info(f"Error parsing LLM response: {e}")
            logger.info(f"Response was: {response}")

            # Fallback: treat each page as a separate document
            return [
                DocumentBoundary(pages=[i + 1], type=DocumentType.OTHER, confidence=0.3, text_preview=text[:200])
                for i, text in enumerate(pages_text)
            ]

    def extract_invoice_data(
        self,
        document_type: DocumentType,
        pdf_bytes: bytes,
        pdf_source_id: Optional[str] = None,
        bypass_cache: bool = False,
    ) -> Invoice:
        """
        Extract structured data from invoice PDF using vision models.

        Args:
            document_type: Type of document (parent or supporting)
            pdf_bytes: PDF file bytes
            pdf_source_id: Optional stable identifier for caching (e.g., "upload_id:pages")
            bypass_cache: If True, skip cache lookup and force fresh LLM call (for debugging)

        Returns:
            Invoice object with extracted data (or list of Invoice objects if multiple found)
        """
        prompt = """Extract ALL structured data from the invoice PDF document. Read the PDF carefully and extract:
- vendor: The company/person providing the invoice (may be labeled as "Supplier", "Vendor", "From", "Bill From", "Sold By", etc.)
- invoice_number: The invoice or reference number
- date: The invoice date (YYYY-MM-DD format)
- recipient: The customer/person receiving the invoice
- total_amount: The total amount of the invoice
- amount_due: The amount currently due (e.g., "Deposit Due", "Balance Due", "Amount Due") - set to null if it equals total_amount or if not explicitly shown
- line_items: Extract each net new charge, credit, or deduction - but NOT aggregation/summary rows

CRITICAL - Handling Credits and Negative Amounts:
- Amounts in parentheses (e.g., ($4,332.22)) are NEGATIVE numbers (credits/deductions)
- Lines labeled "Credit" or shown in red are NEGATIVE amounts
- If the document is a CREDIT MEMO or CREDIT, the total_amount should be NEGATIVE
- Convert parenthesized amounts to negative: ($4,332.22) -> -4332.22
- Preserve the negative sign in the JSON output

CRITICAL - Handling Crossed-Out, Struck-Through, or Void Items:
- If a line item has been crossed out, struck through, or marked as void, DO NOT include it
- If text or amounts are visually struck through (line drawn through them), they are cancelled and should be EXCLUDED
- If an invoice section is marked as "VOID" or "CANCELLED", ignore all line items in that section
- Look for visual strikethrough formatting, hand-drawn lines through items, or explicit "VOID" markings
- Only include line items and totals that represent the FINAL, VALID charges
- Example: If you see "$100" with a line through it and "$150" written next to it, use $150

CRITICAL - Understanding Line Items vs Aggregation Rows:
A LINE ITEM is a NET NEW charge, fee, tax, credit, or deduction that changes the total amount.
An AGGREGATION ROW is a sum of previous line items and does NOT add anything new to the total.

CRITICAL - Handling Timesheets:
If the document is a TIMESHEET showing hours worked with labor rates:
- Detail rows show HOURS worked and HOURLY RATES (e.g., "9.45 hours @ $45/hr")
- Total rows show DOLLAR AMOUNTS charged (e.g., "Jobsite Labor Total: $2,464.65")
- Extract ONLY the labor category TOTAL rows (dollar amounts), NOT individual time entries
- The totals are the actual charges - time entries are just supporting detail

Example Timesheet:
  X EXCLUDE: "Jose - 9.45 hours @ $45/hr" (time entry detail)
  V INCLUDE: "Jobsite Labor Total: $2,464.65" (actual charge)
  V INCLUDE: "Painting Labor Total: $3,030.30" (actual charge)

What to INCLUDE in line_items (net new charges/credits):
V Individual products, materials, or supplies purchased
V Services provided (labor, installation, engineering, etc.)
V Labor category totals from timesheets (e.g., "Jobsite Labor Total", "Painting Labor Total")
V Equipment rentals
V Delivery fees, inspection fees, permit fees
V Tax / Sales Tax (this is a NEW charge calculated from subtotal, adds to total)
V Builders compensation / markup charges (NEW charge, adds to total)
V Credits, adjustments, or refunds (NEW deduction, reduces total)
V Retention held (NEW deduction, reduces total)
V ANY charge or credit that contributes to the final total

What to EXCLUDE from line_items (these are AGGREGATIONS, NOT charges):
X Individual time entries on timesheets (hours worked - these are detail, not charges)
X Subtotal - this is just summing the items above it, adds nothing new
X Total / Grand Total / Invoice Total / Draw Total - final sum, adds nothing new
X Balance Due / Amount Due - calculation of what's owed, adds nothing new
X Line Subtotal - intermediate sum of a group of items
X Any row that is clearly labeled as summing/aggregating rows above it

KEY DISTINCTION:
- Tax: INCLUDE (it's a new charge: subtotal + tax = total)
- Subtotal: EXCLUDE (it's just a sum: item1 + item2 + ... = subtotal)
- Builders Comp: INCLUDE (it's a new charge: subtotal + builders comp = new subtotal)
- Total: EXCLUDE (it's just a sum: subtotal + tax + fees = total)
- Labor Total: INCLUDE (it's a charge from timesheet hours)
- Time Entry: EXCLUDE (it's just detail showing how labor total was calculated)

THINK: "Does this row add a new charge/credit, or is it just adding up previous rows?"
- If it adds a NEW charge/credit -> INCLUDE
- If it's just summing -> EXCLUDE

EXAMPLES from a construction draw request:
INCLUDE: "Able Access Elevator - Elevator: $8,650.00" (product purchase)
INCLUDE: "Builders Comp: $23,340.24" (new markup charge)
INCLUDE: "Retention Held: -$7,780.08" (new deduction)
INCLUDE: "Tax: $1,234.56" (new tax charge)
INCLUDE: "Credit for BC on Demo Overages: -$960.00" (new credit)
EXCLUDE: "Subtotal: $155,601.62" (just summing items above, no new charge)
EXCLUDE: "August Draw Total: $163,065.53" (final sum of everything, no new charge)

Return ONLY valid JSON in this structure (do NOT use placeholder values - extract real data from the PDF):
{
  "vendor": "actual vendor name from PDF",
  "invoice_number": "actual invoice number",
  "date": "actual date",
  "recipient": "actual recipient name",
  "total_amount": actual_number,
  "amount_due": actual_number_or_null,
  "line_items": [
    {
      "description": "actual item description",
      "vendor": "vendor/supplier name for this line item if specified (may be labeled 'Supplier', 'Vendor', or appear before the description)",
      "quantity": actual_number,
      "unit_price": actual_number,
      "total": actual_number
    }
  ]
}

IMPORTANT: Extract the ACTUAL data from the PDF. Do not return placeholder values. Do NOT include subtotal/total rows that merely aggregate line items above them."""

        response = self._call_llm(prompt, pdf_bytes=pdf_bytes, pdf_source_id=pdf_source_id, bypass_cache=bypass_cache)

        # Extract JSON from response
        try:
            # Try to find JSON array first (multiple invoices)
            array_start = response.find("[")
            array_end = response.rfind("]") + 1
            obj_start = response.find("{")

            invoices_data = []

            # If we have an array and it comes before any object, parse as array
            if array_start != -1 and (obj_start == -1 or array_start < obj_start):
                json_str = response[array_start:array_end]
                parsed = self._parse_json_with_repair(json_str, "invoice extraction (array)")
                if parsed is None:
                    raise ValueError("Failed to parse invoice JSON array after repair attempts")

                if isinstance(parsed, list):
                    invoices_data = parsed
                    if len(parsed) > 1:
                        logger.info(f"[LLM] Found {len(parsed)} invoices on same pages")
                else:
                    invoices_data = [parsed]
            else:
                # Single invoice object
                obj_end = response.rfind("}") + 1
                if obj_start != -1 and obj_end > obj_start:
                    json_str = response[obj_start:obj_end]
                    parsed = self._parse_json_with_repair(json_str, "invoice extraction (object)")
                    if parsed is None:
                        raise ValueError("Failed to parse invoice JSON object after repair attempts")
                    invoices_data = [parsed]

            # Convert each invoice data to Invoice object
            result_invoices = []
            for data in invoices_data:
                # Convert line items to LineItem objects
                line_items = []
                for item in data.get("line_items", []):
                    line_items.append(
                        LineItem(
                            description=item.get("description", ""),
                            vendor=item.get("vendor"),
                            quantity=item.get("quantity"),
                            unit_price=item.get("unit_price"),
                            total=item.get("total"),  # Allow None for invoices without itemized prices
                        )
                    )

                # Check if all line items have null totals and set warning
                line_items_warning = None
                if line_items:
                    items_with_totals = [item for item in line_items if item.total is not None]
                    if not items_with_totals:
                        line_items_warning = "Individual line item prices not available - bulk order or quote format"
                        logger.info(
                            f"[LLM] Warning: Invoice {data.get('invoice_number', 'unknown')} has line items but no individual prices"
                        )

                result_invoices.append(
                    Invoice(
                        type=document_type,
                        vendor=data.get("vendor"),
                        invoice_number=data.get("invoice_number"),
                        date=data.get("date"),
                        recipient=data.get("recipient"),
                        total_amount=data.get("total_amount"),
                        amount_due=data.get("amount_due"),
                        line_items=line_items,
                        line_items_warning=line_items_warning,
                        extracted_text=None,
                        status="processed",
                    )
                )

            # Return list of invoices (for backward compatibility, return first if single)
            if len(result_invoices) == 1:
                return result_invoices[0]
            else:
                return result_invoices
        except (json.JSONDecodeError, ValueError) as e:
            logger.info(f"Error parsing invoice data: {e}")
            logger.info(f"Response was: {response}")

        # Fallback: return empty invoice
        return Invoice(type=document_type, extracted_text=response, status="pending")

    def validate_extraction(
        self, extracted_invoice: Invoice, pdf_bytes: Optional[bytes] = None, original_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validate extracted invoice data against original PDF or text.

        Args:
            extracted_invoice: Extracted Invoice object
            pdf_bytes: Original PDF file bytes (preferred)
            original_text: Original text from the invoice (fallback)

        Returns:
            Dictionary with validation results
        """
        # Convert invoice to dict for comparison
        invoice_dict = {
            "vendor": extracted_invoice.vendor,
            "invoice_number": extracted_invoice.invoice_number,
            "date": extracted_invoice.date,
            "recipient": extracted_invoice.recipient,
            "total_amount": extracted_invoice.total_amount,
            "line_items": [
                {
                    "description": item.description,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "total": item.total,
                }
                for item in extracted_invoice.line_items
            ],
        }

        if pdf_bytes:
            prompt = f"""Compare the extracted invoice data against the original invoice PDF and identify any SIGNIFICANT discrepancies.

Extracted Data:
{json.dumps(invoice_dict, indent=2)}

Validation Guidelines:
1. Minor formatting differences (spacing, punctuation, abbreviations) are OK
2. Dates in different formats (2024-01-15 vs Jan 15, 2024) are OK if the date is correct
3. Vendor/recipient name variations (Inc. vs Incorporated, LLC vs L.L.C.) are OK
4. Rounding differences of less than $1 are OK
5. Only flag SIGNIFICANT errors that would affect payment or understanding

Check these fields:
- Vendor name (allow minor formatting differences)
- Invoice number (must match exactly)
- Date (allow format differences if same date)
- Recipient/customer name (allow minor formatting differences)
- Total amount (must match within $1)
- Line items: descriptions should be accurate, totals should match within $1

Mark as "is_valid: true" if there are NO significant discrepancies.
Mark as "is_valid: false" ONLY if there are errors that would affect business decisions.

Return JSON in this exact format:
{{
  "is_valid": true,
  "confidence_score": 0.95,
  "discrepancies": [],
  "notes": "All key fields match. Minor formatting differences are acceptable."
}}

OR if there are significant issues:
{{
  "is_valid": false,
  "confidence_score": 0.6,
  "discrepancies": [
    {{
      "field": "total_amount",
      "expected": "5000.00",
      "actual": "4500.00",
      "severity": "high"
    }}
  ],
  "notes": "Total amount does not match."
}}

Only return valid JSON, no other text."""
        else:
            prompt = f"""Compare the extracted invoice data against the original invoice text and identify any SIGNIFICANT discrepancies.

Original Invoice Text:
{original_text[:4000]}

Extracted Data:
{json.dumps(invoice_dict, indent=2)}

Validation Guidelines:
1. Minor formatting differences (spacing, punctuation, abbreviations) are OK
2. Dates in different formats (2024-01-15 vs Jan 15, 2024) are OK if the date is correct
3. Vendor/recipient name variations (Inc. vs Incorporated, LLC vs L.L.C.) are OK
4. Rounding differences of less than $1 are OK
5. Only flag SIGNIFICANT errors that would affect payment or understanding

Check these fields:
- Vendor name (allow minor formatting differences)
- Invoice number (must match exactly)
- Date (allow format differences if same date)
- Recipient/customer name (allow minor formatting differences)
- Total amount (must match within $1)
- Line items: descriptions should be accurate, totals should match within $1

Mark as "is_valid: true" if there are NO significant discrepancies.
Mark as "is_valid: false" ONLY if there are errors that would affect business decisions.

Return JSON in this exact format:
{{{{
  "is_valid": true,
  "confidence_score": 0.95,
  "discrepancies": [],
  "notes": "All key fields match. Minor formatting differences are acceptable."
}}}}

OR if there are significant issues:
{{{{
  "is_valid": false,
  "confidence_score": 0.6,
  "discrepancies": [
    {{{{
      "field": "total_amount",
      "expected": "5000.00",
      "actual": "4500.00",
      "severity": "high"
    }}}}
  ],
  "notes": "Total amount does not match."
}}}}

Only return valid JSON, no other text."""

        response = self._call_llm(prompt, pdf_bytes=pdf_bytes)

        # Extract JSON from response
        try:
            start_idx = response.find("{")
            end_idx = response.rfind("}") + 1
            if start_idx != -1 and end_idx > start_idx:
                json_str = response[start_idx:end_idx]
                data = self._parse_json_with_repair(json_str, "invoice validation")
                if data is None:
                    raise ValueError("Failed to parse validation JSON after repair attempts")

                # Convert discrepancies to ValidationDiscrepancy objects
                discrepancies = []
                for disc in data.get("discrepancies", []):
                    discrepancies.append(
                        ValidationDiscrepancy(
                            field=disc.get("field", "unknown"),
                            expected=str(disc.get("expected", "")),
                            actual=str(disc.get("actual", "")),
                            severity=disc.get("severity", "medium"),
                        )
                    )

                return {
                    "is_valid": data.get("is_valid", False),
                    "confidence_score": data.get("confidence_score", 0.5),
                    "discrepancies": discrepancies,
                    "notes": data.get("notes"),
                }
        except (json.JSONDecodeError, ValueError) as e:
            logger.info(f"Error parsing validation response: {e}")
            logger.info(f"Response was: {response}")

        # Fallback: return uncertain validation
        return {
            "is_valid": False,
            "confidence_score": 0.0,
            "discrepancies": [],
            "notes": "Failed to validate extraction",
        }

    async def analyze_invoice_deep(
        self,
        invoice: Invoice,
        pdf_bytes: bytes,
        pdf_source_id: Optional[str] = None,
    ) -> InvoiceAnalysis:
        """
        Perform deep fraud detection and quality analysis on an invoice.

        Args:
            invoice: The invoice object with extracted data
            pdf_bytes: PDF file bytes for visual inspection
            pdf_source_id: Optional stable identifier for caching

        Returns:
            InvoiceAnalysis object with comprehensive assessment
        """
        prompt = """Perform a comprehensive fraud detection and quality analysis of this invoice document.

## 1. REQUIRED ELEMENTS CHECK
Verify the invoice contains all legally required elements:
- Unique invoice number
- Invoice date
- Vendor's complete business name and address
- Vendor contact information (phone, email)
- Tax ID or business registration number
- Itemized description of goods/services
- Quantities and unit prices
- Subtotals, taxes, and final total
- Payment terms and due date
- Payment methods accepted

Flag any missing critical information.

## 2. FRAUD RED FLAGS
Examine for common fraud indicators:
- **Suspicious Amounts**: Round numbers (e.g., exactly $1,000.00), unusually high amounts, or amounts just below approval thresholds
- **Contact Info Issues**: P.O. Box only (no physical address), mismatched addresses, inconsistent vendor details
- **Invoice Quality**: Handwritten or poorly formatted invoices, low-quality images, unprofessional appearance
- **Mathematical Errors**: Line items that don't add up correctly, tax calculations that are wrong
- **Urgency Language**: Demands for immediate payment, threats, unusual pressure
- **Duplicate Indicators**: Similar invoice numbers, duplicate line items, or mirror charges

## 3. PROFESSIONAL QUALITY ASSESSMENT
Evaluate legitimacy indicators:
- Professional formatting and branding
- Consistent vendor information
- Clear, detailed line item descriptions
- Proper business documentation (letterhead, logo, etc.)
- OCR quality and document readability

## 4. LINE ITEM ANALYSIS
Review charges for reasonableness:
- Are descriptions clear and specific?
- Do quantities and pricing seem appropriate for the work described?
- Are there any vague or generic line items (e.g., "miscellaneous charges")?
- Do unit prices align with typical market rates for this type of work?

## 5. OVERALL RISK ASSESSMENT
Provide:
- **Risk Level**: low / medium / high
- **Confidence Score**: Your confidence this is a legitimate invoice (0-100%)
- **Key Concerns**: Top 2-3 issues requiring attention
- **Recommendations**: Specific follow-up actions if needed (e.g., "Verify vendor address", "Request detailed breakdown of line item X")

Be specific and reference actual details from the invoice to support your findings. Focus on actionable insights.

Return your analysis as JSON with this structure:
{
  "risk_level": "low" | "medium" | "high",
  "confidence_score": 0-100,
  "missing_elements": ["element1", "element2"],
  "fraud_red_flags": ["flag1", "flag2"],
  "quality_issues": ["issue1", "issue2"],
  "line_item_concerns": ["concern1", "concern2"],
  "key_concerns": ["concern1", "concern2", "concern3"],
  "recommendations": ["rec1", "rec2"],
  "detailed_analysis": "Full narrative analysis with specific details..."
}"""

        # Call LLM with PDF
        response_text = await self._call_llm_async(prompt, pdf_bytes=pdf_bytes, pdf_source_id=pdf_source_id)

        # Parse JSON response
        try:
            # Extract JSON from response (may be wrapped in markdown)
            json_str = response_text
            if "```json" in response_text:
                json_str = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                json_str = response_text.split("```")[1].split("```")[0].strip()

            data = self._parse_json_with_repair(json_str, "invoice analysis")
            if data is None:
                raise ValueError("Failed to parse analysis JSON after repair attempts")

            # Create InvoiceAnalysis object
            from datetime import datetime

            return InvoiceAnalysis(
                invoice_id=invoice.id or "unknown",
                risk_level=data.get("risk_level", "medium"),
                confidence_score=data.get("confidence_score", 50),
                missing_elements=data.get("missing_elements", []),
                fraud_red_flags=data.get("fraud_red_flags", []),
                quality_issues=data.get("quality_issues", []),
                line_item_concerns=data.get("line_item_concerns", []),
                key_concerns=data.get("key_concerns", []),
                recommendations=data.get("recommendations", []),
                detailed_analysis=data.get("detailed_analysis", response_text),
                analyzed_at=datetime.now(),
            )

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.info(f"Error parsing analysis response: {e}")
            logger.info(f"Response was: {response_text[:500]}")

            # Fallback: return analysis based on text response
            from datetime import datetime

            return InvoiceAnalysis(
                invoice_id=invoice.id or "unknown",
                risk_level="medium",
                confidence_score=50,
                missing_elements=[],
                fraud_red_flags=[],
                quality_issues=["Failed to parse structured analysis"],
                line_item_concerns=[],
                key_concerns=["Analysis parsing failed"],
                recommendations=["Retry analysis"],
                detailed_analysis=response_text,
                analyzed_at=datetime.now(),
            )
