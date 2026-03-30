from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    PARENT = "parent"
    SUPPORTING = "supporting"
    OTHER = "other"


class VerificationType(str, Enum):
    WORK_REQUESTED = "work_requested"
    TOTALS_MATCH = "totals_match"
    CORRECT_RECIPIENT = "correct_recipient"
    HAS_SUPPORTING_DETAILS = "has_supporting_details"
    REASONABLE_COST = "reasonable_cost"
    WORK_COMPLETED = "work_completed"
    RECEIPT_EXISTS = "receipt_exists"
    UNMATCHED_SUPPORTING_INVOICE = "unmatched_supporting_invoice"


class VerificationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NEEDS_REVIEW = "needs_review"
    PENDING = "pending"


class LineItem(BaseModel):
    id: Optional[str] = None
    description: Optional[str] = ""
    vendor: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    total: Optional[float] = None
    category: Optional[str] = None
    matched_supporting_invoice_ids: List[str] = Field(default_factory=list)
    match_needs_review: bool = False


class Invoice(BaseModel):
    id: Optional[str] = None
    type: DocumentType
    batch_id: Optional[str] = None
    document_path: Optional[str] = None
    pdf_url: Optional[str] = None
    pages: List[int] = Field(default_factory=list)
    vendor: Optional[str] = None
    invoice_number: Optional[str] = None
    total_amount: Optional[float] = None
    amount_due: Optional[float] = None
    date: Optional[str] = None
    recipient: Optional[str] = None
    line_items: List[LineItem] = Field(default_factory=list)
    extracted_text: Optional[str] = None
    status: Literal["pending", "processed", "matched", "extraction_failed"] = "pending"
    line_items_warning: Optional[str] = None
    extraction_error: Optional[str] = None


class Verification(BaseModel):
    id: Optional[str] = None
    invoice_id: Optional[str] = None
    type: VerificationType
    status: VerificationStatus
    confidence_score: Optional[float] = None
    evidence: Optional[str] = None
    human_reviewed: bool = False
    notes: Optional[str] = None


class PDFPage(BaseModel):
    page_num: int
    thumbnail_url: str
    text_preview: str
    full_text: str


class DocumentBoundary(BaseModel):
    pages: List[int]
    type: DocumentType
    confidence: float
    text_preview: str


class InvoiceAnalysis(BaseModel):
    invoice_id: Optional[str] = None
    risk_level: Literal["low", "medium", "high"]
    confidence_score: int  # 0-100
    missing_elements: List[str] = Field(default_factory=list)
    fraud_red_flags: List[str] = Field(default_factory=list)
    quality_issues: List[str] = Field(default_factory=list)
    line_item_concerns: List[str] = Field(default_factory=list)
    key_concerns: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    detailed_analysis: str
    analyzed_at: datetime = Field(default_factory=datetime.now)


class ValidationDiscrepancy(BaseModel):
    field: str
    expected: str
    actual: str
    severity: Literal["high", "medium", "low"]
