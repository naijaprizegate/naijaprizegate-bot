# ======================================================
# services/finance/exceptions.py
# ======================================================

"""
Custom exceptions for the NaijaPrize Finance subsystem.

Each exception represents a specific business rule violation.

Business logic should raise these exceptions instead of
returning generic True/False values.
"""

# ----------------------------
# Base Exception
# ----------------------------
class FinanceError(Exception):
    """
    Base class for all finance-related exceptions.
    """
    pass


# ------------------------
# Wallet Exception
# -----------------------
class WalletError(FinanceError):
    """Base wallet exception."""
    pass


class InsufficientWalletBalanceError(WalletError):
    """Raised when available wallet balance is insufficient."""
    pass


class WalletNotFoundError(WalletError):
    """Raised when a referral wallet cannot be located."""
    pass


class WalletAlreadyExistsError(WalletError):
    """Raised when a referral wallet already exists."""
    pass


class ReferralNotFoundError(FinanceError):
    """Raised when the referral relationship cannot be found."""
    pass

class InvalidWalletAmountError(WalletError):
    """Raised when a wallet transaction amount is invalid."""
    pass


# ----------------------------
# Premium Point Exceptions
# ----------------------------
class PremiumPointsError(FinanceError):
    """Base premium points exception."""
    pass


class InsufficientPremiumPointsError(PremiumPointsError):
    """Raised when available Premium Points are insufficient."""
    pass


# -----------------------------------
# Commission Exceptions
# ----------------------------------
class CommissionError(FinanceError):
    """Base commission exception."""
    pass


class PaymentNotQualifiedError(CommissionError):
    """Raised when a payment does not qualify for commission."""
    pass


class CommissionAlreadyCreditedError(CommissionError):
    """Raised when commission has already been credited."""
    pass


# --------------------------------
# Withdrawal Exceptions
# ----------------------------------
class WithdrawalError(FinanceError):
    """Base withdrawal exception."""
    pass


class WithdrawalLimitError(WithdrawalError):
    """Raised when requested withdrawal exceeds the allowed limit."""
    pass


class DuplicateWithdrawalRequestError(WithdrawalError):
    """Raised when a pending withdrawal request already exists."""
    pass


class InvalidWithdrawalAmountError(WithdrawalError):
    """Raised when withdrawal amount is invalid."""
    pass


# ---------------------------------
# Admin Exceptions
# --------------------------------
class WithdrawalApprovalError(WithdrawalError):
    """Raised when a withdrawal cannot be approved."""
    pass


class WithdrawalRejectionError(WithdrawalError):
    """Raised when a withdrawal cannot be rejected."""
    pass
