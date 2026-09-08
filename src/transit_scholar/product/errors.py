"""Typed Product-layer failures used at the API boundary."""


class ProductError(Exception):
    """Base class for expected Product/application failures."""


class ProductNotFoundError(ProductError, ValueError):
    """A requested product resource does not exist."""


class ProductConflictError(ProductError, ValueError):
    """A requested operation conflicts with current product state."""


class ProductValidationError(ProductError, ValueError):
    """A product command or value is invalid."""


class ProviderUnavailableError(ProductError):
    """A configured external provider cannot service the request."""


class ProductPayloadTooLargeError(ProductError):
    """A product input exceeds its configured size limit."""
