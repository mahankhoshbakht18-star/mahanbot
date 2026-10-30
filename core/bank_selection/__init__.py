"""
core/bank_selection — Unified Bank Selection Architecture
Exports Orchestrator, Matcher, CatalogParser, QuotaClassifier, and PostbackSynchronizer.
"""
from core.bank_matcher import (
    PersianBankMatcher,
    BankOptionInfo,
    KNOWN_BANK_CODES,
    BANK_CANONICAL_TO_CODE,
)
from core.bank_selection.catalog_parser import BankCatalogParser
from core.bank_selection.quota_classifier import BankQuotaClassifier, QuotaStatus
from core.bank_selection.postback_sync import PostbackSynchronizer
from core.bank_selection.orchestrator import BankSelectionOrchestrator

__all__ = [
    "BankSelectionOrchestrator",
    "PersianBankMatcher",
    "BankCatalogParser",
    "BankQuotaClassifier",
    "QuotaStatus",
    "PostbackSynchronizer",
    "BankOptionInfo",
    "KNOWN_BANK_CODES",
    "BANK_CANONICAL_TO_CODE",
]
