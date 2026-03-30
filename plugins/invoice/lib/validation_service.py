import asyncio
import logging
import os
import uuid
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment

from .llm_service import LLMService
from .models import Invoice, LineItem, Verification, VerificationStatus, VerificationType

# Configure logger with timestamps
logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s.%(msecs)03d [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=logging.INFO
)


class ValidationService:
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service
        # Check environment variable for matching algorithm preference
        self.use_bipartite_matching = os.getenv("USE_BIPARTITE_MATCHING", "false").lower() in ["true", "1", "yes"]
        if self.use_bipartite_matching:
            logger.info("[VALIDATION_SERVICE] Using BIPARTITE matching algorithm (optimal global assignment)")
        else:
            logger.info("[VALIDATION_SERVICE] Using GREEDY matching algorithm (faster, local optimization)")

    async def validate_invoice_async(
        self,
        parent_invoice: Invoice,
        supporting_invoices: List[Invoice],
        parent_pdf_bytes: Optional[bytes] = None,
        parent_source_id: Optional[str] = None,
    ) -> List[Verification]:
        """
        Run all validations on an invoice with its supporting documents in parallel.

        Args:
            parent_invoice: The parent invoice to validate
            supporting_invoices: List of supporting invoices/receipts
            parent_pdf_bytes: PDF bytes of parent invoice for visual verification
            parent_source_id: Stable source ID for caching

        Returns:
            List of Verification objects
        """
        # Run most validations in parallel, but some must run sequentially
        # First batch: validations that can run in parallel
        # Select matching algorithm based on configuration
        matching_method = (
            self._verify_receipts_exist_bipartite if self.use_bipartite_matching else self._verify_receipts_exist
        )

        parallel_tasks = [
            # Parent invoice validations (can run in parallel)
            self._verify_work_requested_async(parent_invoice, parent_pdf_bytes, parent_source_id),
            asyncio.to_thread(self._verify_totals_match, parent_invoice),
            self._verify_correct_recipient_async(parent_invoice, parent_pdf_bytes, parent_source_id),
            asyncio.to_thread(self._verify_has_supporting_details, parent_invoice, supporting_invoices),
            self._verify_reasonable_cost_async(parent_invoice, parent_pdf_bytes, parent_source_id),
            self._verify_work_completed_async(parent_invoice, parent_pdf_bytes, parent_source_id),
            asyncio.to_thread(matching_method, parent_invoice, supporting_invoices),
            self._verify_receipt_work_completed_async(supporting_invoices),
        ]

        # Run parallel tasks
        parallel_results = await asyncio.gather(*parallel_tasks, return_exceptions=True)

        # Second batch: tasks that depend on the results of the first batch
        # _identify_unmatched_supporting_invoices must run AFTER _verify_receipts_exist
        sequential_tasks = [
            asyncio.to_thread(self._identify_unmatched_supporting_invoices, parent_invoice, supporting_invoices),
        ]

        sequential_results = await asyncio.gather(*sequential_tasks, return_exceptions=True)

        # Combine all results
        results = parallel_results + sequential_results

        # Flatten results and handle errors
        verifications = []
        for result in results:
            if isinstance(result, Exception):
                logger.info(f"[VALIDATION] Error in verification: {result}")
                continue
            if isinstance(result, list):
                verifications.extend(result)
            else:
                verifications.append(result)

        return verifications

    def validate_invoice(
        self,
        parent_invoice: Invoice,
        supporting_invoices: List[Invoice],
        parent_pdf_bytes: Optional[bytes] = None,
        parent_source_id: Optional[str] = None,
    ) -> List[Verification]:
        """
        Synchronous wrapper for validate_invoice_async.

        Args:
            parent_invoice: The parent invoice to validate
            supporting_invoices: List of supporting invoices/receipts
            parent_pdf_bytes: PDF bytes of parent invoice for visual verification
            parent_source_id: Stable source ID for caching

        Returns:
            List of Verification objects
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(
            self.validate_invoice_async(parent_invoice, supporting_invoices, parent_pdf_bytes, parent_source_id)
        )

    async def _verify_work_requested_async(
        self, invoice: Invoice, pdf_bytes: Optional[bytes], source_id: Optional[str]
    ) -> Verification:
        """Verify that work was requested (async version)."""
        prompt = f"""Analyze this parent invoice to determine if work was properly requested/authorized.

Invoice Details:
- Vendor: {invoice.vendor}
- Invoice Number: {invoice.invoice_number}
- Recipient: {invoice.recipient}
- Total: ${invoice.total_amount}{f" (Amount Due: ${invoice.amount_due})" if invoice.amount_due and invoice.amount_due != invoice.total_amount and invoice.amount_due != 0.00 else ""}

Look for:
- Purchase Order (PO) number
- Work Order number
- Contract reference
- Authorization number
- Job reference

Return JSON:
{{
  "status": "pass|fail|needs_review",
  "confidence": 0.0-1.0,
  "evidence": "What you found or didn't find",
  "notes": "Brief explanation"
}}"""

        response = await self.llm._call_llm_async(prompt, pdf_bytes=pdf_bytes, pdf_source_id=source_id)
        result = self._parse_verification_response(response)

        return Verification(
            id=str(uuid.uuid4()),
            invoice_id=invoice.id,
            type=VerificationType.WORK_REQUESTED,
            status=VerificationStatus(result["status"]),
            confidence_score=result.get("confidence"),
            evidence=result.get("evidence"),
            notes=result.get("notes"),
        )

    def _verify_work_requested(
        self, invoice: Invoice, pdf_bytes: Optional[bytes], source_id: Optional[str]
    ) -> Verification:
        """Verify that work was requested (sync wrapper)."""
        return asyncio.run(self._verify_work_requested_async(invoice, pdf_bytes, source_id))

    def _verify_totals_match(self, invoice: Invoice) -> Verification:
        """Verify that line items add up to the total amount."""
        line_items_total = sum(item.total for item in invoice.line_items if item.total is not None)
        invoice_total = invoice.total_amount or 0

        # Allow for small rounding differences (within $1)
        difference = abs(line_items_total - invoice_total)

        if difference < 1.0:
            status = VerificationStatus.PASS
            evidence = f"Line items total: ${line_items_total:.2f}, Invoice total: ${invoice_total:.2f}, Difference: ${difference:.2f}"
            confidence = 1.0
        elif difference < 10.0:
            status = VerificationStatus.NEEDS_REVIEW
            evidence = f"Line items total: ${line_items_total:.2f}, Invoice total: ${invoice_total:.2f}, Difference: ${difference:.2f}"
            confidence = 0.5
        else:
            status = VerificationStatus.FAIL
            evidence = f"Line items total: ${line_items_total:.2f}, Invoice total: ${invoice_total:.2f}, Difference: ${difference:.2f}"
            confidence = 0.9

        return Verification(
            id=str(uuid.uuid4()),
            invoice_id=invoice.id,
            type=VerificationType.TOTALS_MATCH,
            status=status,
            confidence_score=confidence,
            evidence=evidence,
            notes=f"Calculated difference: ${difference:.2f}",
        )

    async def _verify_correct_recipient_async(
        self, invoice: Invoice, pdf_bytes: Optional[bytes], source_id: Optional[str]
    ) -> Verification:
        """Verify bill is to the correct recipient (async)."""
        prompt = f"""Analyze this invoice to verify it's billed to the correct recipient.

Invoice Details:
- Vendor: {invoice.vendor}
- Recipient/Bill To: {invoice.recipient}
- Invoice Number: {invoice.invoice_number}

Check:
- Is the recipient name correct and complete?
- Is the billing address correct?
- Are there any red flags (wrong company, PO Box when physical address expected, etc.)?

Return JSON:
{{
  "status": "pass|fail|needs_review",
  "confidence": 0.0-1.0,
  "evidence": "What you found",
  "notes": "Brief explanation"
}}"""

        response = await self.llm._call_llm_async(prompt, pdf_bytes=pdf_bytes, pdf_source_id=source_id)
        result = self._parse_verification_response(response)

        return Verification(
            id=str(uuid.uuid4()),
            invoice_id=invoice.id,
            type=VerificationType.CORRECT_RECIPIENT,
            status=VerificationStatus(result["status"]),
            confidence_score=result.get("confidence"),
            evidence=result.get("evidence"),
            notes=result.get("notes"),
        )

    def _verify_correct_recipient(
        self, invoice: Invoice, pdf_bytes: Optional[bytes], source_id: Optional[str]
    ) -> Verification:
        """Verify bill is to the correct recipient (sync wrapper)."""
        return asyncio.run(self._verify_correct_recipient_async(invoice, pdf_bytes, source_id))

    def _verify_has_supporting_details(
        self, parent_invoice: Invoice, supporting_invoices: List[Invoice]
    ) -> Verification:
        """Verify that line items have supporting details that match totals."""
        total_supporting = sum(inv.total_amount or 0 for inv in supporting_invoices)
        parent_total = parent_invoice.total_amount or 0

        # Check if supporting documents roughly match parent total
        difference = abs(total_supporting - parent_total)
        coverage_ratio = total_supporting / parent_total if parent_total > 0 else 0

        if coverage_ratio >= 0.95 and difference < 10:
            status = VerificationStatus.PASS
            confidence = 0.9
        elif coverage_ratio >= 0.8:
            status = VerificationStatus.NEEDS_REVIEW
            confidence = 0.6
        else:
            status = VerificationStatus.FAIL
            confidence = 0.8

        evidence = f"Supporting invoices total: ${total_supporting:.2f}, Parent total: ${parent_total:.2f}, Coverage: {coverage_ratio*100:.1f}%"

        return Verification(
            id=str(uuid.uuid4()),
            invoice_id=parent_invoice.id,
            type=VerificationType.HAS_SUPPORTING_DETAILS,
            status=status,
            confidence_score=confidence,
            evidence=evidence,
            notes=f"Found {len(supporting_invoices)} supporting document(s)",
        )

    async def _verify_reasonable_cost_async(
        self, invoice: Invoice, pdf_bytes: Optional[bytes], source_id: Optional[str]
    ) -> Verification:
        """Verify that line item costs are reasonable (async)."""
        line_items_summary = "\n".join(
            [
                (
                    f"- {item.description}: ${item.total:.2f} ({item.quantity} @ ${item.unit_price:.2f})"
                    if item.unit_price
                    else f"- {item.description}: ${item.total:.2f}"
                )
                for item in invoice.line_items
            ]
        )

        prompt = f"""Analyze these line items for reasonableness. Flag anything wildly expensive or absurd.

Invoice from: {invoice.vendor}
Total: ${invoice.total_amount}{f" (Amount Due: ${invoice.amount_due})" if invoice.amount_due and invoice.amount_due != invoice.total_amount and invoice.amount_due != 0.00 else ""}

Line Items:
{line_items_summary}

Check for:
- Suspiciously high unit prices
- Unreasonable quantities
- Items that don't match the vendor's typical services
- Duplicate charges
- Absurd descriptions or amounts

Return JSON:
{{
  "status": "pass|fail|needs_review",
  "confidence": 0.0-1.0,
  "evidence": "What you found or specific concerns",
  "notes": "Brief explanation"
}}"""

        response = await self.llm._call_llm_async(prompt, pdf_bytes=pdf_bytes, pdf_source_id=source_id)
        result = self._parse_verification_response(response)

        return Verification(
            id=str(uuid.uuid4()),
            invoice_id=invoice.id,
            type=VerificationType.REASONABLE_COST,
            status=VerificationStatus(result["status"]),
            confidence_score=result.get("confidence"),
            evidence=result.get("evidence"),
            notes=result.get("notes"),
        )

    def _verify_reasonable_cost(
        self, invoice: Invoice, pdf_bytes: Optional[bytes], source_id: Optional[str]
    ) -> Verification:
        """Verify that line item costs are reasonable (sync wrapper)."""
        return asyncio.run(self._verify_reasonable_cost_async(invoice, pdf_bytes, source_id))

    async def _verify_work_completed_async(
        self, invoice: Invoice, pdf_bytes: Optional[bytes], source_id: Optional[str]
    ) -> Verification:
        """Verify that work was completed (async)."""
        prompt = f"""Analyze this parent invoice to verify work was completed.

Invoice Details:
- Vendor: {invoice.vendor}
- Invoice Number: {invoice.invoice_number}
- Date: {invoice.date}
- Total: ${invoice.total_amount}{f" (Amount Due: ${invoice.amount_due})" if invoice.amount_due and invoice.amount_due != invoice.total_amount and invoice.amount_due != 0.00 else ""}

Look for:
- Completion date
- Signature or approval
- "Work completed" notation
- Inspection/acceptance stamp
- Date of service/completion

Return JSON:
{{
  "status": "pass|fail|needs_review",
  "confidence": 0.0-1.0,
  "evidence": "What you found",
  "notes": "Brief explanation"
}}"""

        response = await self.llm._call_llm_async(prompt, pdf_bytes=pdf_bytes, pdf_source_id=source_id)
        result = self._parse_verification_response(response)

        return Verification(
            id=str(uuid.uuid4()),
            invoice_id=invoice.id,
            type=VerificationType.WORK_COMPLETED,
            status=VerificationStatus(result["status"]),
            confidence_score=result.get("confidence"),
            evidence=result.get("evidence"),
            notes=result.get("notes"),
        )

    def _verify_work_completed(
        self, invoice: Invoice, pdf_bytes: Optional[bytes], source_id: Optional[str]
    ) -> Verification:
        """Verify that work was completed (sync wrapper)."""
        return asyncio.run(self._verify_work_completed_async(invoice, pdf_bytes, source_id))

    def _verify_receipts_exist(self, parent_invoice: Invoice, supporting_invoices: List[Invoice]) -> List[Verification]:
        """
        Verify that receipts exist for each line item with EXACT amount AND semantic matching.

        Algorithm:
        For each parent line item:
          1. Try to find supporting invoice where total matches parent line item amount
          2. If not found, try to find supporting invoice line item that matches
          3. Validate semantic similarity with AI
          4. If semantic match passes, record the match
        """
        verifications = []

        logger.info(f"\n[MATCHING] Starting matching algorithm")
        logger.info(f"[MATCHING] Parent invoice has {len(parent_invoice.line_items)} line items")
        logger.info(f"[MATCHING] Checking against {len(supporting_invoices)} supporting invoices")

        # Collect all potential matches for batch AI validation
        potential_matches = []
        parent_to_match = {}  # parent_line.id -> match info
        used_invoice_total_matches = set()  # Track invoices matched at invoice-total level

        # PASS 1A: Try to match invoice totals EXACTLY first
        logger.info("\n[MATCHING] PASS 1A: Matching invoice totals (EXACT matches only)...")
        for parent_line in parent_invoice.line_items:
            # Skip parent line items without totals (can't match by amount)
            if parent_line.total is None:
                continue

            desc_for_log = (parent_line.description or "(no description)")[:60]
            logger.info(f"[MATCHING] P1A: {desc_for_log} ${parent_line.total:.2f}")

            # Only look for TOTAL matches in Pass 1A - EXACT matches only
            for supp_inv in supporting_invoices:
                # Skip invoices already matched at the total level
                if supp_inv.id in used_invoice_total_matches:
                    continue

                # Prefer amount_due over total_amount when matching (e.g., for deposits)
                amount_to_match = (
                    supp_inv.amount_due
                    if supp_inv.amount_due is not None and supp_inv.amount_due != 0.00
                    else supp_inv.total_amount
                )
                if amount_to_match and parent_line.total:
                    # Don't use abs() - we need to check for same sign and value
                    diff = amount_to_match - parent_line.total
                    if abs(diff) <= 0.01:  # Exact match (within 1 cent)
                        # Found a total match!
                        logger.info(
                            f"[MATCHING]   -> Found total match: {supp_inv.vendor} ${amount_to_match:.2f} (ID: {supp_inv.id})"
                        )
                        used_invoice_total_matches.add(supp_inv.id)

                        # Record this match
                        match_id = f"match_{len(potential_matches)}"
                        match_info = {
                            "match_id": match_id,
                            "parent_line_item": parent_line,
                            "supporting_invoice": supp_inv,
                            "supporting_line_item": None,
                            "match_type": "invoice_total",
                            "parent_desc": parent_line.description,
                            "supporting_desc": supp_inv.vendor or "Unknown",
                            "parent_amount": parent_line.total,
                            "supporting_amount": supp_inv.total_amount,
                        }
                        potential_matches.append(match_info)
                        parent_to_match[parent_line.id] = match_info
                        break

        # PASS 1B: Try fuzzy matching on invoice totals (within 2% tolerance) for remaining unmatched
        logger.info("\n[MATCHING] PASS 1B: Fuzzy matching invoice totals (within 2% tolerance)...")
        for parent_line in parent_invoice.line_items:
            # Skip if already matched
            if parent_line.id in parent_to_match:
                continue

            # Skip parent line items without totals
            if parent_line.total is None:
                continue

            desc_for_log = (parent_line.description or "(no description)")[:60]
            logger.info(f"[MATCHING] P1B: {desc_for_log} ${parent_line.total:.2f}")

            # Look for fuzzy matches within 2% tolerance
            best_match = None
            best_diff_pct = float("inf")

            for supp_inv in supporting_invoices:
                # Skip invoices already matched at the total level
                if supp_inv.id in used_invoice_total_matches:
                    continue

                # Prefer amount_due over total_amount when matching (e.g., for deposits)
                amount_to_match = (
                    supp_inv.amount_due
                    if supp_inv.amount_due is not None and supp_inv.amount_due != 0.00
                    else supp_inv.total_amount
                )
                if amount_to_match and parent_line.total:
                    # Don't match negative amounts (returns) with positive amounts
                    # Only consider matches with same sign (both positive or both negative)
                    if (amount_to_match * parent_line.total) > 0:  # Same sign check
                        diff = abs(amount_to_match - parent_line.total)
                        diff_pct = (diff / abs(parent_line.total)) * 100

                        # Check if within 2% tolerance
                        if diff_pct <= 2.0 and diff_pct < best_diff_pct:
                            best_match = supp_inv
                            best_diff_pct = diff_pct

            if best_match:
                # Found a fuzzy match!
                amount_to_match = (
                    best_match.amount_due
                    if best_match.amount_due is not None and best_match.amount_due != 0.00
                    else best_match.total_amount
                )
                logger.info(
                    f"[MATCHING]   -> Found fuzzy match ({best_diff_pct:.2f}% diff): {best_match.vendor} ${amount_to_match:.2f} (ID: {best_match.id})"
                )
                used_invoice_total_matches.add(best_match.id)

                # Record this match
                match_id = f"match_{len(potential_matches)}"
                match_info = {
                    "match_id": match_id,
                    "parent_line_item": parent_line,
                    "supporting_invoice": best_match,
                    "supporting_line_item": None,
                    "match_type": "invoice_total_fuzzy",
                    "parent_desc": parent_line.description,
                    "supporting_desc": best_match.vendor or "Unknown",
                    "parent_amount": parent_line.total,
                    "supporting_amount": best_match.total_amount,
                }
                potential_matches.append(match_info)
                parent_to_match[parent_line.id] = match_info

        # PASS 2A: For unmatched parent line items, try EXACT match to line items within supporting invoices
        logger.info("\n[MATCHING] PASS 2A: Matching remaining items to invoice line items (EXACT)...")
        for parent_line in parent_invoice.line_items:
            # Skip if already matched in Pass 1
            if parent_line.id in parent_to_match:
                continue

            # Skip parent line items without totals
            if parent_line.total is None:
                continue

            desc_for_log = (parent_line.description or "(no description)")[:60]
            logger.info(f"[MATCHING] P2A: {desc_for_log} ${parent_line.total:.2f}")

            supporting_invoice = None
            supporting_line_item = None

            # Look for line item matches within supporting invoices
            for supp_inv in supporting_invoices:
                # Skip invoices that were already used for total matching
                if supp_inv.id in used_invoice_total_matches:
                    continue

                if supp_inv.line_items:
                    for supp_line in supp_inv.line_items:
                        # Skip supporting line items without totals
                        if supp_line.total is None or parent_line.total is None:
                            continue
                        # Don't match negative amounts (returns) with positive amounts
                        if (supp_line.total * parent_line.total) > 0:  # Same sign check
                            diff = abs(supp_line.total - parent_line.total)
                            if diff <= 0.01:
                                supporting_invoice = supp_inv
                                supporting_line_item = supp_line
                                supp_line_desc = (supp_line.description or "(no description)")[:40]
                                logger.info(
                                    f"[MATCHING]   -> Found line item match: {supp_inv.vendor} (Total: ${supp_inv.total_amount:.2f}) - Line item: {supp_line_desc} ${supp_line.total:.2f}"
                                )
                                logger.info(f"[MATCHING]     WARNING: Matching to a line item within a larger invoice!")
                                break
                if supporting_invoice:
                    # Mark this invoice as used (even for partial match)
                    used_invoice_total_matches.add(supporting_invoice.id)

                    # Record the match
                    match_id = f"match_{len(potential_matches)}"
                    match_info = {
                        "match_id": match_id,
                        "parent_line_item": parent_line,
                        "supporting_invoice": supporting_invoice,
                        "supporting_line_item": supporting_line_item,
                        "match_type": "line_item",
                        "parent_desc": parent_line.description,
                        "supporting_desc": (
                            f"{supporting_invoice.vendor} - {supporting_line_item.description}"
                            if supporting_line_item
                            else supporting_invoice.vendor or "Unknown"
                        ),
                        "parent_amount": parent_line.total,
                        "supporting_amount": (
                            supporting_line_item.total if supporting_line_item else supporting_invoice.total_amount
                        ),
                    }
                    potential_matches.append(match_info)
                    parent_to_match[parent_line.id] = match_info
                    break

            if parent_line.id not in parent_to_match:
                logger.info(f"[MATCHING]   No exact match found")

        # PASS 2B: For still unmatched items, try FUZZY match (within 2%) to line items within supporting invoices
        logger.info("\n[MATCHING] PASS 2B: Fuzzy matching remaining items to invoice line items (within 2%)...")
        for parent_line in parent_invoice.line_items:
            # Skip if already matched
            if parent_line.id in parent_to_match:
                continue

            # Skip parent line items without totals
            if parent_line.total is None:
                continue

            desc_for_log = (parent_line.description or "(no description)")[:60]
            logger.info(f"[MATCHING] P2B: {desc_for_log} ${parent_line.total:.2f}")

            best_match_invoice = None
            best_match_line = None
            best_diff_pct = float("inf")

            # Look for fuzzy line item matches within supporting invoices
            for supp_inv in supporting_invoices:
                # Skip invoices that were already used for total matching
                if supp_inv.id in used_invoice_total_matches:
                    continue

                if supp_inv.line_items:
                    for supp_line in supp_inv.line_items:
                        # Skip supporting line items without totals
                        if supp_line.total is None or parent_line.total is None:
                            continue

                        # Don't match negative amounts (returns) with positive amounts
                        if (supp_line.total * parent_line.total) <= 0:  # Different signs - skip
                            continue

                        diff = abs(supp_line.total - parent_line.total)
                        diff_pct = (diff / abs(parent_line.total)) * 100

                        # Check if within 2% tolerance
                        if diff_pct <= 2.0 and diff_pct < best_diff_pct:
                            best_match_invoice = supp_inv
                            best_match_line = supp_line
                            best_diff_pct = diff_pct

            if best_match_invoice:
                # Found a fuzzy match!
                best_match_line_desc = (best_match_line.description or "(no description)")[:40]
                logger.info(
                    f"[MATCHING]   -> Found fuzzy line item match ({best_diff_pct:.2f}% diff): {best_match_invoice.vendor} - {best_match_line_desc} ${best_match_line.total:.2f}"
                )
                # Mark this invoice as used (even for partial match)
                used_invoice_total_matches.add(best_match_invoice.id)

                # Record the match
                match_id = f"match_{len(potential_matches)}"
                match_info = {
                    "match_id": match_id,
                    "parent_line_item": parent_line,
                    "supporting_invoice": best_match_invoice,
                    "supporting_line_item": best_match_line,
                    "match_type": "line_item_fuzzy",
                    "parent_desc": parent_line.description,
                    "supporting_desc": f"{best_match_invoice.vendor} - {best_match_line.description}",
                    "parent_amount": parent_line.total,
                    "supporting_amount": best_match_line.total,
                }
                potential_matches.append(match_info)
                parent_to_match[parent_line.id] = match_info

            if parent_line.id not in parent_to_match:
                logger.info(f"[MATCHING]   No match found (even with 2% tolerance)")

        # Step 3: Validate semantic similarity with AI (for flagging, not rejection)
        logger.info(f"\n[MATCHING] Step 2: Validating {len(potential_matches)} potential matches with AI")
        ai_approved_match_ids = set()
        if potential_matches:
            ai_approved_match_ids = self._validate_semantic_matches(potential_matches)
            logger.info(f"[MATCHING] AI approved {len(ai_approved_match_ids)}/{len(potential_matches)} matches")

            # Show flagged matches (AI rejected but amount matched)
            flagged = [m for m in potential_matches if m["match_id"] not in ai_approved_match_ids]
            if flagged:
                logger.info(f"\n[MATCHING] AI FLAGGED {len(flagged)} matches for manual review:")
                for m in flagged:
                    parent_desc = (m["parent_desc"] or "(no description)")[:50]
                    supporting_desc = (m["supporting_desc"] or "(no description)")[:50]
                    logger.info(f"[MATCHING]   {m['match_id']}: {parent_desc} ${m['parent_amount']:.2f}")
                    logger.info(f"[MATCHING]     -> Matched to: {supporting_desc}")
                    logger.info(f"[MATCHING]     Reason: Amount matches but may not be semantically similar")

        # Step 4: Create verifications for each parent line item
        logger.info(f"\n[MATCHING] Step 3: Final results per parent line item")
        matched_count = 0
        flagged_count = 0
        unmatched_count = 0

        for line_item in parent_invoice.line_items:
            match_info = parent_to_match.get(line_item.id)

            if match_info:
                # We have an amount match - accept it regardless of AI validation
                matched_count += 1
                supp_inv = match_info["supporting_invoice"]

                line_item.matched_supporting_invoice_ids = [supp_inv.id]
                logger.info(
                    f"[MATCHING]   -> Set matched_supporting_invoice_ids for line item {line_item.id}: [{supp_inv.id}]"
                )

                # Check if AI flagged this match
                ai_approved = match_info["match_id"] in ai_approved_match_ids
                line_item.match_needs_review = not ai_approved

                if ai_approved:
                    status = VerificationStatus.PASS
                    confidence = 0.9
                    # Show which amount was matched
                    amount_str = ""
                    if supp_inv.total_amount is not None:
                        if (
                            supp_inv.amount_due
                            and supp_inv.amount_due != 0.00
                            and supp_inv.amount_due != supp_inv.total_amount
                        ):
                            amount_str = f"${supp_inv.amount_due:.2f} (of ${supp_inv.total_amount:.2f} total)"
                        else:
                            amount_str = f"${supp_inv.total_amount:.2f}"
                    evidence = (
                        f"Matched to: {supp_inv.vendor} {amount_str}"
                        if amount_str
                        else f"Matched to: {supp_inv.vendor}"
                    )
                    desc_for_log = (line_item.description or "(no description)")[:50]
                    logger.info(f"[MATCHING]   PASS {desc_for_log:50s} ${line_item.total:>10,.2f}")
                    logger.info(f"[MATCHING]       -> {supp_inv.vendor}")
                else:
                    # Flagged for review but still matched
                    flagged_count += 1
                    status = VerificationStatus.NEEDS_REVIEW
                    confidence = 0.7
                    # Show which amount was matched
                    amount_str = ""
                    if supp_inv.total_amount is not None:
                        if (
                            supp_inv.amount_due
                            and supp_inv.amount_due != 0.00
                            and supp_inv.amount_due != supp_inv.total_amount
                        ):
                            amount_str = f"${supp_inv.amount_due:.2f} (of ${supp_inv.total_amount:.2f} total)"
                        else:
                            amount_str = f"${supp_inv.total_amount:.2f}"
                    evidence = (
                        f"Matched to: {supp_inv.vendor} {amount_str} (flagged for review)"
                        if amount_str
                        else f"Matched to: {supp_inv.vendor} (flagged for review)"
                    )
                    desc_for_log = (line_item.description or "(no description)")[:50]
                    logger.info(f"[MATCHING]   FLAG {desc_for_log:50s} ${line_item.total:>10,.2f}")
                    logger.info(f"[MATCHING]       -> {supp_inv.vendor} (FLAGGED - needs manual review)")
            else:
                unmatched_count += 1
                status = VerificationStatus.NEEDS_REVIEW
                confidence = 0.5
                evidence = f"No receipt found for: {line_item.description or '(no description)'}"

                desc_for_log = (line_item.description or "(no description)")[:50]
                logger.info(f"[MATCHING]   MISS {desc_for_log:50s} ${line_item.total:>10,.2f}")

            verifications.append(
                Verification(
                    id=str(uuid.uuid4()),
                    invoice_id=parent_invoice.id,
                    type=VerificationType.RECEIPT_EXISTS,
                    status=status,
                    confidence_score=confidence,
                    evidence=evidence,
                    notes=f"Line item: {line_item.description} (${line_item.total:.2f})",
                )
            )

        logger.info(
            f"\n[MATCHING] FINAL: {matched_count} matched ({flagged_count} flagged for review), {unmatched_count} unmatched\n"
        )

        return verifications

    def _verify_receipts_exist_bipartite(
        self, parent_invoice: Invoice, supporting_invoices: List[Invoice]
    ) -> List[Verification]:
        """
        Verify that receipts exist for each line item using bipartite matching.

        This is an alternative to the greedy _verify_receipts_exist method that uses
        optimal bipartite matching to find the best global assignment of parent line items
        to supporting invoice candidates (totals and line items).

        Algorithm:
        1. Build candidate pool: For each supporting invoice, create candidates for:
           - The invoice total/amount_due (preferred via 2x weight multiplier)
           - Each line item (standard 1x weight multiplier)
        2. Build cost matrix with edge weights based on:
           - Amount similarity (1.0 exact, 0.95 within 2%, 0.85 within 5%, 0.70 within 10%, 0.50 within 20%)
           - Vendor name similarity (1.0 strong match, 0.6 partial, 0.3 weak, 0.1 no match)
           - Type preference (total 2.0x > line_item 1.0x)
           - Weight = amount_score x vendor_score x type_multiplier
        3. Solve maximum weight bipartite matching using Hungarian algorithm
        4. Create verifications based on optimal assignment
        """
        verifications = []

        logger.info(f"\n[BIPARTITE_MATCHING] Starting bipartite matching algorithm")
        logger.info(f"[BIPARTITE_MATCHING] Parent invoice has {len(parent_invoice.line_items)} line items")
        logger.info(f"[BIPARTITE_MATCHING] Checking against {len(supporting_invoices)} supporting invoices")

        # Step 1: Build candidate pool (Set B)
        candidates = self._build_candidate_pool(supporting_invoices)
        logger.info(f"[BIPARTITE_MATCHING] Created {len(candidates)} candidates from supporting invoices")

        # Filter parent line items that have amounts (can't match without amount)
        matchable_parent_items = [item for item in parent_invoice.line_items if item.total is not None]
        logger.info(f"[BIPARTITE_MATCHING] {len(matchable_parent_items)} parent items have amounts (matchable)")

        if not matchable_parent_items or not candidates:
            logger.info(f"[BIPARTITE_MATCHING] Nothing to match - returning empty results")
            # Create verifications for unmatched items
            for line_item in parent_invoice.line_items:
                verifications.append(
                    Verification(
                        id=str(uuid.uuid4()),
                        invoice_id=parent_invoice.id,
                        type=VerificationType.RECEIPT_EXISTS,
                        status=VerificationStatus.NEEDS_REVIEW,
                        confidence_score=0.5,
                        evidence=f"No receipt found for: {line_item.description}",
                        notes=(
                            f"Line item: {line_item.description} (${line_item.total:.2f})"
                            if line_item.total
                            else f"Line item: {line_item.description} (no amount)"
                        ),
                    )
                )
            return verifications

        # Step 2: Build cost matrix with edge weights
        cost_matrix, potential_matches = self._build_cost_matrix(matchable_parent_items, candidates)
        logger.info(f"[BIPARTITE_MATCHING] Built cost matrix: {cost_matrix.shape}")
        logger.info(f"[BIPARTITE_MATCHING] Collected {len(potential_matches)} potential matches")

        # Step 3: Solve bipartite matching (maximum weight)
        # scipy's linear_sum_assignment minimizes cost, so we negate for maximization
        row_indices, col_indices = linear_sum_assignment(-cost_matrix)
        logger.info(f"[BIPARTITE_MATCHING] Found optimal assignment")

        # Step 4: Enforce mutual exclusion constraint
        # A supporting invoice can match EITHER its total OR its line items, but NOT both
        WEIGHT_THRESHOLD = 0.3  # Minimum weight to consider a valid match

        # First pass: collect all matches above threshold
        raw_matches = []
        for i, j in zip(row_indices, col_indices):
            weight = cost_matrix[i, j]
            if weight >= WEIGHT_THRESHOLD:
                parent_item = matchable_parent_items[i]
                candidate = candidates[j]
                raw_matches.append(
                    {
                        "parent_item": parent_item,
                        "candidate": candidate,
                        "weight": weight,
                        "invoice_id": candidate["invoice_id"],
                        "candidate_type": candidate["type"],  # "total" or "line_item"
                    }
                )

        logger.info(f"[BIPARTITE_MATCHING] Found {len(raw_matches)} potential matches above threshold")

        # Second pass: enforce mutual exclusion per supporting invoice
        # Group matches by invoice_id
        invoice_matches = {}
        for match in raw_matches:
            inv_id = match["invoice_id"]
            if inv_id not in invoice_matches:
                invoice_matches[inv_id] = []
            invoice_matches[inv_id].append(match)

        # For each invoice, decide: use total match OR use line item matches
        final_matches = []
        for inv_id, matches in invoice_matches.items():
            # Separate total vs line item matches
            total_matches = [m for m in matches if m["candidate_type"] == "total"]
            line_matches = [m for m in matches if m["candidate_type"] == "line_item"]

            if total_matches and line_matches:
                # Conflict! Choose based on total weight
                total_weight_sum = sum(m["weight"] for m in total_matches)
                line_weight_sum = sum(m["weight"] for m in line_matches)

                if total_weight_sum >= line_weight_sum:
                    final_matches.extend(total_matches)
                    logger.info(
                        f"[BIPARTITE_MATCHING] Invoice {inv_id[:8]}...: Chose TOTAL match (weight={total_weight_sum:.2f}) over {len(line_matches)} line items (weight={line_weight_sum:.2f})"
                    )
                else:
                    final_matches.extend(line_matches)
                    logger.info(
                        f"[BIPARTITE_MATCHING] Invoice {inv_id[:8]}...: Chose {len(line_matches)} LINE ITEM matches (weight={line_weight_sum:.2f}) over total (weight={total_weight_sum:.2f})"
                    )
            else:
                # No conflict, use whatever matched
                final_matches.extend(matches)

        logger.info(f"[BIPARTITE_MATCHING] After mutual exclusion: {len(final_matches)} valid matches")

        # Step 5: Create mapping from parent item to match
        matched_parent_indices = set()
        matched_count = 0
        flagged_count = 0

        parent_to_match = {}
        for match in final_matches:
            parent_to_match[match["parent_item"].id] = {
                "candidate": match["candidate"],
                "weight": match["weight"],
            }

        # Step 6: Create verifications for all parent line items
        logger.info(f"\n[BIPARTITE_MATCHING] Creating verifications:")

        for line_item in parent_invoice.line_items:
            match_info = parent_to_match.get(line_item.id)

            if match_info:
                matched_count += 1
                candidate = match_info["candidate"]
                weight = match_info["weight"]

                # Record the match
                line_item.matched_supporting_invoice_ids = [candidate["invoice_id"]]

                # Flag for review if weight is low (between threshold and 0.6)
                needs_review = weight < 0.6
                line_item.match_needs_review = needs_review

                # Safe description handling for logs (handle None descriptions)
                desc_for_log = (line_item.description or "(no description)")[:50]

                if needs_review:
                    flagged_count += 1
                    status = VerificationStatus.NEEDS_REVIEW
                    confidence = 0.7
                    evidence = f"Matched to: {candidate['description']} (${candidate['amount']:.2f}) - {candidate['type']} (low confidence, weight={weight:.2f})"
                    logger.info(
                        f"[BIPARTITE_MATCHING]   FLAG {desc_for_log:50s} ${line_item.total:>10,.2f} (weight={weight:.2f})"
                    )
                else:
                    status = VerificationStatus.PASS
                    confidence = 0.9
                    evidence = (
                        f"Matched to: {candidate['description']} (${candidate['amount']:.2f}) - {candidate['type']}"
                    )
                    logger.info(
                        f"[BIPARTITE_MATCHING]   PASS {desc_for_log:50s} ${line_item.total:>10,.2f} (weight={weight:.2f})"
                    )
            else:
                desc_for_log = (line_item.description or "(no description)")[:50]
                status = VerificationStatus.NEEDS_REVIEW
                confidence = 0.5
                evidence = f"No receipt found for: {line_item.description or '(no description)'}"
                logger.info(
                    f"[BIPARTITE_MATCHING]   MISS {desc_for_log:50s} ${line_item.total if line_item.total else 'N/A':>10}"
                )

            verifications.append(
                Verification(
                    id=str(uuid.uuid4()),
                    invoice_id=parent_invoice.id,
                    type=VerificationType.RECEIPT_EXISTS,
                    status=status,
                    confidence_score=confidence,
                    evidence=evidence,
                    notes=(
                        f"Line item: {line_item.description} (${line_item.total:.2f})"
                        if line_item.total
                        else f"Line item: {line_item.description}"
                    ),
                )
            )

        unmatched_count = len(parent_invoice.line_items) - matched_count
        logger.info(
            f"\n[BIPARTITE_MATCHING] FINAL: {matched_count} matched ({flagged_count} flagged), {unmatched_count} unmatched\n"
        )

        return verifications

    def _build_candidate_pool(self, supporting_invoices: List[Invoice]) -> List[Dict]:
        """
        Build Set B: All possible match candidates from supporting invoices.

        For each supporting invoice, create:
        1. One candidate for the total/amount_due (PREFERRED via higher weight)
        2. One candidate for each line item

        Returns:
            List of candidate dictionaries
        """
        candidates = []

        for supp_inv in supporting_invoices:
            # Candidate #1: Invoice total (or amount_due if present)
            amount_to_match = (
                supp_inv.amount_due
                if supp_inv.amount_due is not None and supp_inv.amount_due != 0.00
                else supp_inv.total_amount
            )

            if amount_to_match is not None:
                candidates.append(
                    {
                        "candidate_id": f"{supp_inv.id}_TOTAL",
                        "type": "total",  # This gets type_multiplier = 2.0
                        "invoice_id": supp_inv.id,
                        "invoice": supp_inv,
                        "line_item": None,
                        "amount": amount_to_match,
                        "description": supp_inv.vendor or "Unknown",
                    }
                )

            # Candidates #2+: Each line item
            for idx, line in enumerate(supp_inv.line_items):
                if line.total is not None:  # Only create candidate if has amount
                    candidates.append(
                        {
                            "candidate_id": f"{supp_inv.id}_LINE_{idx}",
                            "type": "line_item",  # This gets type_multiplier = 1.0
                            "invoice_id": supp_inv.id,
                            "invoice": supp_inv,
                            "line_item": line,
                            "amount": line.total,
                            "description": f"{supp_inv.vendor or 'Unknown'} - {line.description}",
                        }
                    )

        return candidates

    def _build_cost_matrix(self, parent_items: List[LineItem], candidates: List[Dict]) -> Tuple[np.ndarray, List[Dict]]:
        """
        Build cost matrix for bipartite matching.

        Cost matrix[i,j] = edge weight between parent_items[i] and candidates[j]

        Edge weight = vendor_score^2 x amount_score x type_multiplier

        Vendor score is squared to make vendor matching the PRIMARY signal.
        This prevents cross-vendor matches even with exact amount matches.

        Returns:
            Tuple of (cost_matrix, potential_matches_for_ai_validation)
        """
        n_parent = len(parent_items)
        n_candidates = len(candidates)

        cost_matrix = np.zeros((n_parent, n_candidates))
        potential_matches = []

        for i, parent_item in enumerate(parent_items):
            for j, candidate in enumerate(candidates):
                # Calculate amount similarity score
                amount_score = self._calculate_amount_score(parent_item.total, candidate["amount"])

                # Calculate vendor name similarity score
                vendor_score = self._calculate_vendor_score(parent_item.description, candidate.get("vendor", ""))

                # Type preference multiplier
                type_multiplier = 2.0 if candidate["type"] == "total" else 1.0

                # Calculate final weight: vendor^2 x amount x type
                # Squaring vendor_score makes it dominant (0.05^2 = 0.0025 for poor matches)
                weight = (vendor_score**2) * amount_score * type_multiplier

                # Store in cost matrix
                cost_matrix[i, j] = weight

                # Track all matches for debugging (no longer filtering by amount_score > 0)
                if weight > 0:
                    match_key = f"{i}_{j}"
                    potential_matches.append(
                        {
                            "match_key": match_key,
                            "matrix_indices": (i, j),
                            "parent_item": parent_item,
                            "candidate": candidate,
                            "parent_desc": parent_item.description,
                            "supporting_desc": candidate["description"],
                            "parent_amount": parent_item.total,
                            "supporting_amount": candidate["amount"],
                            "match_type": candidate["type"],
                            "amount_score": amount_score,
                            "vendor_score": vendor_score,
                            "weight": weight,
                        }
                    )

        return cost_matrix, potential_matches

    def _calculate_amount_score(self, parent_amount: float, candidate_amount: float) -> float:
        """
        Calculate amount similarity score.

        Returns:
            0.0 - 1.0, where 1.0 is exact match, 0.0 is incompatible
        """
        if parent_amount is None or candidate_amount is None:
            return 0.0

        # Check for same sign (both positive or both negative)
        if (parent_amount * candidate_amount) <= 0:
            return 0.0  # Different signs - incompatible

        diff = abs(parent_amount - candidate_amount)
        diff_pct = (diff / abs(parent_amount)) * 100

        # More lenient amount scoring to allow vendor name to be primary signal
        if diff <= 0.01:
            return 1.0  # Exact match
        elif diff_pct <= 2.0:
            return 0.95  # Within 2%
        elif diff_pct <= 5.0:
            return 0.85  # Within 5%
        elif diff_pct <= 10.0:
            return 0.70  # Within 10%
        elif diff_pct <= 20.0:
            return 0.50  # Within 20%
        else:
            return 0.0  # Beyond 20% - incompatible

    def _calculate_vendor_score(self, parent_desc: str, candidate_vendor: str) -> float:
        """
        Calculate vendor name similarity score using simple string matching.

        Args:
            parent_desc: Parent line item description (may contain vendor name)
            candidate_vendor: Vendor name from supporting invoice

        Returns:
            0.1 - 1.0, where 1.0 is strong match, 0.1 is no match
        """
        if not parent_desc or not candidate_vendor:
            return 0.5  # Neutral score if either is missing

        # Normalize both strings: lowercase, remove extra spaces
        parent_normalized = " ".join(parent_desc.lower().strip().split())
        vendor_normalized = " ".join(candidate_vendor.lower().strip().split())

        # Check for exact substring match
        if vendor_normalized in parent_normalized or parent_normalized in vendor_normalized:
            return 1.0

        # Calculate character-level similarity (Jaccard similarity on character bigrams)
        def get_bigrams(s: str) -> set:
            return set(s[i : i + 2] for i in range(len(s) - 1))

        parent_bigrams = get_bigrams(parent_normalized)
        vendor_bigrams = get_bigrams(vendor_normalized)

        if not parent_bigrams or not vendor_bigrams:
            return 0.5

        intersection = len(parent_bigrams & vendor_bigrams)
        union = len(parent_bigrams | vendor_bigrams)
        similarity = intersection / union if union > 0 else 0.0

        # Map similarity to score ranges with harsher penalties for non-matches
        if similarity >= 0.85:
            return 1.0  # Strong match
        elif similarity >= 0.70:
            return 0.8  # Good match
        elif similarity >= 0.50:
            return 0.5  # Partial match
        elif similarity >= 0.30:
            return 0.2  # Weak match
        else:
            return 0.05  # Very poor match (harsh penalty)

    def _validate_semantic_matches_batch(self, potential_matches: List[Dict]) -> set:
        """
        Validate semantic similarity for all potential matches using a single AI call.

        Returns:
            Set of match_keys that are semantically valid
        """
        import json

        # Prepare matches for AI validation
        matches_for_ai = []
        for match in potential_matches:
            matches_for_ai.append(
                {
                    "match_key": match["match_key"],
                    "parent_desc": match["parent_desc"],
                    "parent_amount": match["parent_amount"],
                    "supporting_desc": match["supporting_desc"],
                    "supporting_amount": match["supporting_amount"],
                    "match_type": match["match_type"],
                    "amount_score": match["amount_score"],
                }
            )

        prompt = f"""Analyze these potential invoice line item matches and determine which are semantically similar.
A match is valid if the parent description and supporting description refer to the same type of work, product, or service.

Potential Matches:
{json.dumps(matches_for_ai, indent=2)}

Rules:
- Descriptions must be semantically similar (same work/product/service)
- Amount match is already verified (within tolerance)
- Be lenient with abbreviations and vendor-specific naming
- "Jobsite Labor" matches "Jobsite Labor" or "Labor - Jobsite"
- "Rough Materials" from "Pacific Lumber Supply" matches a line item about materials
- Vendor name alone (e.g., "Pacific Lumber Supply") can match if it's a materials/product vendor and parent line is materials/products
- Match type "total" means matching to the full invoice total
- Match type "line_item" means matching to a specific line item within an invoice

Return ONLY a JSON array of valid match keys:
{{"valid_matches": ["0_5", "1_3", ...]}}"""

        try:
            response = self.llm._call_llm(prompt)

            # Parse response
            start_idx = response.find("{")
            end_idx = response.rfind("}") + 1
            if start_idx != -1 and end_idx > start_idx:
                json_str = response[start_idx:end_idx]
                result = json.loads(json_str)
                valid_keys = set(result.get("valid_matches", []))
                logger.info(
                    f"[BIPARTITE_MATCHING] AI validated {len(valid_keys)}/{len(potential_matches)} potential matches"
                )
                return valid_keys
            else:
                logger.info(f"[BIPARTITE_MATCHING] Failed to parse AI response for semantic validation")
                return set()
        except Exception as e:
            logger.info(f"[BIPARTITE_MATCHING] Error in semantic validation: {e}")
            return set()

    def _validate_semantic_matches(self, potential_matches: List[Dict]) -> set:
        """
        Validate semantic similarity for potential matches using a single AI call.
        Returns a set of valid match_ids.
        """
        import json

        # Prepare matches for AI validation
        matches_for_ai = []
        for match in potential_matches:
            matches_for_ai.append(
                {
                    "match_id": match["match_id"],
                    "parent_desc": match["parent_desc"],
                    "parent_amount": match["parent_amount"],
                    "supporting_desc": match["supporting_desc"],
                    "supporting_amount": match["supporting_amount"],
                    "match_type": match["match_type"],
                }
            )

        prompt = f"""Analyze these potential invoice line item matches and determine which are semantically similar.
A match is valid if the parent description and supporting description refer to the same type of work, product, or service.

Potential Matches:
{json.dumps(matches_for_ai, indent=2)}

Rules:
- Descriptions must be semantically similar (same work/product/service)
- Amount match is already verified (~$1 tolerance)
- Be lenient with abbreviations and vendor-specific naming
- "Jobsite Labor" matches "Jobsite Labor" or "Labor - Jobsite"
- "Rough Materials" from "Pacific Lumber Supply" matches a line item about materials
- Vendor name alone (e.g., "Pacific Lumber Supply") can match if it's a materials/product vendor and parent line is materials/products

Return ONLY a JSON array of valid match IDs:
{{"valid_matches": ["match_0", "match_3", ...]}}"""

        try:
            response = self.llm._call_llm(prompt)

            # Parse response
            start_idx = response.find("{")
            end_idx = response.rfind("}") + 1
            if start_idx != -1 and end_idx > start_idx:
                json_str = response[start_idx:end_idx]
                result = json.loads(json_str)
                valid_ids = set(result.get("valid_matches", []))
                logger.info(f"[VALIDATION] AI validated {len(valid_ids)}/{len(potential_matches)} potential matches")
                return valid_ids
            else:
                logger.info(f"[VALIDATION] Failed to parse AI response for semantic validation")
                return set()
        except Exception as e:
            logger.info(f"[VALIDATION] Error in semantic validation: {e}")
            return set()

    async def _verify_receipt_work_completed_async(self, supporting_invoices: List[Invoice]) -> List[Verification]:
        """Verify work was completed for all receipts in parallel."""
        from pathlib import Path

        from .pdf_extractor import PDFExtractor

        async def verify_one_receipt(receipt):
            # Extract PDF bytes and create source ID for caching
            pdf_bytes = None
            source_id = None
            if receipt.document_path and receipt.pages:
                try:
                    with PDFExtractor(receipt.document_path) as extractor:
                        pdf_bytes = extractor.extract_pages_as_pdf(receipt.pages)
                    upload_id = Path(receipt.document_path).stem
                    source_id = f"{upload_id}:pages_{'-'.join(map(str, sorted(receipt.pages)))}"
                except Exception as e:
                    logger.info(f"Warning: Could not extract receipt PDF bytes: {e}")

            prompt = f"""Analyze this supporting receipt/invoice to verify work/service was completed.

Receipt Details:
- Vendor: {receipt.vendor}
- Invoice Number: {receipt.invoice_number}
- Date: {receipt.date}
- Total: ${receipt.total_amount}{f" (Amount Due: ${receipt.amount_due})" if receipt.amount_due and receipt.amount_due != receipt.total_amount and receipt.amount_due != 0.00 else ""}

Look for:
- Service completion date
- Delivered/completed status
- Payment received stamp
- Any indication work was done

Return JSON:
{{
  "status": "pass|fail|needs_review",
  "confidence": 0.0-1.0,
  "evidence": "What you found",
  "notes": "Brief explanation"
}}"""

            response = await self.llm._call_llm_async(prompt, pdf_bytes=pdf_bytes, pdf_source_id=source_id)
            result = self._parse_verification_response(response)

            return Verification(
                id=str(uuid.uuid4()),
                invoice_id=receipt.id,
                type=VerificationType.WORK_COMPLETED,
                status=VerificationStatus(result["status"]),
                confidence_score=result.get("confidence"),
                evidence=result.get("evidence"),
                notes=result.get("notes"),
            )

        # Verify all receipts in parallel
        if not supporting_invoices:
            return []

        tasks = [verify_one_receipt(receipt) for receipt in supporting_invoices]
        verifications = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions
        return [v for v in verifications if isinstance(v, Verification)]

    def _verify_receipt_work_completed(self, supporting_invoices: List[Invoice]) -> List[Verification]:
        """Verify work was completed for receipts (sync wrapper)."""
        return asyncio.run(self._verify_receipt_work_completed_async(supporting_invoices))

    def _identify_unmatched_supporting_invoices(
        self, parent_invoice: Invoice, supporting_invoices: List[Invoice]
    ) -> List[Verification]:
        """
        Identify supporting invoices that are not matched to any line item in the parent invoice.
        This helps flag potential issues like:
        - Supporting docs included by mistake
        - Supporting docs that total more than parent invoice
        - Missing line items in parent invoice
        """
        verifications = []

        # Collect all supporting invoice IDs that are referenced in line items
        matched_ids = set()
        for line_item in parent_invoice.line_items:
            matched_ids.update(line_item.matched_supporting_invoice_ids)

        logger.info(f"[VALIDATION] Checking for unmatched supporting invoices. Matched IDs: {matched_ids}")
        logger.info(f"[VALIDATION] Total supporting invoices: {len(supporting_invoices)}")

        # Find supporting invoices that aren't matched to any line item
        for supporting_invoice in supporting_invoices:
            if supporting_invoice.id and supporting_invoice.id not in matched_ids:
                # This invoice is unmatched - create a verification for it

                # Handle extraction_failed invoices which may have missing data
                if supporting_invoice.status == "extraction_failed":
                    vendor_str = supporting_invoice.vendor or "Unknown"
                    error_msg = (
                        f" (Extraction failed: {supporting_invoice.extraction_error})"
                        if supporting_invoice.extraction_error
                        else " (Extraction failed)"
                    )
                    evidence = f"Supporting invoice from {vendor_str} could not be parsed{error_msg}"
                else:
                    amount_str = (
                        f"${supporting_invoice.total_amount:.2f}"
                        if supporting_invoice.total_amount
                        else "Unknown amount"
                    )
                    if (
                        supporting_invoice.amount_due
                        and supporting_invoice.amount_due != 0.00
                        and supporting_invoice.amount_due != supporting_invoice.total_amount
                    ):
                        amount_str = (
                            f"${supporting_invoice.amount_due:.2f} (of ${supporting_invoice.total_amount:.2f} total)"
                        )
                    invoice_num = (
                        f"#{supporting_invoice.invoice_number}"
                        if supporting_invoice.invoice_number
                        else "No invoice number"
                    )
                    evidence = f"Supporting invoice from {supporting_invoice.vendor or 'Unknown'} ({invoice_num}, {amount_str}) is not matched to any line item in the parent invoice"

                verifications.append(
                    Verification(
                        id=str(uuid.uuid4()),
                        invoice_id=supporting_invoice.id,
                        type=VerificationType.UNMATCHED_SUPPORTING_INVOICE,
                        status=VerificationStatus.NEEDS_REVIEW,
                        confidence_score=1.0,  # We're certain it's unmatched
                        evidence=evidence,
                        notes="This invoice may need manual review to determine if it should be included or if a line item is missing from the parent invoice",
                    )
                )

        # If there are unmatched invoices, also calculate how much they total
        if verifications:
            unmatched_invoices = [inv for inv in supporting_invoices if inv.id not in matched_ids]
            unmatched_total = sum(inv.total_amount or 0 for inv in unmatched_invoices)
            parent_total = parent_invoice.total_amount or 0

            logger.info(
                f"[VALIDATION] Found {len(verifications)} unmatched supporting invoice(s) totaling ${unmatched_total:.2f} (parent total: ${parent_total:.2f})"
            )
            for inv in unmatched_invoices:
                # Convert document_path to URL
                pdf_url = "N/A"
                if inv.document_path:
                    # Extract relative path from absolute path
                    # e.g., /path/to/data/uploads/abc/extracted/doc.pdf -> /pdfs/abc/extracted/doc.pdf
                    from pathlib import Path

                    path = Path(inv.document_path)
                    # Find the uploads directory part
                    parts = path.parts
                    try:
                        uploads_idx = parts.index("uploads")
                        relative_parts = parts[uploads_idx + 1 :]  # Everything after 'uploads'
                        pdf_url = f"/pdfs/{'/'.join(relative_parts)}"
                    except ValueError:
                        pdf_url = inv.document_path

                logger.info(
                    f"[VALIDATION]   - {inv.vendor or 'Unknown'} #{inv.invoice_number or 'N/A'} ${inv.total_amount or 0:.2f} | PDF: {pdf_url}"
                )

        return verifications

    def _parse_verification_response(self, response: str) -> Dict:
        """Parse JSON response from LLM."""
        import json

        try:
            # Try to find JSON in the response
            start_idx = response.find("{")
            end_idx = response.rfind("}") + 1
            if start_idx != -1 and end_idx > start_idx:
                json_str = response[start_idx:end_idx]
                return json.loads(json_str)
        except Exception as e:
            logger.info(f"Error parsing verification response: {e}")
            logger.info(f"Response was: {response}")

        # Fallback
        return {
            "status": "needs_review",
            "confidence": 0.3,
            "evidence": "Failed to parse LLM response",
            "notes": response[:200],
        }
